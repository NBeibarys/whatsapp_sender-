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
