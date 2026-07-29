const fs = require('node:fs');
const path = require('node:path');
require('node:dns').setDefaultResultOrder('ipv4first');
const { openDb } = require('./db');
const { renderTemplate } = require('./template');
const {
  updateHeartbeat,
  isDisconnectRequested,
  clearDisconnectRequest,
  markDisconnected,
  clearQrCode,
} = require('./heartbeat');
const {
  ACK_TIMEOUT_MS,
  getSettings,
  markSendStarted,
  markSendAttempted,
  markSent,
  markFailed,
  recoverStuckSends,
  sweepAckTimeouts,
  getNextPendingContact,
  countSentToday,
  getAttachments,
  isHalted,
  getHalt,
} = require('./queue');
const { applyMigrations } = require('./migrate');
const { evaluateSendWindow } = require('./sendWindow');
const { createAckTracker } = require('./ackHandler');
const {
  connect,
  registerReplyListener,
  registerAckListener,
  checkOnWhatsApp,
  sendMessage,
  sendMediaMessage,
} = require('./baileys');

const DB_PATH = path.join(__dirname, '..', 'data', 'silkroad.db');
const AUTH_DIR = path.join(__dirname, '..', 'auth');
const SCHEMA_PATH = path.join(__dirname, '..', 'schema.sql');

const IDLE_POLL_MS = 5000;
const RECONNECT_RETRY_MS = 5000;
// WhatsApp answers repeated registration attempts from the same host with
// 405 Connection Failure and gets stricter the harder you retry. Back off
// exponentially instead of hammering it every 5s.
const RECONNECT_MAX_RETRY_MS = 5 * 60 * 1000;
const HEARTBEAT_INTERVAL_MS = 30000;
const ERROR_RETRY_MS = 5000;

function reconnectDelayMs(consecutiveFailures) {
  return Math.min(RECONNECT_RETRY_MS * 2 ** (consecutiveFailures - 1), RECONNECT_MAX_RETRY_MS);
}

/**
 * Pick the retry policy for a failed connect().
 *
 * An unscanned QR is not a failure of ours. Baileys rotates through a fixed
 * number of codes (~2 minutes) and then closes the socket; if we treat that
 * like a refused connection, the backoff climbs to 5 minutes and the operator
 * stares at a QR-less page for most of every cycle. So: come straight back with
 * a fresh code, and drop the failure count — a QR is proof WhatsApp is still
 * willing to talk to us.
 *
 * A close with no QR at all is the 405 registration refusal, which gets worse
 * the harder you retry. That one keeps the exponential backoff.
 */
function connectRetryPolicy(err, consecutiveFailures) {
  if (err && err.restartRequired) {
    // The scan succeeded and Baileys wants the reconnect that completes login.
    // Making the operator wait for that would be absurd.
    return { failures: 0, waitMs: 0, qrExpired: false, pairingRestart: true };
  }
  if (err && err.qrEmitted) {
    return { failures: 0, waitMs: RECONNECT_RETRY_MS, qrExpired: true };
  }
  const failures = consecutiveFailures + 1;
  return { failures, waitMs: reconnectDelayMs(failures), qrExpired: false };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Sleep, but wake early once shouldWake() turns true.
 *
 * The reconnect backoff can grow to 5 minutes; without this an operator
 * pressing Disconnect would sit there waiting out the whole sleep.
 */
async function sleepUntil(ms, shouldWake, stepMs = 1000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (shouldWake && shouldWake()) return true;
    await sleep(Math.min(stepMs, deadline - Date.now()));
  }
  return Boolean(shouldWake && shouldWake());
}

/**
 * Act on an operator disconnect request.
 *
 * The request flag is consumed ONLY here, where it is actually acted on.
 * Clearing it before checking for a socket silently swallowed every
 * disconnect requested while the worker was not connected — which is exactly
 * the state it sits in during connection-failure backoff.
 *
 * With no socket there is nothing to log out of, but the operator's intent is
 * the same: drop the session and come back with a fresh QR. So we still clear
 * the stored session and exit cleanly for the supervisor to respawn.
 */
async function handleDisconnectRequest(db, sock, options = {}) {
  if (!isDisconnectRequested(db)) return { acted: false };

  const authDir = options.authDir || AUTH_DIR;
  const log = options.log || console;
  const exit = options.exit || ((code) => process.exit(code));
  const removeAuth =
    options.removeAuth || ((dir) => fs.rmSync(dir, { recursive: true, force: true }));

  clearDisconnectRequest(db);
  markDisconnected(db);
  clearQrCode(db);

  if (sock) {
    log.log('Disconnect requested from the app — logging out of WhatsApp.');
    try {
      await sock.logout();
    } catch (err) {
      log.error('Error during logout:', err.message);
    }
  } else {
    log.log(
      'Disconnect requested from the app while not connected — clearing the stored session anyway.'
    );
  }

  removeAuth(authDir);
  log.log('Disconnected. Restarting to generate a fresh QR code.');
  exit(0);
  return { acted: true, hadSocket: Boolean(sock) };
}

async function processContact(db, sock, contact, settings) {
  // Claim the row before anything can touch the socket: a crash mid-send then
  // leaves status='sending' with no delivery_state, which startup recovery
  // turns into needs_review instead of silently re-sending.
  markSendStarted(db, contact.id);

  try {
    // Inside the try: malformed extra_fields JSON or a deleted program must
    // mark the contact failed, not leave it stuck in 'sending'.
    const extraFields = JSON.parse(contact.extra_fields || '{}');
    const program = db
      .prepare('SELECT template_text FROM programs WHERE id = ?')
      .get(contact.program_id);
    if (!program) {
      throw new Error(`Program ${contact.program_id} no longer exists`);
    }
    const attachments = getAttachments(db, contact.program_id);
    const message = renderTemplate(program.template_text, { name: contact.name, ...extraFields });

    if (settings.dry_run) {
      const attachmentNote = attachments.length
        ? ` [with attachments: ${attachments.map((a) => a.file_name).join(', ')}]`
        : '';
      console.log(`[DRY RUN] Would send to ${contact.phone}: ${message}${attachmentNote}`);
      markSent(db, contact.id, message);
      return;
    }

    const exists = await checkOnWhatsApp(sock, contact.phone);
    if (!exists) {
      throw new Error('Number not registered on WhatsApp');
    }

    // sendMessage resolving only means the socket accepted the payload —
    // WhatsApp can still refuse it (e.g. ack error 463 on a restricted
    // account). Record the message id and wait for the ack instead of
    // claiming success here.
    let result;
    if (attachments.length === 0) {
      result = await sendMessage(sock, contact.phone, message);
    } else {
      result = await sendMediaMessage(sock, contact.phone, attachments[0], message);
      for (const attachment of attachments.slice(1)) {
        await sendMediaMessage(sock, contact.phone, attachment, undefined);
      }
    }

    const waMessageId = result?.key?.id;
    if (!waMessageId) {
      console.warn(`No message id returned for ${contact.phone} — relying on the ack timeout.`);
    }
    markSendAttempted(db, contact.id, waMessageId, message);
  } catch (err) {
    markFailed(db, contact.id, err.message);
  }
}

async function runLoop(db, tracker = createAckTracker(db)) {
  const recovered = recoverStuckSends(db);
  if (recovered > 0) {
    console.log(`Marked ${recovered} interrupted send(s) as needs_review.`);
  }

  let sock = null;
  let haltLogged = false;
  let windowClosedLogged = null;
  let connectFailures = 0;

  while (true) {
    try {
      updateHeartbeat(db);

      // Acks arrive asynchronously; anything still unconfirmed after
      // ACK_TIMEOUT_MS never made it and must stop looking "sent".
      const timedOut = sweepAckTimeouts(db, ACK_TIMEOUT_MS);
      if (timedOut.length > 0) {
        console.error(
          `No WhatsApp confirmation within ${ACK_TIMEOUT_MS / 1000}s for ${timedOut.length} ` +
            `message(s): ${timedOut.map((row) => row.phone).join(', ')} — marked failed.`
        );
        tracker.registerTimeouts(timedOut.length);
      }

      // Honoured whether or not a socket exists (see handleDisconnectRequest).
      await handleDisconnectRequest(db, sock);

      const settings = getSettings(db);

      if (sock && sock.connectionLost) {
        console.log('Reconnecting after lost connection...');
        try {
          sock.end?.();
        } catch {}
        sock = null;
      }

      if (!settings.dry_run && !sock) {
        console.log('dry_run turned off — connecting to WhatsApp...');
        try {
          sock = await connect(AUTH_DIR, db);
          registerReplyListener(sock, db);
          registerAckListener(sock, db, tracker);
          connectFailures = 0;
          console.log('Connected to WhatsApp.');
        } catch (err) {
          const policy = connectRetryPolicy(err, connectFailures);
          connectFailures = policy.failures;
          if (policy.pairingRestart) {
            console.log(`${err.message} — reconnecting now to finish logging in.`);
          } else if (policy.qrExpired) {
            console.log(
              `${err.message} — generating a fresh QR in ${Math.round(policy.waitMs / 1000)}s.`
            );
          } else {
            console.error(
              `Failed to connect to WhatsApp (attempt ${connectFailures}): ${err.message} — ` +
                `retrying in ${Math.round(policy.waitMs / 1000)}s.`
            );
          }
          // Wake early if the operator asks to disconnect mid-backoff.
          await sleepUntil(policy.waitMs, () => isDisconnectRequested(db));
          continue;
        }
      }

      // Halted: keep the loop (and the heartbeat, and the ack listeners)
      // alive, but send nothing until the app clears the halt.
      if (isHalted(db)) {
        if (!haltLogged) {
          console.error(`Sending is HALTED: ${getHalt(db).halt_reason}`);
          haltLogged = true;
        }
        await sleep(IDLE_POLL_MS);
        continue;
      }
      haltLogged = false;

      // Outside the sending window: idle without touching the queue, so
      // nothing is consumed or marked until the window opens again.
      const sendWindow = evaluateSendWindow(settings);
      if (!sendWindow.allowed) {
        if (windowClosedLogged !== sendWindow.reason) {
          console.log(sendWindow.reason);
          windowClosedLogged = sendWindow.reason;
        }
        await sleep(IDLE_POLL_MS);
        continue;
      }
      windowClosedLogged = null;

      if (settings.daily_cap !== null && countSentToday(db) >= settings.daily_cap) {
        await sleep(IDLE_POLL_MS);
        continue;
      }

      const contact = getNextPendingContact(db);

      if (!contact) {
        await sleep(IDLE_POLL_MS);
        continue;
      }

      await processContact(db, sock, contact, settings);

      const jitter = settings.jitter_seconds > 0 ? Math.random() * settings.jitter_seconds : 0;
      await sleep((settings.delay_seconds + jitter) * 1000);
    } catch (err) {
      console.error('Worker loop error:', err);
      await sleep(ERROR_RETRY_MS);
      continue;
    }
  }
}

async function main() {
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  const db = openDb(DB_PATH);
  db.exec(fs.readFileSync(SCHEMA_PATH, 'utf8'));
  // schema.sql only creates missing tables; existing databases need the
  // additive columns added explicitly (mirrored in app/db.py).
  const migrated = applyMigrations(db);
  if (migrated.length > 0) {
    console.log(`Applied schema migration: ${migrated.join(', ')}`);
  }

  updateHeartbeat(db);

  // Keep the heartbeat fresh even while the send loop sleeps through long
  // delays (the UI treats heartbeats older than 120s as a dead worker).
  setInterval(() => {
    try {
      updateHeartbeat(db);
    } catch (err) {
      console.error('Heartbeat update failed:', err.message);
    }
  }, HEARTBEAT_INTERVAL_MS);

  await runLoop(db);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('Worker crashed:', err);
    process.exit(1);
  });
}

module.exports = {
  processContact,
  runLoop,
  reconnectDelayMs,
  connectRetryPolicy,
  sleepUntil,
  handleDisconnectRequest,
};
