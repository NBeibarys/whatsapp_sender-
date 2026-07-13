function getSettings(db) {
  return db
    .prepare('SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1')
    .get();
}

module.exports = { getSettings };
