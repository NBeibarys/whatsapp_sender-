const test = require('node:test');
const assert = require('node:assert/strict');
const { makeTestDb } = require('./helpers');
const { updateHeartbeat, getHeartbeat } = require('../heartbeat');

test('updateHeartbeat writes a recent timestamp', () => {
  const { db, cleanup } = makeTestDb();

  updateHeartbeat(db);
  const lastSeen = getHeartbeat(db);
  const diffMs = Date.now() - new Date(lastSeen).getTime();

  assert.ok(diffMs >= 0 && diffMs < 5000, `expected recent timestamp, got diff ${diffMs}ms`);
  cleanup();
});
