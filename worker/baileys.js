const fs = require('node:fs');
const {
  default: makeWASocket,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  DisconnectReason,
} = require('@whiskeysockets/baileys');
const qrcodeTerminal = require('qrcode-terminal');
const QRCode = require('qrcode');
const { setQrCode, clearQrCode, updateHeartbeat, markConnected, markDisconnected } = require('./heartbeat');
const { markReplied, recordReply } = require('./queue');

/**
 * The socket closed before login. The flags are the whole point of this error:
 * they tell the caller which of three very different things happened, and
 * therefore how fast to reconnect (see connectRetryPolicy in worker/index.js).
 */
function preLoginCloseError(qrEmitted, restartRequired = false) {
  let message;
  if (restartRequired) {
    message = 'WhatsApp accepted the pairing and asked us to reconnect';
  } else if (qrEmitted) {
    message = 'QR code expired without being scanned';
  } else {
    message =
      'Connection closed before login and before any QR code was issued ' +
      '(WhatsApp refused the connection)';
  }
  const err = new Error(message);
  err.qrEmitted = qrEmitted;
  err.restartRequired = restartRequired;
  return err;
}

async function connect(authDir, db) {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const { version, isLatest } = await fetchLatestBaileysVersion();
  console.log(`Using WhatsApp Web protocol version ${version.join('.')}, isLatest: ${isLatest}`);
  // Deliberately no qrTimeout override. Baileys supports it, but WhatsApp hands
  // over all ~6 pairing refs in a single stanza and Baileys just paces them
  // (60s for the first, 20s each after). Stretching that would keep showing a
  // ref long after the server issued it — a code that scans to an error. The
  // fresh-QR guarantee comes from reconnecting fast instead (see index.js).
  const sock = makeWASocket({ auth: state, version });
  sock.ev.on('creds.update', saveCreds);

  let initialConnectionResolved = false;
  // Did WhatsApp actually hand us a pairing code this attempt? It decides the
  // caller's retry policy (see the pre-login close branch below).
  let qrEmitted = false;

  return new Promise((resolve, reject) => {
    let settled = false;
    const settleResolve = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const settleReject = (err) => {
      if (settled) return;
      settled = true;
      reject(err);
    };

    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        qrEmitted = true;
        console.log('Scan this QR code with WhatsApp (Linked Devices):');
        qrcodeTerminal.generate(qr, { small: true });
        updateHeartbeat(db);
        QRCode.toDataURL(qr)
          .then((dataUrl) => {
            if (!initialConnectionResolved) {
              setQrCode(db, dataUrl);
            }
          })
          .catch((err) => console.error('Failed to render QR code for the app UI:', err.message));
      }

      if (connection === 'open') {
        initialConnectionResolved = true;
        clearQrCode(db);
        markConnected(db);
        settleResolve(sock);
        return;
      }

      if (connection === 'close') {
        const shouldReconnect =
          lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;

        if (!initialConnectionResolved) {
          if (!shouldReconnect) {
            markDisconnected(db);
            fs.rmSync(authDir, { recursive: true, force: true });
            settleReject(
              new Error(
                'WhatsApp session was invalid (logged out before connecting). ' +
                  'Cleared the stale session; the next restart will show a fresh QR code.'
              )
            );
            return;
          }
          // Baileys closes the socket when QR attempts are exhausted (or on a
          // network error) before login. Reject so the caller's retry loop can
          // reconnect and generate a fresh QR — otherwise this Promise never
          // settles and the worker hangs forever showing a stale QR.
          //
          // Two very different failures land here and they need OPPOSITE retry
          // policies, so tell them apart on the error:
          //   qrEmitted true  — codes were shown and simply went unscanned. The
          //     operator is probably staring at the screen; reconnect at once.
          //   qrEmitted false — WhatsApp refused the connection before issuing
          //     any code (the 405 registration case). Hammering that is what
          //     deepens the block, so the caller backs off exponentially.
          //   restartRequired — the scan WORKED; Baileys pairs, then asks for a
          //     reconnect to log in with the new creds. Not a failure: no wait.
          //
          // The exhausted refs are dead, so the stored QR goes with them: a
          // code that cannot be scanned must never sit on screen looking live.
          // The replacement is seconds away and the UI says so meanwhile.
          clearQrCode(db);
          const restartRequired =
            lastDisconnect?.error?.output?.statusCode === DisconnectReason.restartRequired;
          settleReject(preLoginCloseError(qrEmitted, restartRequired));
          return;
        }

        if (!shouldReconnect) {
          // The linked device was removed (user unlinked it, or WhatsApp did).
          // The persisted creds are dead: keeping them would only produce a
          // doomed reconnect loop, so wipe them and exit cleanly (0) — the
          // supervisor respawns us and the fresh start goes straight to a QR.
          markDisconnected(db);
          clearQrCode(db);
          console.error(
            'WhatsApp device was unlinked (logged out). ' +
              'Clearing the stale session so a fresh QR can be generated.'
          );
          fs.rmSync(authDir, { recursive: true, force: true });
          process.exit(0);
        }

        // Post-login stream drop (network error, stream ack error, etc.).
        // Flag the socket so the run loop discards it and reconnects with
        // the persisted session — otherwise every send fails with
        // "Connection Closed" until the worker is manually restarted.
        markDisconnected(db);
        sock.connectionLost = true;
        console.error('WhatsApp connection lost — will reconnect.');
      }
    });
  });
}

/** Extract readable text from an inbound message (media has no text body). */
function replyBody(msg) {
  const message = msg && msg.message;
  if (!message) return '<media>';
  return message.conversation || message.extendedTextMessage?.text || '<media>';
}

function registerReplyListener(sock, db) {
  sock.ev.on('messages.upsert', ({ messages }) => {
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      const remoteJid = msg.key.remoteJid || '';
      if (!remoteJid.endsWith('@s.whatsapp.net')) continue;
      const phone = `+${remoteJid.replace('@s.whatsapp.net', '')}`;
      try {
        markReplied(db, phone);
        // Keep the actual text, not just the fact that they replied.
        recordReply(db, phone, replyBody(msg));
      } catch (err) {
        console.error('Failed to record reply from', phone, err.message);
      }
    }
  });
}

/**
 * Delivery truth. See worker/ackHandler.js for why 'messages.update' is the
 * only event we need: it carries server/delivery/read receipts for 1:1 chats
 * AND ack errors (status ERROR + messageStubParameters=[code]).
 */
function registerAckListener(sock, db, tracker) {
  sock.ev.on('messages.update', (updates) => {
    try {
      tracker.handleUpdates(updates);
    } catch (err) {
      console.error('Failed to process message ack update:', err.message);
    }
  });
}

async function checkOnWhatsApp(sock, phone) {
  const [result] = await sock.onWhatsApp(phone);
  return Boolean(result?.exists);
}

/** Returns the sent WAMessage so the caller can track its key.id. */
async function sendMessage(sock, phone, text) {
  const jid = `${phone.replace('+', '')}@s.whatsapp.net`;
  return sock.sendMessage(jid, { text });
}

async function sendMediaMessage(sock, phone, attachment, caption) {
  const jid = `${phone.replace('+', '')}@s.whatsapp.net`;
  const content =
    attachment.media_type === 'image'
      ? { image: { url: attachment.file_path }, caption }
      : { document: { url: attachment.file_path }, fileName: attachment.file_name, caption };
  return sock.sendMessage(jid, content);
}

module.exports = {
  connect,
  preLoginCloseError,
  registerReplyListener,
  registerAckListener,
  replyBody,
  checkOnWhatsApp,
  sendMessage,
  sendMediaMessage,
};
