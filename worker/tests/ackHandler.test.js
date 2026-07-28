const test = require('node:test');
const assert = require('node:assert/strict');
const { makeTestDb } = require('./helpers');
const { createAckTracker, WA_STATUS } = require('../ackHandler');
const { markSendAttempted, isHalted, getHalt } = require('../queue');

const SILENT = { warn() {}, error() {}, log() {} };

function seed(db, { phone = '+10000000001', waId = 'MSG1', programName = 'A' } = {}) {
  const programId = db
    .prepare('INSERT INTO programs (name, template_text) VALUES (?, ?)')
    .run(programName, 'Hi {{name}}').lastInsertRowid;
  const contactId = db
    .prepare('INSERT INTO contacts (program_id, phone, name, extra_fields) VALUES (?, ?, ?, ?)')
    .run(programId, phone, 'Test', '{}').lastInsertRowid;
  markSendAttempted(db, contactId, waId, 'Hi Test');
  return { programId, contactId };
}

function row(db, contactId) {
  return db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);
}

function ack(waId, status, extra) {
  return {
    key: { id: waId, fromMe: true, remoteJid: '10000000001@s.whatsapp.net' },
    update: Object.assign({ status }, extra),
  };
}

test('a send attempt parks the contact in pending_ack and does NOT claim sent', () => {
  const { db, cleanup } = makeTestDb();
  const { contactId } = seed(db);

  const contact = row(db, contactId);
  assert.equal(contact.status, 'sending');
  assert.equal(contact.delivery_state, 'pending_ack');
  assert.equal(contact.wa_message_id, 'MSG1');
  cleanup();
});

test('server ack promotes the contact to sent + server_ack', () => {
  const { db, cleanup } = makeTestDb();
  const { contactId } = seed(db);
  const tracker = createAckTracker(db, { log: SILENT });

  tracker.handleUpdate(ack('MSG1', WA_STATUS.SERVER_ACK));

  const contact = row(db, contactId);
  assert.equal(contact.status, 'sent');
  assert.equal(contact.delivery_state, 'server_ack');
  assert.ok(contact.sent_at);
  cleanup();
});

test('delivery and read receipts advance delivery_state and stamp times', () => {
  const { db, cleanup } = makeTestDb();
  const { contactId } = seed(db);
  const tracker = createAckTracker(db, { log: SILENT });

  tracker.handleUpdate(ack('MSG1', WA_STATUS.SERVER_ACK));
  tracker.handleUpdate(ack('MSG1', WA_STATUS.DELIVERY_ACK));
  let contact = row(db, contactId);
  assert.equal(contact.delivery_state, 'delivered');
  assert.ok(contact.delivered_at);

  tracker.handleUpdate(ack('MSG1', WA_STATUS.READ));
  contact = row(db, contactId);
  assert.equal(contact.delivery_state, 'read');
  assert.ok(contact.read_at);
  assert.ok(contact.delivered_at);
  cleanup();
});

test('a late server ack does not downgrade an already-read message', () => {
  const { db, cleanup } = makeTestDb();
  const { contactId } = seed(db);
  const tracker = createAckTracker(db, { log: SILENT });

  tracker.handleUpdate(ack('MSG1', WA_STATUS.READ));
  tracker.handleUpdate(ack('MSG1', WA_STATUS.SERVER_ACK));

  assert.equal(row(db, contactId).delivery_state, 'read');
  cleanup();
});

test('an ack ERROR marks the contact failed with the plain-language reason', () => {
  const { db, cleanup } = makeTestDb();
  const { contactId } = seed(db);
  const tracker = createAckTracker(db, { log: SILENT });

  tracker.handleUpdate(ack('MSG1', WA_STATUS.ERROR, { messageStubParameters: ['463'] }));

  const contact = row(db, contactId);
  assert.equal(contact.status, 'failed');
  assert.equal(contact.delivery_state, 'rejected');
  assert.equal(contact.ack_error, '463');
  assert.match(contact.error_message, /restricted from starting new chats/);
  cleanup();
});

test('acks for unknown message ids are ignored (messages sent from the phone)', () => {
  const { db, cleanup } = makeTestDb();
  const { contactId } = seed(db);
  const tracker = createAckTracker(db, { log: SILENT });

  const result = tracker.handleUpdate(ack('SOMEONE-ELSE', WA_STATUS.ERROR, {
    messageStubParameters: ['463'],
  }));

  assert.equal(result, null);
  assert.equal(row(db, contactId).delivery_state, 'pending_ack');
  cleanup();
});

test('an ack whose phone does not match the contact is ignored', () => {
  const { db, cleanup } = makeTestDb();
  const { contactId } = seed(db);
  const tracker = createAckTracker(db, { log: SILENT });

  const result = tracker.handleUpdate({
    key: { id: 'MSG1', fromMe: true, remoteJid: '19999999999@s.whatsapp.net' },
    update: { status: WA_STATUS.SERVER_ACK },
  });

  assert.equal(result, null);
  assert.equal(row(db, contactId).status, 'sending');
  cleanup();
});

test('inbound messages (fromMe false) never touch contact rows', () => {
  const { db, cleanup } = makeTestDb();
  const { contactId } = seed(db);
  const tracker = createAckTracker(db, { log: SILENT });

  tracker.handleUpdate({
    key: { id: 'MSG1', fromMe: false, remoteJid: '10000000001@s.whatsapp.net' },
    update: { status: WA_STATUS.READ },
  });

  assert.equal(row(db, contactId).delivery_state, 'pending_ack');
  cleanup();
});

test('three consecutive rejections halt sending and pause every program', () => {
  const { db, cleanup } = makeTestDb();
  const tracker = createAckTracker(db, { log: SILENT });
  ['+10000000001', '+10000000002', '+10000000003'].forEach(function (phone, i) {
    seed(db, { phone, waId: `MSG${i + 1}`, programName: `P${i}` });
  });

  tracker.handleUpdate(ack('MSG1', WA_STATUS.ERROR, { messageStubParameters: ['463'] }));
  assert.equal(isHalted(db), false, 'one rejection must not halt');
  tracker.handleUpdate({
    key: { id: 'MSG2', fromMe: true, remoteJid: '10000000002@s.whatsapp.net' },
    update: { status: WA_STATUS.ERROR, messageStubParameters: ['463'] },
  });
  assert.equal(isHalted(db), false, 'two rejections must not halt');
  tracker.handleUpdate({
    key: { id: 'MSG3', fromMe: true, remoteJid: '10000000003@s.whatsapp.net' },
    update: { status: WA_STATUS.ERROR, messageStubParameters: ['463'] },
  });

  assert.equal(isHalted(db), true, 'three rejections in a row must halt');
  const halt = getHalt(db);
  assert.match(halt.halt_reason, /3 messages in a row/);
  assert.match(halt.halt_reason, /restricted from starting new chats/);
  const paused = db.prepare('SELECT COUNT(*) AS n FROM programs WHERE paused = 0').get();
  assert.equal(paused.n, 0, 'every program must be paused');
  cleanup();
});

test('a successful server ack resets the consecutive rejection counter', () => {
  const { db, cleanup } = makeTestDb();
  const tracker = createAckTracker(db, { log: SILENT });
  seed(db, { phone: '+10000000001', waId: 'MSG1', programName: 'P0' });
  seed(db, { phone: '+10000000002', waId: 'MSG2', programName: 'P1' });

  tracker.handleUpdate(ack('MSG1', WA_STATUS.ERROR, { messageStubParameters: ['463'] }));
  assert.equal(tracker.consecutiveRejections, 1);
  tracker.handleUpdate({
    key: { id: 'MSG2', fromMe: true, remoteJid: '10000000002@s.whatsapp.net' },
    update: { status: WA_STATUS.SERVER_ACK },
  });

  assert.equal(tracker.consecutiveRejections, 0);
  assert.equal(isHalted(db), false);
  cleanup();
});

test('ack timeouts count towards the halt threshold too', () => {
  const { db, cleanup } = makeTestDb();
  const tracker = createAckTracker(db, { log: SILENT });
  db.prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi')").run();

  tracker.registerTimeouts(3);

  assert.equal(isHalted(db), true);
  assert.match(getHalt(db).halt_reason, /No confirmation from WhatsApp in time/);
  cleanup();
});
