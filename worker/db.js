const Database = require('better-sqlite3');

function openDb(filePath) {
  const db = new Database(filePath);
  db.pragma('journal_mode = WAL');
  db.pragma('busy_timeout = 5000');
  return db;
}

module.exports = { openDb };
