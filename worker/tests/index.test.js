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

test('processContact does NOT mark sent when the socket merely accepts the payload', async () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name, extra_fields) VALUES (?, '+10000000006', 'Test', '{}')")
    .run(programId).lastInsertRowid;
  const contact = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);

  // Fake socket: resolves like a real send that WhatsApp has not confirmed yet.
  const sock = {
    onWhatsApp: async () => [{ exists: true }],
    sendMessage: async () => ({ key: { id: 'WAMSG-123', fromMe: true } }),
  };

  await processContact(db, sock, contact, { dry_run: 0, delay_seconds: 60, jitter_seconds: 0 });

  const row = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'sending', 'must not claim sent before WhatsApp acks');
  assert.equal(row.delivery_state, 'pending_ack');
  assert.equal(row.wa_message_id, 'WAMSG-123');
  assert.equal(row.rendered_message, 'Hi Test');
  cleanup();
});

test('processContact still records the attempt when no message id comes back', async () => {
  const { db, cleanup } = makeTestDb();
  const programId = db
    .prepare("INSERT INTO programs (name, template_text) VALUES ('A', 'Hi {{name}}')")
    .run().lastInsertRowid;
  const contactId = db
    .prepare("INSERT INTO contacts (program_id, phone, name, extra_fields) VALUES (?, '+10000000007', 'Test', '{}')")
    .run(programId).lastInsertRowid;
  const contact = db.prepare('SELECT * FROM contacts WHERE id = ?').get(contactId);

  const sock = {
    onWhatsApp: async () => [{ exists: true }],
    sendMessage: async () => undefined,
  };

  await processContact(db, sock, contact, { dry_run: 0, delay_seconds: 60, jitter_seconds: 0 });

  const row = db.prepare('SELECT status, delivery_state, wa_message_id FROM contacts WHERE id = ?').get(contactId);
  assert.equal(row.status, 'sending');
  assert.equal(row.delivery_state, 'pending_ack', 'the ack timeout must be able to fail it');
  assert.equal(row.wa_message_id, null);
  cleanup();
});

const { reconnectDelayMs } = require('../index');

test('reconnect backoff grows exponentially and is capped', () => {
  // WhatsApp answers a retry storm with 405; the delay must widen fast.
  assert.equal(reconnectDelayMs(1), 5000);
  assert.equal(reconnectDelayMs(2), 10000);
  assert.equal(reconnectDelayMs(3), 20000);
  assert.equal(reconnectDelayMs(7), 300000, 'capped at 5 minutes');
  assert.equal(reconnectDelayMs(50), 300000, 'stays capped');
});

const { connectRetryPolicy } = require('../index');
const { preLoginCloseError } = require('../baileys');

test('an unscanned QR retries fast, so a fresh code is always seconds away', () => {
  // The operator is looking at the page right now: 5s, not a 5-minute backoff.
  const policy = connectRetryPolicy(preLoginCloseError(true), 0);

  assert.equal(policy.qrExpired, true);
  assert.equal(policy.waitMs, 5000);
});

test('QR expiry never grows the backoff counter, however many codes go unscanned', () => {
  // Baileys rotates through ~6 codes every couple of minutes. Before the fix
  // each cycle counted as a failure, so the wait climbed 5s → 300s and the
  // page sat QR-less for most of every cycle.
  let failures = 0;
  for (let cycle = 0; cycle < 20; cycle += 1) {
    const policy = connectRetryPolicy(preLoginCloseError(true), failures);
    failures = policy.failures;
    assert.equal(policy.waitMs, 5000, `cycle ${cycle} must still retry fast`);
  }
  assert.equal(failures, 0, 'unscanned codes are not connection failures');
});

test('a QR clears backoff earned by earlier refusals — WhatsApp is talking to us again', () => {
  const policy = connectRetryPolicy(preLoginCloseError(true), 6);

  assert.equal(policy.failures, 0);
  assert.equal(policy.waitMs, 5000);

  // And the escalation restarts from the bottom, exactly as after a success
  // (runLoop zeroes the same counter when connect() resolves).
  assert.equal(connectRetryPolicy(preLoginCloseError(false), policy.failures).waitMs, 5000);
});

test('a close with no QR keeps backing off exponentially (the 405 refusal case)', () => {
  // Hammering the registration endpoint is what deepens a block.
  let failures = 0;
  const waits = [];
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const policy = connectRetryPolicy(preLoginCloseError(false), failures);
    failures = policy.failures;
    waits.push(policy.waitMs);
    assert.equal(policy.qrExpired, false);
  }

  assert.deepEqual(waits, [5000, 10000, 20000, 40000]);
  assert.equal(failures, 4);
});

test('non-QR connect errors (network, logged out) also back off', () => {
  const policy = connectRetryPolicy(new Error('getaddrinfo ENOTFOUND'), 2);

  assert.equal(policy.qrExpired, false);
  assert.equal(policy.failures, 3);
  assert.equal(policy.waitMs, 20000);
});

test('a successful scan reconnects immediately instead of waiting', () => {
  // Baileys pairs, then closes with restartRequired to log in with the new
  // creds. Making the operator who just scanned wait 5s would be absurd.
  const policy = connectRetryPolicy(preLoginCloseError(true, true), 3);

  assert.equal(policy.pairingRestart, true);
  assert.equal(policy.waitMs, 0);
  assert.equal(policy.failures, 0);
  assert.equal(policy.qrExpired, false, 'a scanned code did not expire');
});

test('the pre-login error carries the flags the retry policy keys on', () => {
  // Contract between baileys.js and index.js: renaming these breaks fast retry.
  assert.equal(preLoginCloseError(true).qrEmitted, true);
  assert.equal(preLoginCloseError(false).qrEmitted, false);
  assert.equal(preLoginCloseError(true).restartRequired, false);
  assert.equal(preLoginCloseError(true, true).restartRequired, true);
  assert.match(preLoginCloseError(true).message, /QR code expired/);
  assert.match(preLoginCloseError(false).message, /before any QR code was issued/);
  assert.match(preLoginCloseError(true, true).message, /accepted the pairing/);
});

const { sleepUntil, handleDisconnectRequest } = require('../index');
const { requestDisconnect, isDisconnectRequested, setQrCode } = require('../heartbeat');

const SILENT_LOG = { log() {}, error() {} };

function disconnectHarness() {
  const calls = { exit: [], removedAuth: [] };
  return {
    calls,
    options: {
      log: SILENT_LOG,
      authDir: '/tmp/silkroad-test-auth-does-not-exist',
      exit: (code) => calls.exit.push(code),
      removeAuth: (dir) => calls.removedAuth.push(dir),
    },
  };
}

test('a disconnect requested while sock is null is honoured, not silently swallowed', async () => {
  const { db, cleanup } = makeTestDb();
  const harness = disconnectHarness();
  requestDisconnect(db);
  setQrCode(db, 'data:image/png;base64,stale');

  // sock === null is exactly the state during connection-failure backoff.
  const result = await handleDisconnectRequest(db, null, harness.options);

  assert.equal(result.acted, true, 'the request must be acted on, not dropped');
  assert.equal(result.hadSocket, false);
  assert.deepEqual(harness.calls.exit, [0], 'must exit cleanly so the supervisor respawns');
  assert.equal(harness.calls.removedAuth.length, 1, 'the stored session must be cleared');
  const row = db.prepare('SELECT disconnect_requested, connected, qr_code FROM worker_heartbeat WHERE id = 1').get();
  assert.equal(row.disconnect_requested, 0, 'consumed only because it was acted on');
  assert.equal(row.connected, 0);
  assert.equal(row.qr_code, null, 'the stale QR must be cleared');
  cleanup();
});

test('the disconnect flag is never consumed when no disconnect was requested', async () => {
  const { db, cleanup } = makeTestDb();
  const harness = disconnectHarness();

  const result = await handleDisconnectRequest(db, null, harness.options);

  assert.equal(result.acted, false);
  assert.deepEqual(harness.calls.exit, [], 'must not exit');
  assert.deepEqual(harness.calls.removedAuth, [], 'must not touch the session');
  cleanup();
});

test('a disconnect with a live socket still logs out before clearing the session', async () => {
  const { db, cleanup } = makeTestDb();
  const harness = disconnectHarness();
  requestDisconnect(db);
  let loggedOut = false;
  const sock = { logout: async () => { loggedOut = true; } };

  const result = await handleDisconnectRequest(db, sock, harness.options);

  assert.equal(result.acted, true);
  assert.equal(result.hadSocket, true);
  assert.equal(loggedOut, true);
  assert.deepEqual(harness.calls.exit, [0]);
  assert.equal(isDisconnectRequested(db), false);
  cleanup();
});

test('a failing logout still clears the session and exits', async () => {
  const { db, cleanup } = makeTestDb();
  const harness = disconnectHarness();
  requestDisconnect(db);
  const sock = { logout: async () => { throw new Error('socket already dead'); } };

  const result = await handleDisconnectRequest(db, sock, harness.options);

  assert.equal(result.acted, true);
  assert.equal(harness.calls.removedAuth.length, 1);
  assert.deepEqual(harness.calls.exit, [0]);
  cleanup();
});

test('sleepUntil wakes early instead of waiting out a long backoff', async () => {
  const started = Date.now();
  let wake = false;
  setTimeout(() => { wake = true; }, 30);

  // A 5-minute backoff must not delay an operator disconnect.
  const wokeEarly = await sleepUntil(300000, () => wake, 10);

  assert.equal(wokeEarly, true);
  assert.ok(Date.now() - started < 5000, 'must return promptly, not after the full sleep');
});

test('sleepUntil sleeps the full duration when nothing wakes it', async () => {
  const started = Date.now();

  const wokeEarly = await sleepUntil(60, () => false, 10);

  assert.equal(wokeEarly, false);
  assert.ok(Date.now() - started >= 55);
});
