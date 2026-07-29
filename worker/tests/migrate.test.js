const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { openDb } = require('../db');
const { applyMigrations } = require('../migrate');
const { makeTestDb } = require('./helpers');

/** The pre-migration schema, exactly as it shipped before delivery tracking. */
const LEGACY_SCHEMA = `
CREATE TABLE programs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  template_text TEXT NOT NULL,
  paused INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  program_id INTEGER NOT NULL REFERENCES programs(id),
  phone TEXT NOT NULL,
  name TEXT NOT NULL,
  extra_fields TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','sending','sent','failed','needs_review')),
  rendered_message TEXT,
  sent_at TEXT,
  error_message TEXT,
  replied_at TEXT,
  UNIQUE(program_id, phone)
);
CREATE TABLE worker_heartbeat (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_seen TEXT,
  qr_code TEXT,
  disconnect_requested INTEGER NOT NULL DEFAULT 0,
  connected INTEGER NOT NULL DEFAULT 0
);
INSERT INTO worker_heartbeat (id, last_seen) VALUES (1, NULL);
`;

function makeLegacyDb() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'silkroad-legacy-'));
  const db = openDb(path.join(dir, 'legacy.db'));
  db.exec(LEGACY_SCHEMA);
  return {
    db,
    cleanup: () => {
      db.close();
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

function columns(db, table) {
  return db.pragma(`table_info(${table})`).map((row) => row.name);
}

test('migrating a pre-migration database adds every new column and table', () => {
  const { db, cleanup } = makeLegacyDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi')")
    .run().lastInsertRowid;
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+1', 'Old', 'sent')"
  ).run(programId);

  const applied = applyMigrations(db);

  assert.ok(applied.length > 0);
  const contactColumns = columns(db, 'contacts');
  ['wa_message_id', 'delivery_state', 'ack_error', 'delivered_at', 'read_at'].forEach((c) => {
    assert.ok(contactColumns.includes(c), `contacts.${c} missing`);
  });
  const heartbeatColumns = columns(db, 'worker_heartbeat');
  ['halted_at', 'halt_reason', 'disconnect_requested_at'].forEach((c) => {
    assert.ok(heartbeatColumns.includes(c), `worker_heartbeat.${c} missing`);
  });
  assert.ok(db.prepare("SELECT 1 FROM sqlite_master WHERE name = 'replies'").get());
  // Existing data survives untouched.
  assert.equal(db.prepare("SELECT status FROM contacts WHERE phone = '+1'").get().status, 'sent');
  cleanup();
});

test('applyMigrations is idempotent — running it twice changes nothing the second time', () => {
  const { db, cleanup } = makeLegacyDb();

  const first = applyMigrations(db);
  const second = applyMigrations(db);

  assert.ok(first.length > 0);
  assert.deepEqual(second, []);
  cleanup();
});

test('applyMigrations is a no-op on a database created from the current schema.sql', () => {
  const { db, cleanup } = makeTestDb();

  assert.deepEqual(applyMigrations(db), []);
  cleanup();
});
