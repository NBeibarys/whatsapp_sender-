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
    CHECK(status IN ('pending','sending','sent','failed','needs_review')),
  rendered_message TEXT,
  -- Set when the send is ATTEMPTED (socket accepted the payload) and refreshed
  -- when WhatsApp confirms it. Truth about the message lives in delivery_state.
  sent_at TEXT,
  error_message TEXT,
  replied_at TEXT,
  -- Truthful delivery tracking (Tier 1). status stays the workflow state;
  -- delivery_state is what WhatsApp actually told us about the message.
  wa_message_id TEXT,
  delivery_state TEXT,  -- pending_ack | server_ack | delivered | read | rejected
  ack_error TEXT,
  delivered_at TEXT,
  read_at TEXT,
  UNIQUE(program_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_contacts_wa_message_id ON contacts(wa_message_id);

CREATE TABLE IF NOT EXISTS program_attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  program_id INTEGER NOT NULL REFERENCES programs(id),
  file_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  media_type TEXT NOT NULL CHECK(media_type IN ('image', 'document')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  delay_seconds INTEGER NOT NULL DEFAULT 60,
  jitter_seconds INTEGER NOT NULL DEFAULT 0,
  daily_cap INTEGER,
  dry_run INTEGER NOT NULL DEFAULT 1,
  -- Sending window, 'HH:MM' in send_timezone (IANA name).
  -- Either bound NULL means "no window": send at any hour.
  send_window_start TEXT,
  send_window_end TEXT,
  send_timezone TEXT
);
INSERT OR IGNORE INTO settings (id, delay_seconds, jitter_seconds, daily_cap, dry_run)
  VALUES (1, 60, 15, NULL, 1);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_seen TEXT,
  qr_code TEXT,
  disconnect_requested INTEGER NOT NULL DEFAULT 0,
  -- When the operator asked (UTC ISO-8601). The worker refuses any request
  -- older than its TTL: an unstamped or long-stale flag is one that outlived
  -- the click that made it (web restart, worker restart, long backoff), and
  -- destroying a live session in a context the operator has forgotten about
  -- is exactly the accident this column exists to prevent.
  disconnect_requested_at TEXT,
  connected INTEGER NOT NULL DEFAULT 0,
  -- Set when the worker stops sending itself after repeated WhatsApp
  -- rejections. Cleared from the app (POST /api/sending/resume).
  halted_at TEXT,
  halt_reason TEXT
);
INSERT OR IGNORE INTO worker_heartbeat (id, last_seen) VALUES (1, NULL);

-- Inbound replies (body kept, not just a timestamp on contacts).
CREATE TABLE IF NOT EXISTS replies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER REFERENCES contacts(id),
  phone TEXT NOT NULL,
  body TEXT,
  received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_replies_contact_id ON replies(contact_id);
