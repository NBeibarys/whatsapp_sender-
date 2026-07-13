const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
} = require('@whiskeysockets/baileys');

async function connect(authDir) {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const sock = makeWASocket({ auth: state, printQRInTerminal: true });
  sock.ev.on('creds.update', saveCreds);

  let initialConnectionResolved = false;

  return new Promise((resolve, reject) => {
    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect } = update;

      if (connection === 'open') {
        initialConnectionResolved = true;
        resolve(sock);
        return;
      }

      if (connection === 'close') {
        const shouldReconnect =
          lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;

        if (!initialConnectionResolved) {
          if (!shouldReconnect) {
            reject(new Error('WhatsApp session logged out. Delete auth/ and re-scan QR.'));
          }
          return;
        }

        if (!shouldReconnect) {
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

async function checkOnWhatsApp(sock, phone) {
  const [result] = await sock.onWhatsApp(phone);
  return Boolean(result?.exists);
}

async function sendMessage(sock, phone, text) {
  const jid = `${phone.replace('+', '')}@s.whatsapp.net`;
  await sock.sendMessage(jid, { text });
}

module.exports = { connect, checkOnWhatsApp, sendMessage };
