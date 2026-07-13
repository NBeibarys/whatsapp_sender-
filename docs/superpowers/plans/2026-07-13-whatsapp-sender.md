# Silkroad WhatsApp Sender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working WhatsApp bulk-campaign sender for Silkroad Innovation Hub: a Node.js worker that sends queued messages through one Baileys WhatsApp session at a configurable pace, and a Streamlit control panel staff use to create programs, upload contact CSVs, and monitor/pause campaigns — all coordinated through a shared SQLite database.

**Architecture:** Two long-running processes (Node worker, Streamlit app) share one SQLite database (`data/silkroad.db`, WAL mode) as the single source of truth. The worker owns the WhatsApp session and polls for `pending` contacts round-robin across non-paused programs; Streamlit only ever reads/writes the database, never touching WhatsApp directly. Crash-safety comes from state living entirely in the DB (`pending` → `sending` → `sent`/`failed`, with anything stuck in `sending` on restart marked `needs_review` for manual handling).

**Tech Stack:** Node.js (`better-sqlite3`, `@whiskeysockets/baileys`, built-in `node:test`), Python (`streamlit`, `phonenumbers`, `pytest`, stdlib `sqlite3` — no pandas, to sidestep its int64/type-coercion pitfalls with phone numbers).

**Assumption carried into this plan:** default phone region for parsing locally-formatted numbers is `KZ` (Kazakhstan), based on Silkroad's Central Asia audience. Numbers already given with a `+` country code parse correctly regardless of this default.

---

## Task 1: Project Scaffolding

**Files:**
- Create: `package.json`
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `schema.sql`

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "silkroad-whatsapp-sender",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "test": "node --test worker/tests/",
    "worker": "node worker/index.js"
  },
  "dependencies": {
    "@whiskeysockets/baileys": "^6.7.9",
    "better-sqlite3": "^11.3.0",
    "pino": "^9.4.0"
  }
}
```

- [ ] **Step 2: Create `requirements.txt`**

```
streamlit>=1.38
phonenumbers>=8.13
pytest>=8.3
```

- [ ] **Step 3: Create `.gitignore`**

```
node_modules/
auth/
data/
__pycache__/
*.pyc
.venv/
```

- [ ] **Step 4: Create `schema.sql`**

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS programs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  template_text TEXT NOT NULL,
  paused INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  program_id INTEGER NOT NULL REFERENCES programs(id),
  phone TEXT NOT NULL,
  name TEXT NOT NULL,
  extra_fields TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','sending','sent','failed','needs_review','skipped')),
  rendered_message TEXT,
  sent_at TEXT,
  error_message TEXT,
  UNIQUE(program_id, phone)
);

CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  delay_seconds INTEGER NOT NULL DEFAULT 60,
  jitter_seconds INTEGER NOT NULL DEFAULT 0,
  daily_cap INTEGER,
  dry_run INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO settings (id, delay_seconds, jitter_seconds, daily_cap, dry_run)
  VALUES (1, 60, 0, NULL, 1);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_seen TEXT
);
INSERT OR IGNORE INTO worker_heartbeat (id, last_seen) VALUES (1, NULL);
```

- [ ] **Step 5: Install dependencies**

Run: `npm install && pip install -r requirements.txt`
Expected: both complete with no errors.

- [ ] **Step 6: Commit**

```bash
git add package.json requirements.txt .gitignore schema.sql
git commit -m "chore: project scaffolding and schema"
```

---

## Task 2: Node DB Module

**Files:**
- Create: `worker/db.js`
- Test: `worker/tests/db.test.js`

- [ ] **Step 1: Write the failing test**

```js
// worker/tests/db.test.js
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test worker/tests/db.test.js`
Expected: FAIL with "Cannot find module '../db'"

- [ ] **Step 3: Write minimal implementation**

```js
// worker/db.js
const Database = require('better-sqlite3');

function openDb(filePath) {
  const db = new Database(filePath);
  db.pragma('journal_mode = WAL');
  db.pragma('busy_timeout = 5000');
  return db;
}

module.exports = { openDb };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test worker/tests/db.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/db.js worker/tests/db.test.js
git commit -m "feat: Node DB module with WAL and busy_timeout"
```

---

## Task 3: Node Template Rendering

**Files:**
- Create: `worker/template.js`
- Test: `worker/tests/template.test.js`

- [ ] **Step 1: Write the failing test**

```js
// worker/tests/template.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { renderTemplate } = require('../template');

test('renderTemplate fills in placeholders from fields', () => {
  const result = renderTemplate(
    'Hi {{name}}, deadline for {{program}} is soon.',
    { name: 'Aigerim', program: 'Fall Cohort' }
  );
  assert.equal(result, 'Hi Aigerim, deadline for Fall Cohort is soon.');
});

test('renderTemplate throws on missing field', () => {
  assert.throws(() => {
    renderTemplate('Hi {{name}}', {});
  }, /Missing template field: name/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test worker/tests/template.test.js`
Expected: FAIL with "Cannot find module '../template'"

- [ ] **Step 3: Write minimal implementation**

```js
// worker/template.js
function renderTemplate(templateText, fields) {
  return templateText.replace(/\{\{(\w+)\}\}/g, (match, key) => {
    if (!(key in fields)) {
      throw new Error(`Missing template field: ${key}`);
    }
    return fields[key];
  });
}

module.exports = { renderTemplate };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test worker/tests/template.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/template.js worker/tests/template.test.js
git commit -m "feat: template placeholder rendering"
```

---

## Task 4: Test Helper + Heartbeat Module

**Files:**
- Create: `worker/tests/helpers.js`
- Create: `worker/heartbeat.js`
- Test: `worker/tests/heartbeat.test.js`

- [ ] **Step 1: Create the shared test helper**

```js
// worker/tests/helpers.js
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { openDb } = require('../db');

function makeTestDb() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'silkroad-test-'));
  const dbPath = path.join(dir, 'test.db');
  const db = openDb(dbPath);
  const schema = fs.readFileSync(path.join(__dirname, '..', '..', 'schema.sql'), 'utf8');
  db.exec(schema);
  return {
    db,
    cleanup: () => {
      db.close();
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

module.exports = { makeTestDb };
```

- [ ] **Step 2: Write the failing test for heartbeat**

```js
// worker/tests/heartbeat.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { makeTestDb } = require('./helpers');
const { updateHeartbeat, getHeartbeat } = require('../heartbeat');

test('updateHeartbeat writes a recent timestamp', () => {
  const { db, cleanup } = makeTestDb();

  updateHeartbeat(db);
  const lastSeen = getHeartbeat(db);
  const diffMs = Date.now() - new Date(lastSeen).getTime();

  assert.ok(diffMs >= 0 && diffMs < 5000, `expected recent timestamp, got diff ${diffMs}ms`);
  cleanup();
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `node --test worker/tests/heartbeat.test.js`
Expected: FAIL with "Cannot find module '../heartbeat'"

- [ ] **Step 4: Write minimal implementation**

```js
// worker/heartbeat.js
function updateHeartbeat(db) {
  db.prepare('UPDATE worker_heartbeat SET last_seen = ? WHERE id = 1').run(
    new Date().toISOString()
  );
}

function getHeartbeat(db) {
  const row = db.prepare('SELECT last_seen FROM worker_heartbeat WHERE id = 1').get();
  return row ? row.last_seen : null;
}

module.exports = { updateHeartbeat, getHeartbeat };
```

- [ ] **Step 5: Run test to verify it passes**

Run: `node --test worker/tests/heartbeat.test.js`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add worker/tests/helpers.js worker/heartbeat.js worker/tests/heartbeat.test.js
git commit -m "feat: worker heartbeat tracking"
```

---

## Task 5: Queue — Settings Read

**Files:**
- Create: `worker/queue.js`
- Test: `worker/tests/queue.test.js`

- [ ] **Step 1: Write the failing test**

```js
// worker/tests/queue.test.js
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test worker/tests/queue.test.js`
Expected: FAIL with "Cannot find module '../queue'"

- [ ] **Step 3: Write minimal implementation**

```js
// worker/queue.js
function getSettings(db) {
  return db
    .prepare('SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1')
    .get();
}

module.exports = { getSettings };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test worker/tests/queue.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/queue.js worker/tests/queue.test.js
git commit -m "feat: read worker settings from DB"
```

---

## Task 6: Queue — Status Transitions

**Files:**
- Modify: `worker/queue.js`
- Modify: `worker/tests/queue.test.js`

- [ ] **Step 1: Write the failing tests**

```js
// append to worker/tests/queue.test.js
const { markSending, markSent, markFailed } = require('../queue');

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test worker/tests/queue.test.js`
Expected: FAIL with "markSending is not a function" (and similarly for markSent/markFailed)

- [ ] **Step 3: Implement the functions**

```js
// append to worker/queue.js, before module.exports
function markSending(db, contactId) {
  db.prepare("UPDATE contacts SET status = 'sending' WHERE id = ?").run(contactId);
}

function markSent(db, contactId, renderedMessage) {
  db.prepare(
    "UPDATE contacts SET status = 'sent', rendered_message = ?, sent_at = ? WHERE id = ?"
  ).run(renderedMessage, new Date().toISOString(), contactId);
}

function markFailed(db, contactId, errorMessage) {
  db.prepare("UPDATE contacts SET status = 'failed', error_message = ? WHERE id = ?").run(
    errorMessage,
    contactId
  );
}
```

Update the exports at the bottom of `worker/queue.js`:

```js
module.exports = { getSettings, markSending, markSent, markFailed };
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test worker/tests/queue.test.js`
Expected: PASS (4 tests total)

- [ ] **Step 5: Commit**

```bash
git add worker/queue.js worker/tests/queue.test.js
git commit -m "feat: queue status transitions (sending/sent/failed)"
```

---

## Task 7: Queue — Crash Recovery

**Files:**
- Modify: `worker/queue.js`
- Modify: `worker/tests/queue.test.js`

- [ ] **Step 1: Write the failing test**

```js
// append to worker/tests/queue.test.js
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test worker/tests/queue.test.js`
Expected: FAIL with "recoverStuckSends is not a function"

- [ ] **Step 3: Implement the function**

```js
// append to worker/queue.js, before module.exports
function recoverStuckSends(db) {
  const result = db
    .prepare("UPDATE contacts SET status = 'needs_review' WHERE status = 'sending'")
    .run();
  return result.changes;
}
```

Update exports:

```js
module.exports = { getSettings, markSending, markSent, markFailed, recoverStuckSends };
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test worker/tests/queue.test.js`
Expected: PASS (6 tests total)

- [ ] **Step 5: Commit**

```bash
git add worker/queue.js worker/tests/queue.test.js
git commit -m "feat: crash recovery marks interrupted sends as needs_review"
```

---

## Task 8: Queue — Round-Robin Selection

**Files:**
- Modify: `worker/queue.js`
- Modify: `worker/tests/queue.test.js`

- [ ] **Step 1: Write the failing tests**

```js
// append to worker/tests/queue.test.js
const { getNextPendingContact } = require('../queue');

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
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000003', 'PendingA', 'pending')"
  ).run(progA);
  db.prepare(
    "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+10000000004', 'PendingB', 'pending')"
  ).run(progB);

  const next = getNextPendingContact(db);
  assert.equal(next.program_id, progA, 'A sent least recently (2026-01-01) so goes before B (2026-01-02)');
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test worker/tests/queue.test.js`
Expected: FAIL with "getNextPendingContact is not a function"

- [ ] **Step 3: Implement the function**

```js
// append to worker/queue.js, before module.exports
function getNextPendingContact(db) {
  return db
    .prepare(
      `
    SELECT c.* FROM contacts c
    JOIN programs p ON p.id = c.program_id
    WHERE c.status = 'pending' AND p.paused = 0
    ORDER BY
      (SELECT MAX(sent_at) FROM contacts c2 WHERE c2.program_id = c.program_id AND c2.status = 'sent') IS NULL DESC,
      (SELECT MAX(sent_at) FROM contacts c2 WHERE c2.program_id = c.program_id AND c2.status = 'sent') ASC,
      c.id ASC
    LIMIT 1
  `
    )
    .get();
}
```

Update exports:

```js
module.exports = {
  getSettings,
  markSending,
  markSent,
  markFailed,
  recoverStuckSends,
  getNextPendingContact,
};
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test worker/tests/queue.test.js`
Expected: PASS (9 tests total)

- [ ] **Step 5: Commit**

```bash
git add worker/queue.js worker/tests/queue.test.js
git commit -m "feat: round-robin pending-contact selection across programs"
```

---

## Task 9: Baileys Connection Wrapper

**Files:**
- Create: `worker/baileys.js`

No automated test for this file: it wraps a live WhatsApp Web session (QR scan, socket events) that only behaves correctly against the real service. Mocking Baileys' internals would test the mock, not real behavior — verified manually instead (Step 3 below).

- [ ] **Step 1: Write the module**

```js
// worker/baileys.js
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
} = require('@whiskeysockets/baileys');

async function connect(authDir) {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const sock = makeWASocket({ auth: state, printQRInTerminal: true });
  sock.ev.on('creds.update', saveCreds);

  return new Promise((resolve, reject) => {
    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect } = update;
      if (connection === 'open') {
        resolve(sock);
      } else if (connection === 'close') {
        const shouldReconnect =
          lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
        if (!shouldReconnect) {
          reject(new Error('WhatsApp session logged out. Delete auth/ and re-scan QR.'));
        }
      }
    });
  });
}

async function checkOnWhatsApp(sock, phone) {
  const [result] = await sock.onWhatsApp(phone);
  return Boolean(result?.exists);
}

async function sendMessage(sock, phone, text) {
  const jid = `${phone.replace('+', '')}@s.whatsapp.net`;
  await sock.sendMessage(jid, { text });
}

module.exports = { connect, checkOnWhatsApp, sendMessage };
```

- [ ] **Step 2: Verify the module loads without syntax errors**

Run: `node -e "require('./worker/baileys')"`
Expected: no output, exit code 0

- [ ] **Step 3: Manually verify a real connection (do this once you have a dedicated test SIM/number)**

Run: `node -e "require('./worker/baileys').connect('./auth').then(() => console.log('connected')).catch(e => console.error(e))"`
Expected: a QR code prints in the terminal; scan it with the WhatsApp mobile app on the number you're dedicating to this tool; "connected" prints once linked. The `auth/` folder now holds the persisted session — back it up.

- [ ] **Step 4: Commit**

```bash
git add worker/baileys.js
git commit -m "feat: Baileys connection, onWhatsApp check, and send wrapper"
```

---

## Task 10: Worker Entrypoint

**Files:**
- Create: `worker/index.js`

No automated test: this wires DB + Baileys + queue into a live polling loop with real timers and (optionally) a real WhatsApp connection — an integration concern, verified manually in Task 19's end-to-end check.

- [ ] **Step 1: Write the entrypoint**

```js
// worker/index.js
const fs = require('node:fs');
const path = require('node:path');
const { openDb } = require('./db');
const { renderTemplate } = require('./template');
const { updateHeartbeat } = require('./heartbeat');
const {
  getSettings,
  markSending,
  markSent,
  markFailed,
  recoverStuckSends,
  getNextPendingContact,
} = require('./queue');
const { connect, checkOnWhatsApp, sendMessage } = require('./baileys');

const DB_PATH = path.join(__dirname, '..', 'data', 'silkroad.db');
const AUTH_DIR = path.join(__dirname, '..', 'auth');
const SCHEMA_PATH = path.join(__dirname, '..', 'schema.sql');

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runLoop(db, sock) {
  const recovered = recoverStuckSends(db);
  if (recovered > 0) {
    console.log(`Marked ${recovered} interrupted send(s) as needs_review.`);
  }

  while (true) {
    updateHeartbeat(db);
    const settings = getSettings(db);
    const contact = getNextPendingContact(db);

    if (!contact) {
      await sleep(5000);
      continue;
    }

    markSending(db, contact.id);
    const extraFields = JSON.parse(contact.extra_fields || '{}');
    const program = db
      .prepare('SELECT template_text FROM programs WHERE id = ?')
      .get(contact.program_id);
    const message = renderTemplate(program.template_text, { name: contact.name, ...extraFields });

    try {
      if (settings.dry_run) {
        console.log(`[DRY RUN] Would send to ${contact.phone}: ${message}`);
      } else {
        const exists = await checkOnWhatsApp(sock, contact.phone);
        if (!exists) {
          throw new Error('Number not registered on WhatsApp');
        }
        await sendMessage(sock, contact.phone, message);
      }
      markSent(db, contact.id, message);
    } catch (err) {
      markFailed(db, contact.id, err.message);
    }

    const jitter = settings.jitter_seconds > 0 ? Math.random() * settings.jitter_seconds : 0;
    await sleep((settings.delay_seconds + jitter) * 1000);
  }
}

async function main() {
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  const db = openDb(DB_PATH);
  db.exec(fs.readFileSync(SCHEMA_PATH, 'utf8'));

  const settings = getSettings(db);
  const sock = settings.dry_run ? null : await connect(AUTH_DIR);

  await runLoop(db, sock);
}

main().catch((err) => {
  console.error('Worker crashed:', err);
  process.exit(1);
});
```

- [ ] **Step 2: Verify it starts in dry-run mode (default settings)**

Run: `node worker/index.js`
Expected: no crash; logs `Marked 0 interrupted send(s)...` is NOT printed (nothing was stuck), then it idles (sleeps 5s repeatedly) since no contacts exist yet. Stop with Ctrl+C — this just confirms the loop boots; full behavior is verified in Task 19 once there's real data.

- [ ] **Step 3: Commit**

```bash
git add worker/index.js
git commit -m "feat: worker entrypoint wiring DB, queue, and Baileys into a polling loop"
```

---

## Task 11: pm2 Process Config

**Files:**
- Create: `ecosystem.config.js`

- [ ] **Step 1: Write the config**

```js
// ecosystem.config.js
module.exports = {
  apps: [
    {
      name: 'silkroad-whatsapp-worker',
      script: 'worker/index.js',
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
  ],
};
```

- [ ] **Step 2: Manually verify pm2 can manage it**

Run: `pm2 start ecosystem.config.js && pm2 logs silkroad-whatsapp-worker --lines 20`
Expected: worker logs appear (same boot behavior as Task 10 Step 2)

Run: `pm2 stop silkroad-whatsapp-worker`
Expected: process stops cleanly

- [ ] **Step 3: Commit**

```bash
git add ecosystem.config.js
git commit -m "chore: pm2 process config for the worker"
```

---

## Task 12: Python Phone Normalization

**Files:**
- Create: `app/__init__.py` (empty, makes `app` a package)
- Create: `app/phone.py`
- Test: `app/tests/__init__.py` (empty)
- Test: `app/tests/test_phone.py`

- [ ] **Step 1: Create package markers**

```python
# app/__init__.py
```

```python
# app/tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

```python
# app/tests/test_phone.py
import pytest
from app.phone import normalize_phone, InvalidPhoneNumber


def test_normalize_phone_with_country_code():
    assert normalize_phone("+77012345678") == "+77012345678"


def test_normalize_phone_local_format_uses_default_region():
    assert normalize_phone("7012345678", default_region="KZ") == "+77012345678"


def test_normalize_phone_invalid_raises():
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone("not-a-phone")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest app/tests/test_phone.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'app.phone'"

- [ ] **Step 4: Write minimal implementation**

```python
# app/phone.py
import phonenumbers


class InvalidPhoneNumber(Exception):
    pass


def normalize_phone(raw: str, default_region: str = "KZ") -> str:
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException as e:
        raise InvalidPhoneNumber(f"Could not parse phone number: {raw}") from e

    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneNumber(f"Invalid phone number: {raw}")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest app/tests/test_phone.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add app/__init__.py app/phone.py app/tests/__init__.py app/tests/test_phone.py
git commit -m "feat: phone number normalization to E.164"
```

---

## Task 13: Python CSV Row Validation

**Files:**
- Create: `app/csv_import.py`
- Test: `app/tests/test_csv_import.py`

- [ ] **Step 1: Write the failing tests**

```python
# app/tests/test_csv_import.py
from app.csv_import import parse_contacts_rows


def test_parse_valid_rows():
    rows = [{"phone": "+77012345678", "name": "Aigerim", "program": "Fall Cohort"}]
    valid, invalid = parse_contacts_rows(rows)

    assert len(valid) == 1
    assert valid[0]["phone"] == "+77012345678"
    assert valid[0]["name"] == "Aigerim"
    assert valid[0]["extra_fields"] == {"program": "Fall Cohort"}
    assert invalid == []


def test_parse_rejects_missing_phone():
    rows = [{"phone": "", "name": "Aigerim"}]
    valid, invalid = parse_contacts_rows(rows)

    assert valid == []
    assert len(invalid) == 1
    assert invalid[0]["error"] == "Missing phone"


def test_parse_rejects_missing_name():
    rows = [{"phone": "+77012345678", "name": ""}]
    valid, invalid = parse_contacts_rows(rows)

    assert valid == []
    assert len(invalid) == 1
    assert invalid[0]["error"] == "Missing name"


def test_parse_rejects_invalid_phone():
    rows = [{"phone": "not-a-phone", "name": "Aigerim"}]
    valid, invalid = parse_contacts_rows(rows)

    assert valid == []
    assert len(invalid) == 1
    assert "phone" in invalid[0]["error"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest app/tests/test_csv_import.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'app.csv_import'"

- [ ] **Step 3: Write minimal implementation**

```python
# app/csv_import.py
from app.phone import normalize_phone, InvalidPhoneNumber


def parse_contacts_rows(rows, default_region="KZ"):
    """rows: list of dict per CSV row (at least 'phone' and 'name' columns).

    Returns (valid, invalid):
      valid: list of {"phone": str, "name": str, "extra_fields": dict}
      invalid: list of {"row": dict, "error": str}
    """
    valid = []
    invalid = []

    for row in rows:
        if not row.get("phone"):
            invalid.append({"row": row, "error": "Missing phone"})
            continue
        if not row.get("name"):
            invalid.append({"row": row, "error": "Missing name"})
            continue
        try:
            phone = normalize_phone(row["phone"], default_region)
        except InvalidPhoneNumber as e:
            invalid.append({"row": row, "error": str(e)})
            continue

        extra_fields = {k: v for k, v in row.items() if k not in ("phone", "name")}
        valid.append({"phone": phone, "name": row["name"], "extra_fields": extra_fields})

    return valid, invalid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest app/tests/test_csv_import.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/csv_import.py app/tests/test_csv_import.py
git commit -m "feat: CSV row validation splitting valid/invalid contacts"
```

---

## Task 14: Python DB Module — Programs

**Files:**
- Create: `app/db.py`
- Test: `app/tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_db.py
import os
import tempfile
import pytest
from app.db import get_connection, create_program

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "schema.sql")


@pytest.fixture
def conn():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    connection = get_connection(db_path)
    with open(SCHEMA_PATH) as f:
        connection.executescript(f.read())
    yield connection
    connection.close()


def test_create_program_returns_id(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")

    assert isinstance(program_id, int)
    row = conn.execute(
        "SELECT name, template_text FROM programs WHERE id = ?", (program_id,)
    ).fetchone()
    assert row[0] == "Fall Cohort"
    assert row[1] == "Hi {{name}}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest app/tests/test_db.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'app.db'"

- [ ] **Step 3: Write minimal implementation**

```python
# app/db.py
import sqlite3


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def create_program(conn: sqlite3.Connection, name: str, template_text: str) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO programs (name, template_text) VALUES (?, ?)",
            (name, template_text),
        )
        return cur.lastrowid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest app/tests/test_db.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/tests/test_db.py
git commit -m "feat: Python DB connection and program creation"
```

---

## Task 15: Python DB Module — Insert Contacts

**Files:**
- Modify: `app/db.py`
- Modify: `app/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to app/tests/test_db.py
from app.db import insert_contacts


def test_insert_contacts_inserts_valid_rows(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {"program": "Fall Cohort"}}]

    inserted, duplicates = insert_contacts(conn, program_id, valid)

    assert inserted == 1
    assert duplicates == []
    row = conn.execute(
        "SELECT phone, status FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()
    assert row[0] == "+77012345678"
    assert row[1] == "pending"


def test_insert_contacts_flags_duplicates(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {}}]
    insert_contacts(conn, program_id, valid)

    inserted, duplicates = insert_contacts(conn, program_id, valid)

    assert inserted == 0
    assert duplicates == ["+77012345678"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest app/tests/test_db.py -v`
Expected: FAIL with "ImportError: cannot import name 'insert_contacts'"

- [ ] **Step 3: Implement the function**

```python
# append to app/db.py
import json


def insert_contacts(conn: sqlite3.Connection, program_id: int, valid_contacts: list) -> tuple:
    """valid_contacts: list of {"phone", "name", "extra_fields"}.
    Returns (inserted_count, duplicate_phones).
    """
    inserted = 0
    duplicates = []
    with conn:
        for c in valid_contacts:
            try:
                conn.execute(
                    "INSERT INTO contacts (program_id, phone, name, extra_fields, status) "
                    "VALUES (?, ?, ?, ?, 'pending')",
                    (program_id, c["phone"], c["name"], json.dumps(c["extra_fields"])),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                duplicates.append(c["phone"])
    return inserted, duplicates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest app/tests/test_db.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/tests/test_db.py
git commit -m "feat: insert contacts with duplicate detection"
```

---

## Task 16: Streamlit — Programs Page

**Files:**
- Create: `app/streamlit_app.py`
- Create: `app/pages/1_Programs.py`

No automated test: Streamlit pages are UI glue over already-tested `app/db.py` functions — verified by running the app.

- [ ] **Step 1: Write the home page**

```python
# app/streamlit_app.py
import streamlit as st

st.title("Silkroad WhatsApp Sender")
st.write("Use the pages in the sidebar: Programs, Upload Contacts, Status.")
```

- [ ] **Step 2: Write the Programs page**

```python
# app/pages/1_Programs.py
import streamlit as st
from app.db import get_connection, create_program

st.title("Programs")

conn = get_connection("data/silkroad.db")

with st.form("new_program"):
    name = st.text_input("Program name")
    template_text = st.text_area("Message template (use {{name}}, {{field}} placeholders)")
    submitted = st.form_submit_button("Create program")
    if submitted:
        if not name or not template_text:
            st.error("Name and template are required.")
        else:
            program_id = create_program(conn, name, template_text)
            st.success(f"Created program '{name}' (id {program_id})")

st.subheader("Existing programs")
rows = conn.execute(
    "SELECT id, name, template_text, paused FROM programs ORDER BY created_at DESC"
).fetchall()
for row in rows:
    st.write(f"**{row[1]}** (id {row[0]}) — {'paused' if row[3] else 'active'}")
    st.code(row[2])
```

- [ ] **Step 3: Manually verify**

Run: `mkdir -p data && sqlite3 data/silkroad.db < schema.sql && streamlit run app/streamlit_app.py`
Expected: browser opens; sidebar shows "Programs"; creating a program with name "Test" and template "Hi {{name}}" shows a success message and appears in the list below.

- [ ] **Step 4: Commit**

```bash
git add app/streamlit_app.py app/pages/1_Programs.py
git commit -m "feat: Streamlit home and Programs page"
```

---

## Task 17: Streamlit — Upload Contacts Page

**Files:**
- Create: `app/pages/2_Upload_Contacts.py`

- [ ] **Step 1: Write the page**

```python
# app/pages/2_Upload_Contacts.py
import csv
import io
import streamlit as st
from app.db import get_connection, insert_contacts
from app.csv_import import parse_contacts_rows

st.title("Upload Contacts")

conn = get_connection("data/silkroad.db")
programs = conn.execute("SELECT id, name FROM programs ORDER BY name").fetchall()

if not programs:
    st.warning("Create a program first on the Programs page.")
else:
    program_choice = st.selectbox("Program", programs, format_func=lambda p: p[1])
    uploaded = st.file_uploader("Contacts CSV (columns: phone, name, plus any extra fields)", type="csv")

    if uploaded is not None:
        text = io.TextIOWrapper(uploaded, encoding="utf-8")
        rows = list(csv.DictReader(text))
        valid, invalid = parse_contacts_rows(rows)

        st.write(f"{len(valid)} valid row(s), {len(invalid)} invalid row(s)")

        if valid:
            st.subheader("Preview (first 3)")
            for v in valid[:3]:
                st.write(v)

        if invalid:
            st.subheader("Rejected rows")
            for i in invalid:
                st.write(i["row"], "->", i["error"])

        if valid and st.button("Queue these contacts"):
            inserted, duplicates = insert_contacts(conn, program_choice[0], valid)
            st.success(f"Queued {inserted} contact(s).")
            if duplicates:
                st.warning(
                    f"Skipped {len(duplicates)} duplicate(s) already in this program: {duplicates}"
                )
```

- [ ] **Step 2: Manually verify**

Create a test CSV at `/tmp/test_contacts.csv`:
```
phone,name,program
+77012345678,Test User,Fall Cohort
```

Run: `streamlit run app/streamlit_app.py`, navigate to "Upload Contacts", select the program created in Task 16, upload `/tmp/test_contacts.csv`.
Expected: shows "1 valid row(s), 0 invalid row(s)", preview shows the row, clicking "Queue these contacts" shows "Queued 1 contact(s)."

- [ ] **Step 3: Commit**

```bash
git add app/pages/2_Upload_Contacts.py
git commit -m "feat: Streamlit Upload Contacts page"
```

---

## Task 18: Streamlit — Status Page

**Files:**
- Create: `app/pages/3_Status.py`

- [ ] **Step 1: Write the page**

```python
# app/pages/3_Status.py
import streamlit as st
from app.db import get_connection

st.title("Campaign Status")

conn = get_connection("data/silkroad.db")

heartbeat = conn.execute("SELECT last_seen FROM worker_heartbeat WHERE id = 1").fetchone()
st.caption(f"Worker last seen: {heartbeat[0] if heartbeat and heartbeat[0] else 'never'}")

programs = conn.execute("SELECT id, name, paused FROM programs ORDER BY name").fetchall()

for program_id, name, paused in programs:
    st.subheader(f"{name} {'(paused)' if paused else ''}")

    counts = conn.execute(
        "SELECT status, COUNT(*) FROM contacts WHERE program_id = ? GROUP BY status",
        (program_id,),
    ).fetchall()
    counts_dict = dict(counts)
    st.write(counts_dict)

    col1, col2 = st.columns(2)
    with col1:
        if paused:
            if st.button(f"Resume {name}", key=f"resume-{program_id}"):
                conn.execute("UPDATE programs SET paused = 0 WHERE id = ?", (program_id,))
                conn.commit()
                st.rerun()
        else:
            if st.button(f"Pause {name}", key=f"pause-{program_id}"):
                conn.execute("UPDATE programs SET paused = 1 WHERE id = ?", (program_id,))
                conn.commit()
                st.rerun()
    with col2:
        failed_count = counts_dict.get("failed", 0)
        if failed_count and st.button(f"Retry {failed_count} failed", key=f"retry-{program_id}"):
            conn.execute(
                "UPDATE contacts SET status = 'pending', error_message = NULL "
                "WHERE program_id = ? AND status = 'failed'",
                (program_id,),
            )
            conn.commit()
            st.rerun()
```

- [ ] **Step 2: Manually verify**

Run: `streamlit run app/streamlit_app.py`, navigate to "Status".
Expected: shows the program from Task 16/17 with a `{'pending': 1}` count, a "Pause" button that flips it to "(paused)" and shows "Resume" on click.

- [ ] **Step 3: Commit**

```bash
git add app/pages/3_Status.py
git commit -m "feat: Streamlit Status page with pause/resume/retry"
```

---

## Task 19: End-to-End Dry-Run Verification

**Files:** none (verification only)

- [ ] **Step 1: Confirm a clean queued campaign**

With the Task 16-18 test data still in place (one program, one pending contact), confirm via the Status page that it shows `{'pending': 1}`.

- [ ] **Step 2: Run the worker in dry-run mode**

Run: `node worker/index.js`
Expected: logs `[DRY RUN] Would send to +77012345678: Hi Test User` (or similar, matching your template/CSV), then the Status page (refresh) shows `{'sent': 1}` and the contact's `rendered_message` column in the DB is populated:

Run: `sqlite3 data/silkroad.db "SELECT status, rendered_message FROM contacts;"`
Expected: `sent|Hi Test User` (or your actual rendered text)

- [ ] **Step 3: Verify crash-safety**

Queue another contact (Upload Contacts page, a second CSV row), start the worker, kill it (Ctrl+C) mid-delay (before the 60s wait finishes) right after a `[DRY RUN]` log line appears. Restart it.

Run: `node worker/index.js`
Expected: on restart, log line `Marked 1 interrupted send(s) as needs_review.` appears (assuming the killed process left that one row in `sending`), and:

Run: `sqlite3 data/silkroad.db "SELECT status FROM contacts WHERE status = 'needs_review';"`
Expected: one row — confirms restart never silently re-sends, matching the at-most-once design in the spec.

- [ ] **Step 4: Note remaining manual steps before a real campaign**

Not part of this plan's automated scope, but required before sending to real recipients (per the spec's testing path):
1. Flip `dry_run` to `0` in the `settings` table and re-run Task 9 Step 3 to scan the QR code with the dedicated hub number.
2. Send to 2-3 real test numbers first.
3. Only then run a full campaign.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: end-to-end verification complete" --allow-empty
```
