const fs = require('node:fs');
const path = require('node:path');
require('node:dns').setDefaultResultOrder('ipv4first');
const { openDb } = require('./db');
const { renderTemplate } = require('./template');
const { updateHeartbeat, isDisconnectRequested, clearDisconnectRequest, markDisconnected } = require('./heartbeat');
const {
  getSettings,
  markSending,
  markSent,
  markFailed,
  recoverStuckSends,
  getNextPendingContact,
  countSentToday,
} = require('./queue');
const { connect, registerReplyListener, checkOnWhatsApp, sendMessage } = require('./baileys');

const DB_PATH = path.join(__dirname, '..', 'data', 'silkroad.db');
const AUTH_DIR = path.join(__dirname, '..', 'auth');
const SCHEMA_PATH = path.join(__dirname, '..', 'schema.sql');

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function processContact(db, sock, contact, settings) {
  markSending(db, contact.id);
  const extraFields = JSON.parse(contact.extra_fields || '{}');
  const program = db
    .prepare('SELECT template_text FROM programs WHERE id = ?')
    .get(contact.program_id);

  try {
    const message = renderTemplate(program.template_text, { name: contact.name, ...extraFields });

    if (settings.dry_run) {
      console.log(`[DRY RUN] Would send to ${contact.phone}: ${message}`);
    } else {
      const exists = await checkOnWhatsApp(sock, contact.phone);
      if (!exists) {
        throw new Error('Number not registered on WhatsApp');
      }
      await sendMessage(sock, contact.phone, message);
    }
    markSent(db, contact.id, message);
  } catch (err) {
    markFailed(db, contact.id, err.message);
  }
}

async function runLoop(db, sock) {
  const recovered = recoverStuckSends(db);
  if (recovered > 0) {
    console.log(`Marked ${recovered} interrupted send(s) as needs_review.`);
  }

  while (true) {
    updateHeartbeat(db);

    if (isDisconnectRequested(db)) {
      clearDisconnectRequest(db);
      if (sock) {
        console.log('Disconnect requested from the app — logging out of WhatsApp.');
        markDisconnected(db);
        try {
          await sock.logout();
        } catch (err) {
          console.error('Error during logout:', err.message);
        }
        fs.rmSync(AUTH_DIR, { recursive: true, force: true });
        console.log('Disconnected. Restarting to generate a fresh QR code.');
        process.exit(0);
      }
    }

    const settings = getSettings(db);

    if (settings.daily_cap !== null && countSentToday(db) >= settings.daily_cap) {
      await sleep(5000);
      continue;
    }

    const contact = getNextPendingContact(db);

    if (!contact) {
      await sleep(5000);
      continue;
    }

    await processContact(db, sock, contact, settings);

    const jitter = settings.jitter_seconds > 0 ? Math.random() * settings.jitter_seconds : 0;
    await sleep((settings.delay_seconds + jitter) * 1000);
  }
}

async function main() {
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  const db = openDb(DB_PATH);
  db.exec(fs.readFileSync(SCHEMA_PATH, 'utf8'));

  updateHeartbeat(db);

  const settings = getSettings(db);
  const sock = settings.dry_run ? null : await connect(AUTH_DIR, db);
  if (sock) {
    registerReplyListener(sock, db);
  }

  await runLoop(db, sock);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('Worker crashed:', err);
    process.exit(1);
  });
}

module.exports = { processContact };
