# Campaign Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the Streamlit control panel from 4 pages into 3 (Campaign / Connection / Settings), with a single Apollo-style Campaign workspace covering campaign creation, template editing with file attachments, contact import/entry, bulk contact actions, and stats — plus worker-side support for sending attachments and detecting replies.

**Architecture:** No new services. The Node worker gains attachment-aware sending and an inbound-message listener for reply detection; the SQLite schema gains `contacts.replied_at` and a `program_attachments` table; the Streamlit app is restructured from 4 page files into 3, with all campaign management (today spread across Programs/Upload Contacts/Status) merged into one page.

**Tech Stack:** Node.js (`better-sqlite3`, `@whiskeysockets/baileys`, `node:test`), Python (`streamlit`, `phonenumbers`, `pytest`, stdlib `sqlite3`).

**Spec:** `docs/superpowers/specs/2026-07-13-campaign-workspace-design.md`

---

## Task 1: Schema — `replied_at` column and `program_attachments` table

**Files:**
- Modify: `schema.sql`

- [ ] **Step 1: Add `replied_at` to the `contacts` table definition**

In `schema.sql`, change:

```sql
CREATE TABLE IF NOT EXISTS contacts (
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
  UNIQUE(program_id, phone)
);
```

to:

```sql
CREATE TABLE IF NOT EXISTS contacts (
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
```

- [ ] **Step 2: Add the `program_attachments` table**

Append after the `contacts` table definition in `schema.sql` (before `CREATE TABLE IF NOT EXISTS settings`):

```sql
CREATE TABLE IF NOT EXISTS program_attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  program_id INTEGER NOT NULL REFERENCES programs(id),
  file_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  media_type TEXT NOT NULL CHECK(media_type IN ('image', 'document')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 3: Verify test helpers pick up the new schema for free**

`worker/tests/helpers.js` and `app/tests/test_db.py`'s `conn` fixture both create a fresh temp DB and run `schema.sql` from scratch on every test run, so no test changes are needed here — the new column/table are simply present. Confirm this by running the existing suites:

Run: `npm test`
Expected: PASS (same count as before — 16 tests)

Run: `.venv/bin/pytest app/tests/ -v`
Expected: PASS (same count as before — 10 tests)

- [ ] **Step 4: Migrate the live database**

The running `data/silkroad.db` was created before this schema change; `CREATE TABLE IF NOT EXISTS` won't retroactively add a column to an existing table, so migrate it directly:

Run:
```bash
.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('data/silkroad.db')
cols = [r[1] for r in conn.execute('PRAGMA table_info(contacts)').fetchall()]
if 'replied_at' not in cols:
    conn.execute('ALTER TABLE contacts ADD COLUMN replied_at TEXT')
conn.execute('''
CREATE TABLE IF NOT EXISTS program_attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  program_id INTEGER NOT NULL REFERENCES programs(id),
  file_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  media_type TEXT NOT NULL CHECK(media_type IN ('image', 'document')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
''')
conn.commit()
print('contacts columns:', [r[1] for r in conn.execute('PRAGMA table_info(contacts)').fetchall()])
print('program_attachments exists:', conn.execute(\"SELECT name FROM sqlite_master WHERE name='program_attachments'\").fetchone())
"
```
Expected: prints `replied_at` in the contacts column list, and a non-`None` result for `program_attachments`.

- [ ] **Step 5: Commit**

```bash
git add schema.sql
git commit -m "feat: add replied_at column and program_attachments table"
```

---

## Task 2: Worker — `markReplied` in `queue.js`

**Files:**
- Modify: `worker/queue.js`
- Modify: `worker/tests/queue.test.js`

- [ ] **Step 1: Write the failing tests**

Append to `worker/tests/queue.test.js`:

```js
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test worker/tests/queue.test.js`
Expected: FAIL with "markReplied is not a function"

- [ ] **Step 3: Implement the function**

Append to `worker/queue.js`, before `module.exports`:

```js
function markReplied(db, phone) {
  const result = db
    .prepare(
      "UPDATE contacts SET replied_at = ? WHERE phone = ? AND status = 'sent' AND replied_at IS NULL"
    )
    .run(new Date().toISOString(), phone);
  return result.changes;
}
```

Update the exports at the bottom of `worker/queue.js`:

```js
module.exports = {
  getSettings,
  markSending,
  markSent,
  markFailed,
  recoverStuckSends,
  getNextPendingContact,
  countSentToday,
  markReplied,
};
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test worker/tests/queue.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/queue.js worker/tests/queue.test.js
git commit -m "feat: add markReplied for inbound-message reply detection"
```

---

## Task 3: Worker — `getAttachments` in `queue.js`

**Files:**
- Modify: `worker/queue.js`
- Modify: `worker/tests/queue.test.js`

- [ ] **Step 1: Write the failing tests**

Append to `worker/tests/queue.test.js`:

```js
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test worker/tests/queue.test.js`
Expected: FAIL with "getAttachments is not a function"

- [ ] **Step 3: Implement the function**

Append to `worker/queue.js`, before `module.exports`:

```js
function getAttachments(db, programId) {
  return db
    .prepare('SELECT * FROM program_attachments WHERE program_id = ? ORDER BY id ASC')
    .all(programId);
}
```

Update the exports:

```js
module.exports = {
  getSettings,
  markSending,
  markSent,
  markFailed,
  recoverStuckSends,
  getNextPendingContact,
  countSentToday,
  markReplied,
  getAttachments,
};
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test worker/tests/queue.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker/queue.js worker/tests/queue.test.js
git commit -m "feat: add getAttachments query for program-level media"
```

---

## Task 4: Worker — reply listener wired into the live socket

**Files:**
- Modify: `worker/baileys.js`
- Modify: `worker/index.js`

No automated test: this registers a listener on a live Baileys socket — same convention as `connect`, `checkOnWhatsApp`, and `sendMessage`, which are verified manually rather than unit tested (a live WhatsApp session is required to observe real inbound events). Verified manually in Step 3.

- [ ] **Step 1: Add `registerReplyListener` to `worker/baileys.js`**

Add the import at the top of `worker/baileys.js` (alongside the existing heartbeat import):

```js
const { setQrCode, clearQrCode, updateHeartbeat, markConnected, markDisconnected } = require('./heartbeat');
const { markReplied } = require('./queue');
```

Append this function after `connect` and before `checkOnWhatsApp`:

```js
function registerReplyListener(sock, db) {
  sock.ev.on('messages.upsert', ({ messages }) => {
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      const remoteJid = msg.key.remoteJid || '';
      if (!remoteJid.endsWith('@s.whatsapp.net')) continue;
      const phone = `+${remoteJid.replace('@s.whatsapp.net', '')}`;
      markReplied(db, phone);
    }
  });
}
```

Update the exports at the bottom of `worker/baileys.js`:

```js
module.exports = { connect, checkOnWhatsApp, sendMessage, registerReplyListener };
```

- [ ] **Step 2: Wire it into `worker/index.js`**

Change the import line:

```js
const { connect, checkOnWhatsApp, sendMessage } = require('./baileys');
```

to:

```js
const { connect, checkOnWhatsApp, sendMessage, registerReplyListener } = require('./baileys');
```

In `main()`, after the `connect` call, register the listener only when actually connected (not in `dry_run`, where `sock` is `null`):

```js
  const settings = getSettings(db);
  const sock = settings.dry_run ? null : await connect(AUTH_DIR, db);
  if (sock) {
    registerReplyListener(sock, db);
  }
```

- [ ] **Step 3: Verify the module loads without syntax errors, then verify manually**

Run: `node -e "require('./worker/baileys'); require('./worker/index')"`
Expected: no output, exit code 0

Manual verification (requires the worker connected to a real WhatsApp session, `dry_run = 0`):
1. Restart the worker: `pm2 restart silkroad-whatsapp-worker`
2. Send a message to a contact whose row has `status = 'sent'` (e.g. re-run the "Send test message" flow first if needed so there's a `sent` row for a phone you control).
3. From that phone, send any WhatsApp message back to the linked number.
4. Run: `.venv/bin/python -c "import sqlite3; print(sqlite3.connect('data/silkroad.db').execute('SELECT phone, replied_at FROM contacts WHERE replied_at IS NOT NULL').fetchall())"`
Expected: the row for that phone shows a non-null `replied_at` timestamp.

- [ ] **Step 4: Commit**

```bash
git add worker/baileys.js worker/index.js
git commit -m "feat: detect and record inbound replies from contacts"
```

---

## Task 5: Worker — send template attachments

**Files:**
- Modify: `worker/baileys.js`
- Modify: `worker/index.js`
- Test: `worker/tests/index.test.js`

- [ ] **Step 1: Write the failing test (dry_run attachment logging)**

Append to `worker/tests/index.test.js`:

```js
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test worker/tests/index.test.js`
Expected: FAIL — no log line mentions `a.png`

- [ ] **Step 3: Add `sendMediaMessage` to `worker/baileys.js`**

Append after `sendMessage` in `worker/baileys.js`:

```js
async function sendMediaMessage(sock, phone, attachment, caption) {
  const jid = `${phone.replace('+', '')}@s.whatsapp.net`;
  const content =
    attachment.media_type === 'image'
      ? { image: { url: attachment.file_path }, caption }
      : { document: { url: attachment.file_path }, fileName: attachment.file_name, caption };
  await sock.sendMessage(jid, content);
}
```

Update the exports:

```js
module.exports = { connect, checkOnWhatsApp, sendMessage, sendMediaMessage, registerReplyListener };
```

- [ ] **Step 4: Update `processContact` in `worker/index.js`**

Change the import line:

```js
const { connect, checkOnWhatsApp, sendMessage, registerReplyListener } = require('./baileys');
```

to:

```js
const { connect, checkOnWhatsApp, sendMessage, sendMediaMessage, registerReplyListener } = require('./baileys');
```

Change the import from `./queue` to include `getAttachments`:

```js
const {
  getSettings,
  markSending,
  markSent,
  markFailed,
  recoverStuckSends,
  getNextPendingContact,
  countSentToday,
  getAttachments,
} = require('./queue');
```

Replace `processContact` with:

```js
async function processContact(db, sock, contact, settings) {
  markSending(db, contact.id);
  const extraFields = JSON.parse(contact.extra_fields || '{}');
  const program = db
    .prepare('SELECT template_text FROM programs WHERE id = ?')
    .get(contact.program_id);
  const attachments = getAttachments(db, contact.program_id);

  try {
    const message = renderTemplate(program.template_text, { name: contact.name, ...extraFields });

    if (settings.dry_run) {
      const attachmentNote = attachments.length
        ? ` [with attachments: ${attachments.map((a) => a.file_name).join(', ')}]`
        : '';
      console.log(`[DRY RUN] Would send to ${contact.phone}: ${message}${attachmentNote}`);
    } else {
      const exists = await checkOnWhatsApp(sock, contact.phone);
      if (!exists) {
        throw new Error('Number not registered on WhatsApp');
      }
      if (attachments.length === 0) {
        await sendMessage(sock, contact.phone, message);
      } else {
        await sendMediaMessage(sock, contact.phone, attachments[0], message);
        for (const attachment of attachments.slice(1)) {
          await sendMediaMessage(sock, contact.phone, attachment, undefined);
        }
      }
    }
    markSent(db, contact.id, message);
  } catch (err) {
    markFailed(db, contact.id, err.message);
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test worker/tests/index.test.js`
Expected: PASS (3 tests)

Run: `npm test`
Expected: PASS (all suites)

Real-send attachment path (`sendMediaMessage` against a live socket) has no automated test — same convention as `sendMessage`. Verified manually in Task 16.

- [ ] **Step 6: Commit**

```bash
git add worker/baileys.js worker/index.js worker/tests/index.test.js
git commit -m "feat: send template attachments as image/document messages"
```

---

## Task 6: Python — attachment CRUD in `app/db.py`

**Files:**
- Modify: `app/db.py`
- Modify: `app/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Append to `app/tests/test_db.py`:

```python
from app.db import add_attachment, list_attachments, delete_attachment


def test_add_attachment_saves_file_and_returns_id(conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")

    attachment_id = add_attachment(conn, program_id, "flyer.png", b"fake-image-bytes")

    assert isinstance(attachment_id, int)
    attachments = list_attachments(conn, program_id)
    assert len(attachments) == 1
    assert attachments[0]["file_name"] == "flyer.png"
    assert attachments[0]["media_type"] == "image"
    assert os.path.exists(attachments[0]["file_path"])


def test_add_attachment_classifies_non_image_as_document(conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")

    add_attachment(conn, program_id, "brochure.pdf", b"fake-pdf-bytes")

    attachments = list_attachments(conn, program_id)
    assert attachments[0]["media_type"] == "document"


def test_delete_attachment_removes_row_and_file(conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    attachment_id = add_attachment(conn, program_id, "flyer.png", b"fake-image-bytes")
    file_path = list_attachments(conn, program_id)[0]["file_path"]

    delete_attachment(conn, attachment_id)

    assert list_attachments(conn, program_id) == []
    assert not os.path.exists(file_path)
```

`os` is already imported at the top of `app/tests/test_db.py`; `tmp_path` and `monkeypatch` are built-in pytest fixtures, no import needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest app/tests/test_db.py -v`
Expected: FAIL with "ImportError: cannot import name 'add_attachment'"

- [ ] **Step 3: Implement the functions**

Append to `app/db.py`:

```python
import uuid

MEDIA_DIR = "media"


def add_attachment(conn: sqlite3.Connection, program_id: int, file_name: str, content: bytes) -> int:
    ext = os.path.splitext(file_name)[1].lower()
    media_type = "image" if ext in (".jpg", ".jpeg", ".png") else "document"
    program_dir = os.path.join(MEDIA_DIR, str(program_id))
    os.makedirs(program_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}-{file_name}"
    file_path = os.path.join(program_dir, stored_name)
    with open(file_path, "wb") as f:
        f.write(content)
    with conn:
        cur = conn.execute(
            "INSERT INTO program_attachments (program_id, file_path, file_name, media_type) "
            "VALUES (?, ?, ?, ?)",
            (program_id, file_path, file_name, media_type),
        )
        return cur.lastrowid


def list_attachments(conn: sqlite3.Connection, program_id: int) -> list:
    rows = conn.execute(
        "SELECT id, file_path, file_name, media_type FROM program_attachments "
        "WHERE program_id = ? ORDER BY id",
        (program_id,),
    ).fetchall()
    return [{"id": r[0], "file_path": r[1], "file_name": r[2], "media_type": r[3]} for r in rows]


def delete_attachment(conn: sqlite3.Connection, attachment_id: int) -> None:
    row = conn.execute(
        "SELECT file_path FROM program_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    if row is None:
        return
    file_path = row[0]
    with conn:
        conn.execute("DELETE FROM program_attachments WHERE id = ?", (attachment_id,))
    if os.path.exists(file_path):
        os.remove(file_path)
```

`app/db.py` needs `import os` at the top — check first: it currently only imports `sqlite3` (and `json`, added later in the file for `insert_contacts`). Add `import os` alongside the existing `import json` / `import sqlite3` lines at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest app/tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/tests/test_db.py
git commit -m "feat: attachment CRUD (add/list/delete) with file storage under media/"
```

---

## Task 7: Python — `delete_contact` in `app/db.py`

**Files:**
- Modify: `app/db.py`
- Modify: `app/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Append to `app/tests/test_db.py`:

```python
from app.db import delete_contact


def test_delete_contact_removes_pending_contact(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {}}]
    insert_contacts(conn, program_id, valid)
    contact_id = conn.execute(
        "SELECT id FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()[0]

    delete_contact(conn, contact_id)

    row = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    assert row is None


def test_delete_contact_does_not_delete_a_sent_contact(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {}}]
    insert_contacts(conn, program_id, valid)
    contact_id = conn.execute(
        "SELECT id FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()[0]
    conn.execute("UPDATE contacts SET status = 'sent' WHERE id = ?", (contact_id,))
    conn.commit()

    delete_contact(conn, contact_id)

    row = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    assert row is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest app/tests/test_db.py -v`
Expected: FAIL with "ImportError: cannot import name 'delete_contact'"

- [ ] **Step 3: Implement the function**

Append to `app/db.py`:

```python
def delete_contact(conn: sqlite3.Connection, contact_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM contacts WHERE id = ? AND status = 'pending'", (contact_id,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest app/tests/test_db.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/tests/test_db.py
git commit -m "feat: delete_contact (pending-only, preserves send history)"
```

---

## Task 8: `.gitignore` — exclude uploaded media

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add the ignore rule**

Add `media/` to `.gitignore` (alongside the existing `data/`, `auth/` entries) — uploaded attachments are user data, not source.

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore uploaded media/ directory"
```

---

## Task 9: Campaign page — sidebar, selection, and campaign creation

**Files:**
- Create: `app/pages/1_Campaign.py`

No automated test: Streamlit pages are UI glue over already-tested `app/db.py` functions — verified by running the app, matching the existing project convention for every other page.

- [ ] **Step 1: Write the page skeleton**

```python
# app/pages/1_Campaign.py
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection, create_program, TEST_PROGRAM_NAME

st.title("Campaign")

conn = get_connection("data/silkroad.db")

# --- Overall stats across all real campaigns (excludes the hidden Test program) ---
totals = conn.execute(
    """
    SELECT c.status, COUNT(*) FROM contacts c
    JOIN programs p ON p.id = c.program_id
    WHERE p.name != ?
    GROUP BY c.status
    """,
    (TEST_PROGRAM_NAME,),
).fetchall()
totals_dict = dict(totals)
replied_total = conn.execute(
    """
    SELECT COUNT(*) FROM contacts c
    JOIN programs p ON p.id = c.program_id
    WHERE p.name != ? AND c.replied_at IS NOT NULL
    """,
    (TEST_PROGRAM_NAME,),
).fetchone()[0]
st.caption(
    f"All campaigns — sent: {totals_dict.get('sent', 0)} | "
    f"failed: {totals_dict.get('failed', 0)} | "
    f"pending: {totals_dict.get('pending', 0)} | "
    f"needs_review: {totals_dict.get('needs_review', 0)} | "
    f"replied: {replied_total}"
)

# --- Sidebar: campaign list ---
programs = conn.execute(
    "SELECT id, name, paused FROM programs WHERE name != ? ORDER BY name",
    (TEST_PROGRAM_NAME,),
).fetchall()

if "selected_program_id" not in st.session_state:
    st.session_state.selected_program_id = programs[0][0] if programs else None
if "creating_new_campaign" not in st.session_state:
    st.session_state.creating_new_campaign = not programs

with st.sidebar:
    st.subheader("Campaigns")
    for program_id, name, paused in programs:
        counts = dict(
            conn.execute(
                "SELECT status, COUNT(*) FROM contacts WHERE program_id = ? GROUP BY status",
                (program_id,),
            ).fetchall()
        )
        total = sum(counts.values())
        sent = counts.get("sent", 0)
        label = f"{'⏸ ' if paused else ''}{name} ({sent}/{total})"
        if st.button(label, key=f"select-{program_id}", use_container_width=True):
            st.session_state.selected_program_id = program_id
            st.session_state.creating_new_campaign = False
            st.rerun()

    if st.button("+ New Campaign", use_container_width=True):
        st.session_state.creating_new_campaign = True
        st.rerun()

# --- New campaign form ---
if st.session_state.creating_new_campaign:
    st.subheader("New Campaign")
    with st.form("new_campaign"):
        name = st.text_input("Campaign name")
        template_text = st.text_area(
            "Message template (use {{name}}, {{field}} placeholders)"
        )
        submitted = st.form_submit_button("Create campaign")
        if submitted:
            if not name or not template_text:
                st.error("Name and template are required.")
            elif name == TEST_PROGRAM_NAME:
                st.error(f"'{TEST_PROGRAM_NAME}' is a reserved name — choose another.")
            else:
                program_id = create_program(conn, name, template_text)
                st.session_state.selected_program_id = program_id
                st.session_state.creating_new_campaign = False
                st.success(f"Created campaign '{name}'.")
                st.rerun()
```

- [ ] **Step 2: Manually verify**

Run: `streamlit run app/streamlit_app.py` (or refresh if already running), navigate to "Campaign".
Expected: overall stats caption shows at the top; sidebar lists existing real campaigns (not "Test") with a `sent/total` fraction; clicking "+ New Campaign" shows the name+template form; creating one selects it and shows the success message.

- [ ] **Step 3: Commit**

```bash
git add app/pages/1_Campaign.py
git commit -m "feat: Campaign page skeleton — sidebar list, selection, campaign creation"
```

---

## Task 10: Campaign page — template editing and attachments

**Files:**
- Modify: `app/pages/1_Campaign.py`

- [ ] **Step 1: Add the workspace header and template/attachment section**

Update the import line to include the new `app.db` functions:

```python
from app.db import (
    get_connection,
    create_program,
    add_attachment,
    list_attachments,
    delete_attachment,
    TEST_PROGRAM_NAME,
)
```

Append to the end of `app/pages/1_Campaign.py`:

```python
# --- Workspace for the selected campaign ---
elif st.session_state.selected_program_id is not None:
    program_id = st.session_state.selected_program_id
    program = conn.execute(
        "SELECT id, name, template_text, paused FROM programs WHERE id = ?", (program_id,)
    ).fetchone()

    if program is None:
        st.session_state.selected_program_id = None
        st.rerun()

    _, name, template_text, paused = program
    st.subheader(name)

    if paused:
        if st.button("▶ Resume campaign"):
            conn.execute("UPDATE programs SET paused = 0 WHERE id = ?", (program_id,))
            conn.commit()
            st.rerun()
    else:
        if st.button("⏸ Pause campaign"):
            conn.execute("UPDATE programs SET paused = 1 WHERE id = ?", (program_id,))
            conn.commit()
            st.rerun()

    st.markdown("**Template**")
    st.caption("Placeholders: {{name}} plus any extra CSV columns for this campaign's contacts.")
    new_template = st.text_area("Message text", value=template_text, key=f"template-{program_id}")
    if st.button("Save template", key=f"save-template-{program_id}"):
        conn.execute(
            "UPDATE programs SET template_text = ? WHERE id = ?", (new_template, program_id)
        )
        conn.commit()
        st.success("Template saved.")
        st.rerun()

    st.markdown("**Attachments**")
    uploaded_files = st.file_uploader(
        "Add images or documents to send with every message",
        type=["png", "jpg", "jpeg", "pdf", "doc", "docx"],
        accept_multiple_files=True,
        key=f"upload-{program_id}",
    )
    if uploaded_files and st.button("Save attachments", key=f"save-attachments-{program_id}"):
        for f in uploaded_files:
            add_attachment(conn, program_id, f.name, f.read())
        st.success(f"Added {len(uploaded_files)} attachment(s).")
        st.rerun()

    attachments = list_attachments(conn, program_id)
    for a in attachments:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.write(f"{a['file_name']} ({a['media_type']})")
        with col2:
            if st.button("Remove", key=f"remove-attachment-{a['id']}"):
                delete_attachment(conn, a["id"])
                st.rerun()
```

**Note:** this `elif` attaches to the `if st.session_state.creating_new_campaign:` block from Task 9 — the workspace only renders when *not* in campaign-creation mode and a campaign is selected.

- [ ] **Step 2: Manually verify**

Refresh the Campaign page, select an existing campaign.
Expected: shows Pause/Resume, an editable template box that saves on click, and a file uploader; uploading an image and a PDF and clicking "Save attachments" lists both with a media type and a working "Remove" button.

- [ ] **Step 3: Commit**

```bash
git add app/pages/1_Campaign.py
git commit -m "feat: Campaign page — template editing and file attachments"
```

---

## Task 11: Campaign page — add contacts (CSV import / manual entry)

**Files:**
- Modify: `app/pages/1_Campaign.py`

- [ ] **Step 1: Add the "Add contacts" section**

Update the import line at the top of `app/pages/1_Campaign.py` to add:

```python
import csv
import io
```

and change the `app.db` import to also include `insert_contacts`:

```python
from app.db import (
    get_connection,
    create_program,
    add_attachment,
    list_attachments,
    delete_attachment,
    insert_contacts,
    TEST_PROGRAM_NAME,
)
from app.csv_import import parse_contacts_rows
```

Append to the end of the workspace block in `app/pages/1_Campaign.py` (still inside the `elif st.session_state.selected_program_id is not None:` branch):

```python
    st.markdown("**Add contacts**")
    tab_csv, tab_manual = st.tabs(["Import CSV", "Add one"])

    with tab_csv:
        uploaded_csv = st.file_uploader(
            "Contacts CSV (columns: phone, name, plus any extra fields)",
            type="csv",
            key=f"csv-{program_id}",
        )
        if uploaded_csv is not None:
            text = io.TextIOWrapper(uploaded_csv, encoding="utf-8")
            rows = list(csv.DictReader(text))
            valid, invalid = parse_contacts_rows(rows)

            st.write(f"{len(valid)} valid row(s), {len(invalid)} invalid row(s)")
            if valid:
                st.caption("Preview (first 3): " + str(valid[:3]))
            if invalid:
                st.caption("Rejected: " + str(invalid))

            if valid and st.button("Queue these contacts", key=f"queue-csv-{program_id}"):
                inserted, duplicates = insert_contacts(conn, program_id, valid)
                st.success(f"Queued {inserted} contact(s).")
                if duplicates:
                    st.warning(f"Skipped {len(duplicates)} duplicate(s): {duplicates}")
                st.rerun()

    with tab_manual:
        with st.form(f"manual-contact-{program_id}"):
            manual_phone = st.text_input("Phone (with country code, e.g. +77012345678)")
            manual_name = st.text_input("Name")
            manual_submitted = st.form_submit_button("Add contact")
            if manual_submitted:
                valid, invalid = parse_contacts_rows(
                    [{"phone": manual_phone, "name": manual_name}]
                )
                if invalid:
                    st.error(invalid[0]["error"])
                else:
                    inserted, duplicates = insert_contacts(conn, program_id, valid)
                    if inserted:
                        st.success(f"Added {valid[0]['name']} ({valid[0]['phone']}).")
                    if duplicates:
                        st.warning(f"{duplicates[0]} is already in this campaign.")
```

- [ ] **Step 2: Manually verify**

Refresh, select a campaign. Import a small CSV via the "Import CSV" tab and confirm the queued contacts appear; separately add one contact via the "Add one" tab.
Expected: both paths insert into the same campaign and go through the same phone-normalization/validation.

- [ ] **Step 3: Commit**

```bash
git add app/pages/1_Campaign.py
git commit -m "feat: Campaign page — CSV import and manual contact entry"
```

---

## Task 12: Campaign page — stats strip, contact table, bulk actions

**Files:**
- Modify: `app/pages/1_Campaign.py`

**Implementation note:** Streamlit's `st.data_editor` doesn't support per-row action buttons inside a table, so a single row's action is performed by checking exactly that one row and using the bulk-action buttons below the table — there's no separate "row action" UI distinct from bulk actions.

- [ ] **Step 1: Add the stats strip, contact table, and bulk actions**

Append to the end of the workspace block in `app/pages/1_Campaign.py`:

```python
    st.markdown("**Status**")
    counts = dict(
        conn.execute(
            "SELECT status, COUNT(*) FROM contacts WHERE program_id = ? GROUP BY status",
            (program_id,),
        ).fetchall()
    )
    replied_count = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE program_id = ? AND replied_at IS NOT NULL",
        (program_id,),
    ).fetchone()[0]
    delay_seconds = conn.execute("SELECT delay_seconds FROM settings WHERE id = 1").fetchone()[0]
    pending_count = counts.get("pending", 0)
    eta_minutes = round(pending_count * delay_seconds / 60, 1)
    st.caption(
        " | ".join(f"{status}: {count}" for status, count in counts.items())
        + f" | replied: {replied_count}"
        + (f" | ~{eta_minutes} min remaining (estimate)" if pending_count else "")
    )

    failed_count = counts.get("failed", 0)
    needs_review_count = counts.get("needs_review", 0)
    col1, col2, col3 = st.columns(3)
    with col1:
        if failed_count and st.button(f"Retry all {failed_count} failed", key=f"retry-all-{program_id}"):
            conn.execute(
                "UPDATE contacts SET status = 'pending', error_message = NULL "
                "WHERE program_id = ? AND status = 'failed'",
                (program_id,),
            )
            conn.commit()
            st.rerun()
    with col2:
        if needs_review_count and st.button(
            "Mark needs_review as sent", key=f"resolve-sent-{program_id}"
        ):
            conn.execute(
                "UPDATE contacts SET status = 'sent' WHERE program_id = ? AND status = 'needs_review'",
                (program_id,),
            )
            conn.commit()
            st.rerun()
    with col3:
        if needs_review_count and st.button(
            "Mark needs_review as pending", key=f"resolve-pending-{program_id}"
        ):
            conn.execute(
                "UPDATE contacts SET status = 'pending', error_message = NULL "
                "WHERE program_id = ? AND status = 'needs_review'",
                (program_id,),
            )
            conn.commit()
            st.rerun()

    all_contacts = conn.execute(
        "SELECT id, phone, name, status, sent_at, replied_at, error_message FROM contacts "
        "WHERE program_id = ? ORDER BY status, id",
        (program_id,),
    ).fetchall()

    if not all_contacts:
        st.info("No contacts yet — use Add contacts above.")
    else:
        status_filter = st.multiselect(
            "Filter by status",
            options=["pending", "sending", "sent", "failed", "needs_review"],
            default=[],
            key=f"status-filter-{program_id}",
        )
        rows_to_show = [
            {
                "id": c[0],
                "Select": False,
                "phone": c[1],
                "name": c[2],
                "status": c[3],
                "sent_at": c[4],
                "replied_at": c[5],
                "error_message": c[6],
            }
            for c in all_contacts
            if not status_filter or c[3] in status_filter
        ]
        edited_rows = st.data_editor(
            rows_to_show,
            hide_index=True,
            column_order=["Select", "phone", "name", "status", "sent_at", "replied_at", "error_message"],
            disabled=["phone", "name", "status", "sent_at", "replied_at", "error_message"],
            key=f"contact-table-{program_id}",
        )
        selected_ids = [r["id"] for r in edited_rows if r["Select"]]

        bcol1, bcol2 = st.columns(2)
        with bcol1:
            if selected_ids and st.button("Retry selected failed", key=f"retry-selected-{program_id}"):
                for contact_id in selected_ids:
                    conn.execute(
                        "UPDATE contacts SET status = 'pending', error_message = NULL "
                        "WHERE id = ? AND status = 'failed'",
                        (contact_id,),
                    )
                conn.commit()
                st.rerun()
        with bcol2:
            if selected_ids and st.button("Delete selected pending", key=f"delete-selected-{program_id}"):
                for contact_id in selected_ids:
                    delete_contact(conn, contact_id)
                conn.commit()
                st.rerun()
```

Add `delete_contact` to the `app.db` import at the top of the file:

```python
from app.db import (
    get_connection,
    create_program,
    add_attachment,
    list_attachments,
    delete_attachment,
    insert_contacts,
    delete_contact,
    TEST_PROGRAM_NAME,
)
```

- [ ] **Step 2: Manually verify**

Refresh, select a campaign with a mix of contact statuses (queue a couple, mark one failed via a direct DB update if needed to test retry).
Expected: stats caption shows counts + replied + ETA; contact table shows all columns with a working "Select" checkbox; checking a failed row and clicking "Retry selected failed" resets it to pending; checking a pending row and clicking "Delete selected pending" removes it; a checked `sent` row is unaffected by either bulk button (status guard in the SQL / `delete_contact`).

- [ ] **Step 3: Commit**

```bash
git add app/pages/1_Campaign.py
git commit -m "feat: Campaign page — stats strip, contact table, bulk actions"
```

---

## Task 13: Connection page — rename from Status, relocate test-message form

**Files:**
- Create: `app/pages/2_Connection.py`
- Delete: `app/pages/3_Status.py`

- [ ] **Step 1: Write the new page**

`app/pages/2_Connection.py` keeps the connection/heartbeat/QR/disconnect logic from the current `app/pages/3_Status.py` verbatim (heartbeat check, `worker_alive`/`connected` states, QR display, "Disconnect WhatsApp" button), drops the per-campaign programs loop entirely (that moved to the Campaign page in Tasks 9–12), and adds the "Send a test message" form relocated from Settings:

```python
# app/pages/2_Connection.py
import base64
import os
import sys
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection, create_program, insert_contacts, TEST_PROGRAM_NAME
from app.csv_import import parse_contacts_rows

STALE_AFTER_SECONDS = 120
TEST_PROGRAM_TEMPLATE = "Hi {{name}}, this is a test message from the Silkroad WhatsApp Sender."

st.title("Connection")

conn = get_connection("data/silkroad.db")

heartbeat = conn.execute(
    "SELECT last_seen, qr_code, connected FROM worker_heartbeat WHERE id = 1"
).fetchone()
last_seen, qr_code, connected = heartbeat if heartbeat else (None, None, 0)

worker_alive = False

if not last_seen:
    st.error(
        "Worker has never checked in. Start it with `pm2 restart silkroad-whatsapp-worker` "
        "or `node worker/index.js`."
    )
else:
    try:
        last_seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - last_seen_dt).total_seconds()
    except ValueError:
        age_seconds = None

    worker_alive = age_seconds is not None and age_seconds <= STALE_AFTER_SECONDS

    if age_seconds is None:
        st.caption(f"Worker last seen: {last_seen}")
    elif not worker_alive:
        st.error(
            f"Worker may not be running — last seen {int(age_seconds)}s ago. "
            "Start it with `pm2 restart silkroad-whatsapp-worker` or `node worker/index.js`."
        )
    elif connected:
        st.success(f"WhatsApp connected — worker last seen {int(age_seconds)}s ago.")
    else:
        st.warning(
            f"WhatsApp not connected — worker last seen {int(age_seconds)}s ago. "
            "Scan the QR code below to link a number."
        )

if worker_alive and connected:
    if st.button("Disconnect WhatsApp"):
        conn.execute("UPDATE worker_heartbeat SET disconnect_requested = 1 WHERE id = 1")
        conn.commit()
        st.info(
            "Disconnect requested — the worker will log out and show a new QR code "
            "in a few seconds."
        )
        st.rerun()

if qr_code:
    st.subheader("Scan this QR code to connect WhatsApp")
    _, b64data = qr_code.split(",", 1)
    st.image(base64.b64decode(b64data), width=300)
    st.caption("WhatsApp app -> Settings -> Linked Devices -> Link a Device")
    if st.button("Refresh"):
        st.rerun()

st.divider()
st.subheader("Send a test message")
st.caption(
    "Quickly queue a one-off message to verify the pipeline end-to-end, without "
    "hand-writing a script. This reuses (or creates) a dedicated 'Test' program with a "
    "simple default template, and queues one contact as pending — the worker picks it "
    "up on its next poll, same as any other contact."
)

with st.form("test_message_form"):
    test_phone = st.text_input("Phone (with country code, e.g. +77012345678)")
    test_name = st.text_input("Name", value="there")
    test_submitted = st.form_submit_button("Queue test message")

    if test_submitted:
        valid, invalid = parse_contacts_rows([{"phone": test_phone, "name": test_name or "there"}])
        if invalid:
            st.error(invalid[0]["error"])
        else:
            existing = conn.execute(
                "SELECT id FROM programs WHERE name = ?", (TEST_PROGRAM_NAME,)
            ).fetchone()
            test_program_id = existing[0] if existing else create_program(
                conn, TEST_PROGRAM_NAME, TEST_PROGRAM_TEMPLATE
            )
            conn.execute("UPDATE programs SET paused = 0 WHERE id = ?", (test_program_id,))
            conn.commit()
            inserted, duplicates = insert_contacts(conn, test_program_id, valid)
            if inserted:
                st.success(
                    f"Queued a test message to {valid[0]['phone']} in the '{TEST_PROGRAM_NAME}' "
                    "program. Check pm2 logs or the DB to watch it get picked up."
                )
            if duplicates:
                st.warning(
                    f"{duplicates[0]} is already queued in the '{TEST_PROGRAM_NAME}' program "
                    "— use a different number, or wait for it to be sent."
                )
```

- [ ] **Step 2: Delete the old Status page**

```bash
git rm app/pages/3_Status.py
```

- [ ] **Step 3: Manually verify**

Refresh the app.
Expected: sidebar shows "Connection" (no more "Status"); the page shows the same worker/QR/disconnect behavior as before, plus the "Send a test message" form at the bottom; queuing a test message succeeds and does not appear anywhere on the Campaign page's sidebar.

- [ ] **Step 4: Commit**

```bash
git add app/pages/2_Connection.py
git commit -m "feat: rename Status to Connection, relocate test-message form here"
```

---

## Task 14: Settings page — renumber, remove test-message section

**Files:**
- Create: `app/pages/3_Settings.py`
- Delete: `app/pages/4_Settings.py`

- [ ] **Step 1: Write the new page**

`app/pages/3_Settings.py` is the current `app/pages/4_Settings.py` with the entire "Send a test message" section (everything from `st.divider()` through the end of the file) removed, since it moved to the Connection page in Task 13:

```python
# app/pages/3_Settings.py
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection

st.title("Settings")

conn = get_connection("data/silkroad.db")

row = conn.execute(
    "SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1"
).fetchone()
delay_seconds, jitter_seconds, daily_cap, dry_run = row

st.caption(
    "Delay, jitter, and daily cap apply on the next polling cycle — no restart needed. "
    "Turning dry run OFF (to start actually sending) requires restarting the worker "
    "process (e.g. `pm2 restart silkroad-whatsapp-worker`) so it can connect to WhatsApp."
)

with st.form("settings_form"):
    new_dry_run = st.checkbox("Dry run (log instead of actually sending)", value=bool(dry_run))
    new_delay = st.number_input("Delay between sends (seconds)", min_value=1, value=delay_seconds, step=1)
    new_jitter = st.number_input("Random jitter added to delay (seconds)", min_value=0, value=jitter_seconds, step=1)
    has_cap = st.checkbox("Set a daily send cap", value=daily_cap is not None)
    new_cap = st.number_input(
        "Daily cap (max sends per day, across all programs)",
        min_value=0,
        value=daily_cap if daily_cap is not None else 0,
        step=1,
        disabled=not has_cap,
    )
    submitted = st.form_submit_button("Save settings")

    if submitted:
        cap_value = new_cap if has_cap else None
        conn.execute(
            "UPDATE settings SET dry_run = ?, delay_seconds = ?, jitter_seconds = ?, daily_cap = ? WHERE id = 1",
            (int(new_dry_run), new_delay, new_jitter, cap_value),
        )
        conn.commit()
        st.success("Settings saved.")
        st.rerun()
```

- [ ] **Step 2: Delete the old Settings page**

```bash
git rm app/pages/4_Settings.py
```

- [ ] **Step 3: Manually verify**

Refresh the app.
Expected: sidebar order is now Campaign, Connection, Settings; Settings has no "Send a test message" section (that's on Connection now); dry_run/delay/jitter/cap still save correctly.

- [ ] **Step 4: Commit**

```bash
git add app/pages/3_Settings.py
git commit -m "feat: renumber Settings page, remove relocated test-message section"
```

---

## Task 15: Remove the old Programs and Upload Contacts pages

**Files:**
- Delete: `app/pages/1_Programs.py`
- Delete: `app/pages/2_Upload_Contacts.py`

- [ ] **Step 1: Delete both files**

```bash
git rm app/pages/1_Programs.py app/pages/2_Upload_Contacts.py
```

- [ ] **Step 2: Manually verify**

Refresh the app.
Expected: sidebar shows exactly 3 pages — Campaign, Connection, Settings. No "Programs" or "Upload Contacts" entries remain.

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove Programs and Upload Contacts pages, superseded by Campaign"
```

---

## Task 16: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run all automated tests**

Run: `npm test`
Expected: PASS, all suites (db, template, heartbeat, queue, index)

Run: `.venv/bin/pytest app/tests/ -v`
Expected: PASS, all tests (phone, csv_import, db)

- [ ] **Step 2: Restart both processes**

```bash
pm2 restart silkroad-whatsapp-worker
pkill -f "streamlit run app/streamlit_app.py"
```

Then relaunch Streamlit the same way it's been run all session (`PYTHONFAULTHANDLER=1 .venv/bin/streamlit run app/streamlit_app.py --server.headless true --server.port 8501`, backgrounded).

- [ ] **Step 3: Walk the golden path manually**

1. On the Campaign page, create a new campaign with a template using `{{name}}`.
2. Attach one image and one PDF via the template section; confirm both list correctly and "Remove" works on one of them, leaving the other.
3. Add one contact via "Add one", and a few more via CSV import.
4. Confirm the stats strip and sidebar `sent/total` fraction update as expected.
5. With `dry_run` still on (Settings page), confirm pm2 logs show `[DRY RUN]` lines mentioning the remaining attachment's filename.
6. Turn `dry_run` off in Settings, restart the worker, confirm the campaign's contacts actually send (check `sqlite3`/python query for `status='sent'`), and that the attachment/document actually arrives on the receiving phone.
7. From the receiving phone, reply to the message; confirm `replied_at` gets stamped (per Task 4's manual check) and the Campaign page's stats strip reflects it.
8. On the Connection page, confirm "Send a test message" still works and does not create a visible campaign row.
9. Select two pending contacts via checkboxes on the Campaign page and use "Delete selected pending" — confirm both disappear; confirm a `sent` contact's checkbox has no effect on that button.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: campaign workspace end-to-end verification complete" --allow-empty
```
