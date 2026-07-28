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
const { markReplied } = require('./queue');

async function connect(authDir, db) {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const { version, isLatest } = await fetchLatestBaileysVersion();
  console.log(`Using WhatsApp Web protocol version ${version.join('.')}, isLatest: ${isLatest}`);
  const sock = makeWASocket({ auth: state, version });
  sock.ev.on('creds.update', saveCreds);

  let initialConnectionResolved = false;

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
          clearQrCode(db);
          settleReject(
            new Error(
              'Connection closed before login (QR expired or network error) — will retry with a fresh QR'
            )
          );
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

function registerReplyListener(sock, db) {
  sock.ev.on('messages.upsert', ({ messages }) => {
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      const remoteJid = msg.key.remoteJid || '';
      if (!remoteJid.endsWith('@s.whatsapp.net')) continue;
      const phone = `+${remoteJid.replace('@s.whatsapp.net', '')}`;
      try {
        markReplied(db, phone);
      } catch (err) {
        console.error('Failed to record reply from', phone, err.message);
      }
    }
  });
}

async function checkOnWhatsApp(sock, phone) {
  const [result] = await sock.onWhatsApp(phone);
  return Boolean(result?.exists);
}

async function sendMessage(sock, phone, text) {
  const jid = `${phone.replace('+', '')}@s.whatsapp.net`;
  await sock.sendMessage(jid, { text });
}

async function sendMediaMessage(sock, phone, attachment, caption) {
  const jid = `${phone.replace('+', '')}@s.whatsapp.net`;
  const content =
    attachment.media_type === 'image'
      ? { image: { url: attachment.file_path }, caption }
      : { document: { url: attachment.file_path }, fileName: attachment.file_name, caption };
  await sock.sendMessage(jid, content);
}

module.exports = { connect, registerReplyListener, checkOnWhatsApp, sendMessage, sendMediaMessage };
