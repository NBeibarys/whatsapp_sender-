const test = require('node:test');
const assert = require('node:assert/strict');
const { makeTestDb } = require('./helpers');
const { getSettings, markSending, markSent, markFailed, getNextPendingContact } = require('../queue');

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

test('getNextPendingContact prioritizes programs that have not sent yet', () => {
  const { db, cleanup } = makeTestDb();
  const progA = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const progB = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('B', 'Hi {{name}}')")
    .run().lastInsertRowid;

  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status, sent_at) VALUES (?, '+10000000001', 'SentA', 'sent', '2026-01-01T00:00:00.000Z')"
  ).run(progA);
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000002', 'PendingA', 'pending')"
  ).run(progA);
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000003', 'PendingB', 'pending')"
  ).run(progB);

  const next = getNextPendingContact(db);
  assert.equal(next.program_id, progB, 'program that has never sent should go first');
  cleanup();
});

test('getNextPendingContact picks the least-recently-served program next', () => {
  const { db, cleanup } = makeTestDb();
  const progA = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const progB = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('B', 'Hi {{name}}')")
    .run().lastInsertRowid;

  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status, sent_at) VALUES (?, '+10000000001', 'SentA', 'sent', '2026-01-01T00:00:00.000Z')"
  ).run(progA);
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status, sent_at) VALUES (?, '+10000000002', 'SentB', 'sent', '2026-01-02T00:00:00.000Z')"
  ).run(progB);
  // Insert PendingB before PendingA so that B's contact row gets the smaller
  // id and A's gets the larger id. This makes the test discriminate a
  // correct sent_at-based implementation from a naive `ORDER BY c.id ASC`
  // fallback: an id-only implementation would (wrongly) pick B here, while
  // the correct round-robin logic picks A (least-recently-served).
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000004', 'PendingB', 'pending')"
  ).run(progB);
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000003', 'PendingA', 'pending')"
  ).run(progA);

  const next = getNextPendingContact(db);
  assert.equal(next.program_id, progA, 'A sent least recently (2026-01-01) so goes before B (2026-01-02)');
  cleanup();
});

const { countSentToday } = require('../queue');

test('countSentToday counts only contacts sent today', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status, sent_at) VALUES (?, '+10000000001', 'Today1', 'sent', datetime('now'))"
  ).run(programId);
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status, sent_at) VALUES (?, '+10000000002', 'Today2', 'sent', datetime('now'))"
  ).run(programId);
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status, sent_at) VALUES (?, '+10000000003', 'Yesterday', 'sent', datetime('now', '-1 day'))"
  ).run(programId);

  assert.equal(countSentToday(db), 2);
  cleanup();
});

test('getNextPendingContact ignores paused programs', () => {
  const { db, cleanup } = makeTestDb();
  const progA = db
    .prepare("INSERT INTO programs (name, template_text, paused) VALUES ('A', 'Hi {{name}}', 1)")
    .run().lastInsertRowid;
  const progB = db
    .prepare("INSERT INTO programs (name, template_text, paused) VALUES ('B', 'Hi {{name}}', 0)")
    .run().lastInsertRowid;

  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000001', 'PendingA', 'pending')"
  ).run(progA);
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000002', 'PendingB', 'pending')"
  ).run(progB);

  const next = getNextPendingContact(db);
  assert.equal(next.program_id, progB, 'paused program A should be skipped');
  cleanup();
});

const { markReplied } = require('../queue');

test('markReplied stamps replied_at on a matching sent contact', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare(
      "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000001', 'Test', 'sent')"
    )
    .run(programId).lastInsertRowid;

  const changed = markReplied(db, '+10000000001');

  const row = db.prepare('SELECT replied_at FROM contacts WHERE id = ?').get(contactId);
  assert.equal(changed, 1);
  assert.ok(row.replied_at);
  cleanup();
});

test('markReplied does not overwrite an existing replied_at', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status, replied_at) VALUES (?, '+10000000002', 'Test2', 'sent', '2026-01-01T00:00:00.000Z')"
  ).run(programId);

  const changed = markReplied(db, '+10000000002');

  const row = db.prepare("SELECT replied_at FROM contacts WHERE phone = '+10000000002'").get();
  assert.equal(changed, 0);
  assert.equal(row.replied_at, '2026-01-01T00:00:00.000Z');
  cleanup();
});

test('markReplied ignores phones with no matching sent contact', () => {
  const { db, cleanup } = makeTestDb();
  const changed = markReplied(db, '+19999999999');
  assert.equal(changed, 0);
  cleanup();
});

const { getAttachments } = require('../queue');

test('getAttachments returns attachments for a program ordered by id', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  db.prepare(
    "INSERT INTO program_attachments (program_id, file_path, file_name, media_type) VALUES (?, 'media/1/a.png', 'a.png', 'image')"
  ).run(programId);
  db.prepare(
    "INSERT INTO program_attachments (program_id, file_path, file_name, media_type) VALUES (?, 'media/1/b.pdf', 'b.pdf', 'document')"
  ).run(programId);

  const attachments = getAttachments(db, programId);

  assert.equal(attachments.length, 2);
  assert.equal(attachments[0].file_name, 'a.png');
  assert.equal(attachments[1].file_name, 'b.pdf');
  cleanup();
});

test('getAttachments returns an empty array for a program with none', () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;

  const attachments = getAttachments(db, programId);

  assert.deepEqual(attachments, []);
  cleanup();
});
