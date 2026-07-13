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
    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        console.log('Scan this QR code with WhatsApp (Linked Devices):');
        qrcodeTerminal.generate(qr, { small: true });
        updateHeartbeat(db);
        QRCode.toDataURL(qr)
          .then((dataUrl) => setQrCode(db, dataUrl))
          .catch((err) => console.error('Failed to render QR code for the app UI:', err.message));
      }

      if (connection === 'open') {
        initialConnectionResolved = true;
        clearQrCode(db);
        markConnected(db);
        resolve(sock);
        return;
      }

      if (connection === 'close') {
        const shouldReconnect =
          lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;

        if (!initialConnectionResolved) {
          if (!shouldReconnect) {
            markDisconnected(db);
            fs.rmSync(authDir, { recursive: true, force: true });
            reject(
              new Error(
                'WhatsApp session was invalid (logged out before connecting). ' +
                  'Cleared the stale session; the next restart will show a fresh QR code.'
              )
            );
          }
          return;
        }

        if (!shouldReconnect) {
          markDisconnected(db);
          console.error(
            'WhatsApp session was logged out after a successful connection. ' +
              'The worker will now exit; pm2 will restart it, which will require ' +
              'a fresh QR scan (delete the auth/ directory and re-run to get a new QR code, ' +
              'or check pm2 logs for the QR code on the next restart attempt).'
          );
          process.exit(1);
        }
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
