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
  sent_at TEXT,
  error_message TEXT,
  replied_at TEXT,
  UNIQUE(program_id, phone)
);

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
  dry_run INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO settings (id, delay_seconds, jitter_seconds, daily_cap, dry_run)
  VALUES (1, 60, 0, NULL, 1);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_seen TEXT,
  qr_code TEXT,
  disconnect_requested INTEGER NOT NULL DEFAULT 0,
  connected INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO worker_heartbeat (id, last_seen) VALUES (1, NULL);
