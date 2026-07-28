const test = require('node:test');
const assert = require('node:assert/strict');
const { makeTestDb } = require('./helpers');
const { getSettings, markSending, markSent, markFailed, getNextPendingContact } = require('../queue');

test('getSettings returns default settings row', () => {
  const { db, cleanup } = makeTestDb();

  const settings = getSettings(db);

  assert.equal(settings.delay_seconds, 60);
  assert.equal(settings.jitter_seconds, 15);
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

// --- Truthful send status: ack timeouts, recovery, replies ---

const {
  markSendAttempted,
  sweepAckTimeouts,
  recordReply,
  ACK_TIMEOUT_MS,
} = require('../queue');

function seedContact(db, phone, name) {
  const programId = db
    .prepare('INSERT INTO programs (name, template_text) VALUES (?, ?)')
    .run(name, 'Hi {{name}}').lastInsertRowid;
  return db
    .prepare('INSERT INTO contacts (program_id, phone, name, extra_fields) VALUES (?, ?, ?, ?)')
    .run(programId, phone, name, '{}').lastInsertRowid;
}

test('recoverStuckSends does NOT clobber a fresh row that is awaiting an ack', () => {
  const { db, cleanup } = makeTestDb();
  const contactId = seedContact(db, '+10000000010', 'Awaiting');
  markSendAttempted(db, contactId, 'MSG-FRESH', 'Hi Awaiting');

  const changed = recoverStuckSends(db);

  const row = db.prepare('SELECT status, delivery_state FROM contacts WHERE id = ?').get(contactId);
  assert.equal(changed, 0);
  assert.equal(row.status, 'sending');
  assert.equal(row.delivery_state, 'pending_ack');
  cleanup();
});

test('sweepAckTimeouts leaves a send that is still inside the ack window alone', () => {
  const { db, cleanup } = makeTestDb();
  const contactId = seedContact(db, '+10000000011', 'Fresh');
  markSendAttempted(db, contactId, 'MSG-FRESH', 'Hi Fresh');

  const swept = sweepAckTimeouts(db, ACK_TIMEOUT_MS);

  assert.equal(swept.length, 0);
  assert.equal(db.prepare('SELECT status FROM contacts WHERE id = ?').get(contactId).status, 'sending');
  cleanup();
});

test('sweepAckTimeouts fails a send that never got confirmed within the window', () => {
  const { db, cleanup } = makeTestDb();
  const contactId = seedContact(db, '+10000000012', 'Stale');
  markSendAttempted(db, contactId, 'MSG-STALE', 'Hi Stale');
  // Backdate the attempt past the ack window.
  db.prepare('UPDATE contacts SET sent_at = ? WHERE id = ?').run(
    new Date(Date.now() - 5 * ACK_TIMEOUT_MS).toISOString(),
    contactId
  );

  const swept = sweepAckTimeouts(db, ACK_TIMEOUT_MS);

  const row = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);
  assert.equal(swept.length, 1);
  assert.equal(row.status, 'failed');
  assert.equal(row.delivery_state, 'rejected');
  assert.equal(row.ack_error, 'timeout');
  assert.equal(row.error_message, 'No confirmation from WhatsApp within 60s.');
  cleanup();
});

test('sweepAckTimeouts ignores contacts that already have a real delivery state', () => {
  const { db, cleanup } = makeTestDb();
  const contactId = seedContact(db, '+10000000013', 'Delivered');
  markSendAttempted(db, contactId, 'MSG-OK', 'Hi Delivered');
  db.prepare(
    "UPDATE contacts SET delivery_state = 'delivered', status = 'sent', sent_at = ? WHERE id = ?"
  ).run(new Date(Date.now() - 5 * ACK_TIMEOUT_MS).toISOString(), contactId);

  const swept = sweepAckTimeouts(db, ACK_TIMEOUT_MS);

  assert.equal(swept.length, 0);
  assert.equal(db.prepare('SELECT status FROM contacts WHERE id = ?').get(contactId).status, 'sent');
  cleanup();
});

test('recordReply stores the body and links it to the contact we messaged', () => {
  const { db, cleanup } = makeTestDb();
  const contactId = seedContact(db, '+10000000014', 'Replier');
  db.prepare("UPDATE contacts SET status = 'sent', sent_at = ? WHERE id = ?").run(
    new Date().toISOString(),
    contactId
  );

  recordReply(db, '+10000000014', 'Yes, interested!');

  const reply = db.prepare('SELECT * FROM replies').get();
  assert.equal(reply.contact_id, contactId);
  assert.equal(reply.phone, '+10000000014');
  assert.equal(reply.body, 'Yes, interested!');
  assert.ok(reply.received_at);
  cleanup();
});

test('recordReply truncates very long bodies to 2000 chars', () => {
  const { db, cleanup } = makeTestDb();
  seedContact(db, '+10000000015', 'Chatty');

  recordReply(db, '+10000000015', 'x'.repeat(5000));

  assert.equal(db.prepare('SELECT body FROM replies').get().body.length, 2000);
  cleanup();
});

test('recordReply keeps a reply from an unknown number (contact_id NULL)', () => {
  const { db, cleanup } = makeTestDb();

  recordReply(db, '+19999999999', 'who is this?');

  const reply = db.prepare('SELECT * FROM replies').get();
  assert.equal(reply.contact_id, null);
  assert.equal(reply.body, 'who is this?');
  cleanup();
});

// --- sent_at semantics + crash-during-send recovery (review fixes) ---

const { markSendStarted, markServerAck, markDelivered, markRead } = require('../queue');

test('sent_at is the attempt time and a later ack never moves it', () => {
  const { db, cleanup } = makeTestDb();
  const contactId = seedContact(db, '+10000000020', 'Stable');

  markSendStarted(db, contactId);
  const attemptedAt = db.prepare('SELECT sent_at FROM contacts WHERE id = ?').get(contactId).sent_at;
  markSendAttempted(db, contactId, 'MSG-STABLE', 'Hi Stable');
  assert.equal(
    db.prepare('SELECT sent_at FROM contacts WHERE id = ?').get(contactId).sent_at,
    attemptedAt,
    'recording the message id must not move sent_at'
  );

  markServerAck(db, contactId);
  assert.equal(
    db.prepare('SELECT sent_at FROM contacts WHERE id = ?').get(contactId).sent_at,
    attemptedAt,
    'the server ack must not move sent_at (it would shift the daily-cap bucket)'
  );

  markDelivered(db, contactId);
  markRead(db, contactId);
  const row = db.prepare('SELECT sent_at, delivered_at, read_at FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.sent_at, attemptedAt, 'receipts must not move sent_at either');
  assert.ok(row.delivered_at && row.read_at);
  cleanup();
});

test('an ack landing the next day does not move the send into that day (daily cap)', () => {
  const { db, cleanup } = makeTestDb();
  const contactId = seedContact(db, '+10000000021', 'Yesterday');
  markSendStarted(db, contactId);
  markSendAttempted(db, contactId, 'MSG-YDAY', 'Hi');
  // Backdate the attempt to yesterday, then let the ack arrive now.
  const yesterday = new Date(Date.now() - 26 * 3600 * 1000).toISOString();
  db.prepare('UPDATE contacts SET sent_at = ? WHERE id = ?').run(yesterday, contactId);

  markServerAck(db, contactId);

  const row = db.prepare('SELECT status, sent_at FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'sent');
  assert.equal(row.sent_at, yesterday);
  assert.equal(countSentToday(db), 0, "yesterday's send must not consume today's cap");
  cleanup();
});

test('a crash between the socket call and recording leaves a recoverable row', () => {
  const { db, cleanup } = makeTestDb();
  const contactId = seedContact(db, '+10000000022', 'Crashed');

  // markSendStarted ran, the socket call went out, then the process died
  // before markSendAttempted — exactly the duplicate-send window.
  markSendStarted(db, contactId);
  const mid = db.prepare('SELECT status, delivery_state FROM contacts WHERE id = ?').get(contactId);
  assert.equal(mid.status, 'sending');
  assert.equal(mid.delivery_state, null);
  assert.equal(getNextPendingContact(db), undefined, 'a claimed row must not be picked up again');

  // Restart:
  const recovered = recoverStuckSends(db);

  assert.equal(recovered, 1);
  const row = db.prepare('SELECT status FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'needs_review', 'the operator decides; it is never silently re-sent');
  assert.equal(getNextPendingContact(db), undefined);
  cleanup();
});

test('no row can be stranded: every in-flight row is either swept or recovered', () => {
  const { db, cleanup } = makeTestDb();
  const claimed = seedContact(db, '+10000000023', 'Claimed');
  const awaiting = seedContact(db, '+10000000024', 'Awaiting');
  markSendStarted(db, claimed);
  markSendStarted(db, awaiting);
  markSendAttempted(db, awaiting, 'MSG-AWAIT', 'Hi');
  db.prepare('UPDATE contacts SET sent_at = ? WHERE id = ?').run(
    new Date(Date.now() - 5 * ACK_TIMEOUT_MS).toISOString(),
    awaiting
  );

  recoverStuckSends(db);
  sweepAckTimeouts(db, ACK_TIMEOUT_MS);

  assert.equal(db.prepare('SELECT status FROM contacts WHERE id = ?').get(claimed).status, 'needs_review');
  assert.equal(db.prepare('SELECT status FROM contacts WHERE id = ?').get(awaiting).status, 'failed');
  assert.equal(
    db.prepare("SELECT COUNT(*) AS n FROM contacts WHERE status = 'sending'").get().n,
    0,
    'nothing may be left in sending'
  );
  cleanup();
});
