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

function markConnected(db) {
  db.prepare('UPDATE worker_heartbeat SET connected = 1 WHERE id = 1').run();
}

function markDisconnected(db) {
  db.prepare('UPDATE worker_heartbeat SET connected = 0 WHERE id = 1').run();
}

function requestDisconnect(db) {
  db.prepare('UPDATE worker_heartbeat SET disconnect_requested = 1 WHERE id = 1').run();
}

function isDisconnectRequested(db) {
  const row = db.prepare('SELECT disconnect_requested FROM worker_heartbeat WHERE id = 1').get();
  return Boolean(row && row.disconnect_requested);
}

function clearDisconnectRequest(db) {
  db.prepare('UPDATE worker_heartbeat SET disconnect_requested = 0 WHERE id = 1').run();
}

module.exports = {
  updateHeartbeat,
  getHeartbeat,
  setQrCode,
  clearQrCode,
  getQrCode,
  markConnected,
  markDisconnected,
  requestDisconnect,
  isDisconnectRequested,
  clearDisconnectRequest,
};
