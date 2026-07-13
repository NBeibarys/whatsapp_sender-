# Silkroad WhatsApp Sender — Design

## Purpose

Silkroad Innovation Hub needs an in-house replacement for airi.chat to send bulk WhatsApp
campaign messages (deadline reminders, program updates) to startups participating in its
programs. Recipients have consented to be contacted as part of applying to/joining a program.
Multiple programs/cohorts run over time, each with its own contact list and message template.

## Scope

WhatsApp only. Telegram (mentioned in earlier discussion as a separate, policy-clean channel
via the official Bot API) is out of scope for this spec and would get its own design later.

## Explicit, already-made decision (not open for re-litigation in this spec)

This tool sends WhatsApp messages via **Baileys**, an unofficial/reverse-engineered Node.js
library for the WhatsApp Web multi-device protocol, rather than Meta's official WhatsApp
Cloud API. This is a deliberate choice by the product owner, who has accepted:

- Violating WhatsApp's Terms of Service for the number in use
- Real risk of the number being banned, with no recourse
- Responsibility for ensuring recipients have actually consented to contact

The pacing/rate-limiting design below (fixed delay, single serialized send path, daily cap)
exists to reduce — not eliminate — that ban risk. It is a deliberate throughput ceiling, not a
limitation to be optimized around.

## Architecture

Two long-running processes share state through SQLite (`data/silkroad.db`, WAL mode so reads
from Streamlit don't block writes from the worker):

- **Node.js worker** (pm2-managed, always on): owns the single persistent Baileys session
  (QR-scanned once, auth persisted to disk under `auth/`). Polls SQLite for contacts with
  status `pending`, sends one at a time through the one WhatsApp session at a configurable
  fixed delay (+ small jitter), writes status back. All state lives in the DB — no in-memory
  queue — so a worker crash/restart is safe by construction; it simply resumes polling.
- **Streamlit app** (Python): the control panel staff actually use. Creates programs, uploads
  CSVs, validates/previews, shows live status, and exposes pause/cancel per program. Never
  talks to WhatsApp directly.

Sends are **deliberately serialized** through the one Baileys socket. Message sends are
data-independent ("embarrassingly parallel" in the abstract), but the actual bottleneck here
is the single WhatsApp session/number, not CPU or I/O — parallelizing dispatch would multiply
burst rate and directly work against the ban-mitigation pacing. Only the non-send steps that
share no external resource (CSV parsing, phone validation/normalization, template rendering)
are naturally concurrency-safe; at ~300 rows/campaign this has no visible effect on runtime and
is not a design requirement, just a note that nothing about that code needs artificial
serialization.

## Data model (SQLite)

**`programs`**
- `id`, `name`, `template_text` (with `{{name}}` / `{{field}}` placeholders), `created_at`
- `paused` (bool) — staff-controlled stop button for a program's queue

**`contacts`**
- `id`, `program_id`, `phone` (E.164, TEXT — never numeric), `name`, `extra_fields` (JSON text
  for any additional CSV columns), `status` (`pending` / `sending` / `sent` / `failed` /
  `needs_review` / `skipped`), `rendered_message` (snapshot of the actual text sent, filled in
  at send time), `sent_at`, `error_message`
- `UNIQUE(program_id, phone)` — blocks duplicate CSV uploads from creating duplicate sends

**`settings`** (global, editable from Streamlit, read by the worker)
- `delay_seconds` (default: 60), `jitter_seconds` (default: 0), `daily_cap` (default: none),
  `dry_run` (default: true). Defaults are a starting point, not a fixed rule — staff adjust
  from the Streamlit settings panel. At the default 60s delay, a 300-contact campaign takes
  ~5 hours to clear; setting a `daily_cap` spreads it across multiple days instead.

**`worker_heartbeat`**
- `last_seen` timestamp, updated by the worker every polling cycle; surfaced in Streamlit so
  staff can tell if the WhatsApp session has silently logged out while the queue sits full

## Send lifecycle (crash-safety)

Send-then-write is not atomic, so status transitions are:

1. Worker picks a `pending` row → marks it `sending` *before* calling Baileys
2. On success → `sent`, with `sent_at` and `rendered_message` filled in
3. On failure → `failed`, with `error_message`
4. On worker restart, any row still in `sending` is ambiguous (did it send or not?) and is
   marked `needs_review` — never auto-resent. A human resolves these manually. At-most-once
   delivery is the correct default for campaign messages; a missed send is recoverable, a
   duplicate send to 300 people is not.

Failed sends are **never auto-retried**. Staff retry manually from the Streamlit status page,
one program at a time.

## Program isolation

One shared WhatsApp number/session serves all programs. Isolation is at the config/data level
only (separate template + separate contact list per program), not separate sessions. The
worker polls across programs **round-robin**, not strict FIFO — otherwise a large program's
backlog would starve a second program queued behind it. A `paused` flag per program lets staff
stop a program's queue independently (e.g. after spotting a template typo mid-send) without
affecting others.

## Validation & error handling

- Phone numbers normalized to E.164 and validated at CSV upload time, before ever reaching the
  queue; invalid rows are shown separately and never inserted as contacts
- Distinguish "number not on WhatsApp" (checked via Baileys' `onWhatsApp` lookup before
  sending) from transient send errors, in `error_message`
- `data/` (including the Baileys `auth/` session folder) should be backed up — losing it means
  re-scanning the QR code and potentially losing the "warm" number

## Cross-language SQLite notes (Python + Node sharing one file)

- Node side (`better-sqlite3`): explicitly set `PRAGMA busy_timeout = 5000` — the default is 0,
  which surfaces as immediate `SQLITE_BUSY` errors during concurrent Streamlit writes
- Python side: keep CSV-import writes in one short transaction; avoid holding open transactions
- Avoid `pandas.to_sql` type inference for phone numbers — int64 inference strips leading `+`;
  insert phones as TEXT explicitly. Timestamps as ISO-8601 strings. `extra_fields` as JSON text.
- WAL mode requires the DB file to live on local disk, never a network mount

## Project structure

```
silkroad-whatsapp-sender/
  auth/                      # Baileys session, QR-scanned once, persisted across restarts
  data/
    silkroad.db              # SQLite, WAL mode
  worker/
    index.js                 # polling loop, sending-state transition, heartbeat
    db.js                    # better-sqlite3 setup, busy_timeout, WAL pragmas
    baileys.js               # connection, auth state, send + onWhatsApp check
  app/
    streamlit_app.py         # programs, upload, validation, status, pause/cancel
    db.py                    # Python write transactions, type-safe inserts
  schema.sql                 # table definitions (status enum incl. sending/needs_review,
                              # unique constraint, rendered_message column)
  docs/
    superpowers/specs/       # this file
```

## Testing path

1. `dry_run = true` in settings: worker logs what it would send instead of calling Baileys
2. Send to 2-3 real test numbers with `dry_run = false`
3. Full campaign

## Out of scope for this spec

- Telegram integration (separate spec later)
- Auto-retry of failed sends
- Multiple WhatsApp numbers/sessions (per-program or otherwise) — explicitly declined in favor
  of one shared number across all programs
- Any tooling whose purpose is evading WhatsApp's anti-abuse/spam detection (warm-up schedules,
  human-mimicry randomization tuned to detection thresholds, message-variant rotation for
  defeating text-similarity filters). The fixed delay + jitter in `settings` is ordinary rate
  limiting that staff configure themselves; it is not designed or tuned against any known
  detection mechanism.
