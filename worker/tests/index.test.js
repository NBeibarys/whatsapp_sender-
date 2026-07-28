const test = require('node:test');
const assert = require('node:assert/strict');
const { makeTestDb } = require('./helpers');
const { processContact } = require('../index');

test('processContact marks contact as sent in dry_run mode with correct rendered_message', async () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name, extra_fields) VALUES (?, '+10000000001', 'Test', '{}')")
    .run(programId).lastInsertRowid;
  const contact = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);

  await processContact(db, null, contact, { dry_run: 1, delay_seconds: 60, jitter_seconds: 0 });

  const row = db.prepare('SELECT status, rendered_message FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'sent');
  assert.equal(row.rendered_message, 'Hi Test');
  cleanup();
});

test('processContact marks contact as failed (not crashed) when template field is missing', async () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}, deadline for {{program}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name, extra_fields) VALUES (?, '+10000000002', 'Test', '{}')")
    .run(programId).lastInsertRowid;
  const contact = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);

  await processContact(db, null, contact, { dry_run: 1, delay_seconds: 60, jitter_seconds: 0 });

  const row = db.prepare('SELECT status, error_message FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'failed');
  assert.match(row.error_message, /Missing template field: program/);
  cleanup();
});

test('processContact marks contact as failed (not stuck sending) on malformed extra_fields JSON', async () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name, extra_fields) VALUES (?, '+10000000004', 'Test', 'not-json{')")
    .run(programId).lastInsertRowid;
  const contact = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);

  await processContact(db, null, contact, { dry_run: 1, delay_seconds: 60, jitter_seconds: 0 });

  const row = db.prepare('SELECT status, error_message FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'failed');
  assert.ok(row.error_message, 'expected an error_message to be recorded');
  cleanup();
});

test('processContact marks contact as failed (not stuck sending) when its program was deleted', async () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name, extra_fields) VALUES (?, '+10000000005', 'Test', '{}')")
    .run(programId).lastInsertRowid;
  const contact = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);

  // Delete the program out from under the queued contact (FKs off so the
  // contact row survives, mimicking a race between UI delete and the worker).
  db.pragma('foreign_keys = OFF');
  db.prepare('DELETE FROM programs WHERE id = ?').run(programId);

  await processContact(db, null, contact, { dry_run: 1, delay_seconds: 60, jitter_seconds: 0 });

  const row = db.prepare('SELECT status, error_message FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'failed');
  assert.match(row.error_message, /no longer exists/);
  cleanup();
});

test('processContact logs attachment filenames in dry_run mode when the program has attachments', async () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  db.prepare(
    "INSERT INTO program_attachments (program_id, file_path, file_name, media_type) VALUES (?, 'media/1/a.png', 'a.png', 'image')"
  ).run(programId);
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name, extra_fields) VALUES (?, '+10000000003', 'Test', '{}')")
    .run(programId).lastInsertRowid;
  const contact = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);

  const logs = [];
  const originalLog = console.log;
  console.log = (msg) => logs.push(msg);

  await processContact(db, null, contact, { dry_run: 1, delay_seconds: 60, jitter_seconds: 0 });

  console.log = originalLog;

  assert.ok(
    logs.some((line) => line.includes('a.png')),
    `expected a log line mentioning a.png, got: ${logs.join(' | ')}`
  );
  cleanup();
});
