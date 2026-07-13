# Campaign Workspace Redesign

**Goal:** Consolidate the Streamlit control panel from 4 pages (Programs, Upload Contacts, Status, Settings) into 3, with a single Apollo-style "Campaign" workspace as the main page. Add multi-file template attachments, bulk contact actions, basic statistics, and reply detection. Explicitly drop (as too risky / too complex for now): WhatsApp interactive buttons, link-click tracking, multi-step sequences.

**Why:** The current split (create a program on one page, upload contacts on another, watch status on a third mixed with WhatsApp connection state) requires jumping between pages to do one piece of work — create a campaign, load contacts, send. A single workspace mirrors how the operator actually thinks about a campaign.

---

## Page structure (3 pages, down from 4)

| Old | New |
|---|---|
| `1_Programs.py`, `2_Upload_Contacts.py`, campaign-status half of `3_Status.py` | `1_Campaign.py` |
| Connection-status half of `3_Status.py` (heartbeat, QR, connect/disconnect) | `2_Connection.py` |
| `4_Settings.py`, minus the "Send a test message" section which moves to `2_Connection.py` | `3_Settings.py` |

The "Test" program (used for one-off connection-test sends) is never shown as a campaign row anywhere in the Campaign page — its only UI surface is the "Send test message" control on the Connection page. It keeps working exactly as it does today (reuses/creates a program named `Test`, auto-unpaused on every send) — this part is unchanged, just relocated.

---

## Campaign page (`1_Campaign.py`)

### Overall stats (top of page)
A single-line summary across **all** campaigns (excluding the hidden `Test` program): total sent / failed / pending / needs_review, and total replied.

### Sidebar — campaign list
- One row per campaign (program), excluding `Test`: name, a paused badge when `paused=1`, and a `sent/total` progress fraction (e.g. `34/120`).
- "+ New Campaign" control at the bottom, opens a one-step form (name + initial template text) inline in the workspace area. A campaign cannot be created without a template — the worker has nothing to render otherwise.
- Clicking a row loads that campaign into the workspace on the right. Selection persists via Streamlit session state.

### Workspace — selected campaign
**Header:** campaign name, a prominent Pause/Resume toggle (same semantics as today: paused programs are skipped entirely by the worker's `getNextPendingContact` query).

**Template section:**
- Editable text area for `template_text`, with a caption/hint listing available placeholders (`{{name}}` plus any extra CSV columns seen for this campaign's contacts).
- Save button writes `UPDATE programs SET template_text = ? WHERE id = ?`.
- Multi-file uploader accepting images (jpg/png) and documents (pdf and other common document types) via `st.file_uploader(..., accept_multiple_files=True)`. Each uploaded file is copied to `media/<program_id>/<uuid>-<original_filename>` and recorded in a new `program_attachments` table, with `media_type` set to `'image'` when the extension is `.jpg`/`.jpeg`/`.png`, else `'document'`. Uploaded files are listed below the uploader with a per-file remove button (deletes the DB row and the file on disk).

**Add contacts section:** one block, two tabs:
- **Import CSV** — same as today's Upload Contacts page: file uploader, `parse_contacts_rows`, preview of valid/invalid rows, "Queue these contacts" button calling `insert_contacts`.
- **Add one** — same as today's manual-entry form: phone + name (+ optional extra fields), validated through the same `parse_contacts_rows` path so normalization/validation is identical between the two entry methods.

**Stats strip:** counts by status (pending/sending/sent/failed/needs_review) for this campaign, a replied count, and a rough ETA (`pending_count × settings.delay_seconds`, shown as e.g. "~12 min remaining" — this is an estimate, not a guarantee, and should be labeled as such since actual pacing includes jitter and interleaving with other non-paused campaigns).

**Contact table:**
- Checkbox column for bulk-select (`st.data_editor` with a boolean column, or a multiselect keyed by contact id — implementation detail for the plan) plus columns: phone, name, status, sent_at, replied_at, error_message (truncated inline, not hidden behind a click).
- Status filter multiselect, options include `pending`, `sending`, `sent`, `failed`, `needs_review` (unchanged from today, `needs_review` already existed).
- **Row actions:** retry (failed rows only — resets to `pending`, clears `error_message`), delete (pending rows only — hard delete from `contacts`; sending/sent/failed/needs_review rows cannot be deleted, to preserve history and avoid deleting a row the worker currently holds).
- **Bulk actions** (act on checked rows, respecting the same status restrictions as the row-level actions): "Retry selected failed", "Delete selected pending".
- Existing campaign-level "Retry all failed" and "Mark needs_review as sent / as pending" buttons carry over unchanged from the current Status page.

---

## Connection page (`2_Connection.py`)

Renamed from `3_Status.py`. Keeps, unchanged from today's implementation:
- Worker heartbeat / staleness check (never-checked-in / stale / running messaging).
- Explicit connected/disconnected state (`worker_heartbeat.connected`), QR code display, "Disconnect WhatsApp" button (`disconnect_requested` flag).

Adds:
- The "Send a test message" form, moved verbatim from `4_Settings.py` (phone + name inputs, queues into the `Test` program, auto-unpausing it). Placed logically right after the connection/QR section, since it's the natural next step after pairing.

## Settings page (`3_Settings.py`)

Unchanged except the "Send a test message" section is removed (moved to Connection page). Still covers dry_run, delay_seconds, jitter_seconds, daily_cap.

---

## Data model changes

```sql
ALTER TABLE contacts ADD COLUMN replied_at TEXT;

CREATE TABLE program_attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  program_id INTEGER NOT NULL REFERENCES programs(id),
  file_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  media_type TEXT NOT NULL CHECK(media_type IN ('image', 'document')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

`schema.sql` gets both changes for fresh installs; the live `data/silkroad.db` needs the same manual migration applied (as done earlier this session for `worker_heartbeat`'s new columns). A new `media/` directory holds uploaded files, added to `.gitignore` (uploaded contact attachments are user data, not source).

## Worker changes

**Sending with attachments** (`worker/index.js` `processContact`, `worker/baileys.js`):
- If a program has attachments (`program_attachments` rows, ordered by `id`), the rendered template text is sent as the **caption** on the first attachment (`sock.sendMessage(jid, { image: { url }, caption } )` or the `document` equivalent based on `media_type`), then remaining attachments are sent as separate messages (no caption) immediately after, before the normal per-contact `delay_seconds + jitter` wait.
- If a program has no attachments, behavior is unchanged: plain text message.
- `dry_run` mode logs the attachment filenames alongside the rendered text instead of sending.
- All attachment sends for one contact are treated as a single logical "send" — if any part fails, the whole contact is marked `failed` with the error (no partial-success state to track).

**Reply detection** (`worker/baileys.js`):
- After `connect()` resolves, register `sock.ev.on('messages.upsert', ...)`.
- For each incoming message where `key.fromMe` is false, extract the sender's phone from `key.remoteJid` (strip `@s.whatsapp.net`, prefix `+`), and call a new `markReplied(db, phone)` in `worker/queue.js`: `UPDATE contacts SET replied_at = ? WHERE phone = ? AND status = 'sent' AND replied_at IS NULL`. This can match more than one row if the same phone exists across multiple campaigns — both get marked, since there's no reliable way to attribute a freeform reply to one specific campaign, and that ambiguity is an accepted limitation, not a bug to solve here.
- The listener is only registered when `sock` exists (i.e., not in `dry_run` mode).

---

## Explicitly out of scope

- WhatsApp interactive/quick-reply buttons (WhatsApp's own restrictions on the unofficial multi-device protocol make delivery unreliable; not worth building).
- Link-click tracking (would require a new public-facing redirect server; dropped along with buttons).
- Multi-step sequences, saved filters/segments, per-contact activity timelines (Apollo features that don't fit a single-operator, low-volume, paced-sending tool).

## Testing / verification approach

- `app/db.py` additions (attachment CRUD, `delete_contact`, bulk operations) get `pytest` unit tests following the existing pattern in `app/tests/test_db.py`.
- `worker/queue.js` additions (`markReplied`) get `node --test` unit tests following the existing pattern in `worker/tests/queue.test.js`.
- Attachment sending and reply detection are integration concerns (real Baileys socket) — verified manually against the live connected WhatsApp session, same as the existing manual-verification approach for `worker/baileys.js` and `worker/index.js`.
- Streamlit pages remain untested by automation (existing project convention) — verified by running the app.
