const test = require('node:test');
const assert = require('node:assert/strict');
const { makeTestDb } = require('./helpers');
const { getSettings } = require('../queue');

test('getSettings returns default settings row', () => {
  const { db, cleanup } = makeTestDb();

  const settings = getSettings(db);

  assert.equal(settings.delay_seconds, 60);
  assert.equal(settings.jitter_seconds, 0);
  assert.equal(settings.dry_run, 1);
  cleanup();
});
