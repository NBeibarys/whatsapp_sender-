function updateHeartbeat(db) {
  db.prepare('UPDATE worker_heartbeat SET last_seen = ? WHERE id = 1').run(
    new Date().toISOString()
  );
}

function getHeartbeat(db) {
  const row = db.prepare('SELECT last_seen FROM worker_heartbeat WHERE id = 1').get();
  return row ? row.last_seen : null;
}

function setQrCode(db, dataUrl) {
  db.prepare('UPDATE worker_heartbeat SET qr_code = ? WHERE id = 1').run(dataUrl);
}

function clearQrCode(db) {
  db.prepare('UPDATE worker_heartbeat SET qr_code = NULL WHERE id = 1').run();
}

function getQrCode(db) {
  const row = db.prepare('SELECT qr_code FROM worker_heartbeat WHERE id = 1').get();
  return row ? row.qr_code : null;
}

module.exports = { updateHeartbeat, getHeartbeat, setQrCode, clearQrCode, getQrCode };
