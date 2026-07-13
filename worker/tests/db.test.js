const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { openDb } = require('../db');

test('openDb sets WAL journal mode and busy_timeout', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'silkroad-test-'));
  const dbPath = path.join(dir, 'test.db');
  const db = openDb(dbPath);

  const journalMode = db.pragma('journal_mode', { simple: true });
  const busyTimeout = db.pragma('busy_timeout', { simple: true });

  assert.equal(journalMode, 'wal');
  assert.equal(busyTimeout, 5000);

  db.close();
  fs.rmSync(dir, { recursive: true, force: true });
});
