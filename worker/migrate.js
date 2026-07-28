/**
 * Additive, idempotent schema migration (Node side).
 *
 * Either process can open the DB first (the worker is spawned by the web app,
 * but it also runs standalone), so the same migration exists in app/db.py.
 * Both are additive-only — no table rebuilds, no CHECK constraint changes —
 * so running them in any order, any number of times, is safe.
 *
 * Keep this column list and the one in app/db.py in sync.
 */

const ADDED_COLUMNS = {
  contacts: {
    wa_message_id: 'TEXT',
    delivery_state: 'TEXT',
    ack_error: 'TEXT',
    delivered_at: 'TEXT',
    read_at: 'TEXT',
  },
  worker_heartbeat: {
    halted_at: 'TEXT',
    halt_reason: 'TEXT',
  },
  settings: {
    send_window_start: 'TEXT',
    send_window_end: 'TEXT',
    send_timezone: 'TEXT',
  },
};

const CREATED_TABLES = {
  replies: `
    CREATE TABLE replies (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      contact_id INTEGER REFERENCES contacts(id),
      phone TEXT NOT NULL,
      body TEXT,
      received_at TEXT NOT NULL
    )
  `,
};

const CREATED_INDEXES = {
  idx_contacts_wa_message_id: 'CREATE INDEX idx_contacts_wa_message_id ON contacts(wa_message_id)',
  idx_replies_contact_id: 'CREATE INDEX idx_replies_contact_id ON replies(contact_id)',
};

function tableExists(db, table) {
  return Boolean(
    db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(table)
  );
}

function indexExists(db, name) {
  return Boolean(
    db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?").get(name)
  );
}

function existingColumns(db, table) {
  return new Set(db.pragma(`table_info(${table})`).map((row) => row.name));
}

/**
 * Bring an existing database up to the current schema. Returns the list of
 * changes applied (empty when the DB was already current).
 */
function applyMigrations(db) {
  const applied = [];

  for (const [table, columns] of Object.entries(ADDED_COLUMNS)) {
    if (!tableExists(db, table)) continue; // schema.sql creates it complete
    const present = existingColumns(db, table);
    for (const [column, type] of Object.entries(columns)) {
      if (present.has(column)) continue;
      db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${type}`);
      applied.push(`${table}.${column}`);
    }
  }

  for (const [table, sql] of Object.entries(CREATED_TABLES)) {
    if (tableExists(db, table)) continue;
    db.exec(sql);
    applied.push(`table ${table}`);
  }

  for (const [name, sql] of Object.entries(CREATED_INDEXES)) {
    if (indexExists(db, name)) continue;
    db.exec(sql);
    applied.push(`index ${name}`);
  }

  return applied;
}

module.exports = { applyMigrations, ADDED_COLUMNS };
