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
