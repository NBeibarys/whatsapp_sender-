const Database = require('better-sqlite3');

function openDb(filePath) {
  const db = new Database(filePath);
  db.pragma('journal_mode = WAL');
  db.pragma('busy_timeout = 5000');
  db.pragma('foreign_keys = ON');
  return db;
}

module.exports = { openDb };
