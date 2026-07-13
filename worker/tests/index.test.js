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
