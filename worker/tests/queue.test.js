const test = require('node:test');
const assert = require('node:assert/strict');
const { makeTestDb } = require('./helpers');
const { getSettings, markSending, markSent, markFailed } = require('../queue');

test('getSettings returns default settings row', () => {
  const { db, cleanup } = makeTestDb();

  const settings = getSettings(db);

  assert.equal(settings.delay_seconds, 60);
  assert.equal(settings.jitter_seconds, 0);
  assert.equal(settings.dry_run, 1);
  cleanup();
});

test('markSending sets status to sending', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name) VALUES (?, '+10000000001', 'Test')")
    .run(programId).lastInsertRowid;

  markSending(db, contactId);

  const row = db.prepare('SELECT status FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'sending');
  cleanup();
});

test('markSent sets status, rendered_message, and sent_at', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name) VALUES (?, '+10000000001', 'Test')")
    .run(programId).lastInsertRowid;

  markSent(db, contactId, 'Hi Test');

  const row = db.prepare('SELECT status, rendered_message, sent_at FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'sent');
  assert.equal(row.rendered_message, 'Hi Test');
  assert.ok(row.sent_at);
  cleanup();
});

test('markFailed sets status and error_message', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name) VALUES (?, '+10000000001', 'Test')")
    .run(programId).lastInsertRowid;

  markFailed(db, contactId, 'Number not registered on WhatsApp');

  const row = db.prepare('SELECT status, error_message FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'failed');
  assert.equal(row.error_message, 'Number not registered on WhatsApp');
  cleanup();
});

const { recoverStuckSends } = require('../queue');

test('recoverStuckSends marks sending rows as needs_review', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare(
      "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000001', 'Test', 'sending')"
    )
    .run(programId).lastInsertRowid;

  const changed = recoverStuckSends(db);

  const row = db.prepare('SELECT status FROM contacts WHERE id = ?').get(contactId);
  assert.equal(changed, 1);
  assert.equal(row.status, 'needs_review');
  cleanup();
});

test('recoverStuckSends leaves other statuses untouched', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000002', 'Test2', 'pending')"
  ).run(programId);

  const changed = recoverStuckSends(db);

  const row = db.prepare("SELECT status FROM contacts WHERE phone = '+10000000002'").get();
  assert.equal(changed, 0);
  assert.equal(row.status, 'pending');
  cleanup();
});
