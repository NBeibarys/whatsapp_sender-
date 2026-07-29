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
  db.prepare('UPDATE worker_heartbeat SET qr_code = ?, connected = 0 WHERE id = 1').run(dataUrl);
}

function clearQrCode(db) {
  db.prepare('UPDATE worker_heartbeat SET qr_code = NULL WHERE id = 1').run();
}

function getQrCode(db) {
  const row = db.prepare('SELECT qr_code FROM worker_heartbeat WHERE id = 1').get();
  return row ? row.qr_code : null;
}

function markConnected(db) {
  db.prepare('UPDATE worker_heartbeat SET connected = 1, qr_code = NULL WHERE id = 1').run();
}

function markDisconnected(db) {
  db.prepare('UPDATE worker_heartbeat SET connected = 0 WHERE id = 1').run();
}

/**
 * Record an operator disconnect request, stamped with the time it was made.
 *
 * The stamp is not bookkeeping — it is the expiry. A request with no stamp (or
 * an old one) is a request that outlived the click behind it, and acting on it
 * destroys a session nobody is currently asking to destroy.
 */
function requestDisconnect(db, requestedAt = new Date().toISOString()) {
  db.prepare(
    'UPDATE worker_heartbeat SET disconnect_requested = 1, disconnect_requested_at = ? WHERE id = 1'
  ).run(requestedAt);
}

function getDisconnectRequest(db) {
  const row = db
    .prepare(
      'SELECT disconnect_requested, disconnect_requested_at FROM worker_heartbeat WHERE id = 1'
    )
    .get();
  return {
    requested: Boolean(row && row.disconnect_requested),
    requestedAt: row ? row.disconnect_requested_at : null,
  };
}

function isDisconnectRequested(db) {
  return getDisconnectRequest(db).requested;
}

function clearDisconnectRequest(db) {
  db.prepare(
    'UPDATE worker_heartbeat SET disconnect_requested = 0, disconnect_requested_at = NULL WHERE id = 1'
  ).run();
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
  getDisconnectRequest,
  isDisconnectRequested,
  clearDisconnectRequest,
};
