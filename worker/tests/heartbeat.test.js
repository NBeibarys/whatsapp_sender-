const test = require('node:test');
const assert = require('node:assert/strict');
const { makeTestDb } = require('./helpers');
const { updateHeartbeat, getHeartbeat, setQrCode, markConnected } = require('../heartbeat');

test('updateHeartbeat writes a recent timestamp', () => {
  const { db, cleanup } = makeTestDb();

  updateHeartbeat(db);
  const lastSeen = getHeartbeat(db);
  const diffMs = Date.now() - new Date(lastSeen).getTime();

  assert.ok(diffMs >= 0 && diffMs < 5000, `expected recent timestamp, got diff ${diffMs}ms`);
  cleanup();
});


test('markConnected clears any stale QR code', () => {
  const { db, cleanup } = makeTestDb();

  setQrCode(db, 'data:image/png;base64,old');
  markConnected(db);

  const row = db.prepare('SELECT connected, qr_code FROM worker_heartbeat WHERE id = 1').get();
  assert.equal(row.connected, 1);
  assert.equal(row.qr_code, null);
  cleanup();
});

test('setQrCode marks WhatsApp as disconnected while waiting for scan', () => {
  const { db, cleanup } = makeTestDb();

  markConnected(db);
  setQrCode(db, 'data:image/png;base64,new');

  const row = db.prepare('SELECT connected, qr_code FROM worker_heartbeat WHERE id = 1').get();
  assert.equal(row.connected, 0);
  assert.equal(row.qr_code, 'data:image/png;base64,new');
  cleanup();
});
