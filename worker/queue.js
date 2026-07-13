function getSettings(db) {
  return db
    .prepare('SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1')
    .get();
}

function markSending(db, contactId) {
  db.prepare("UPDATE contacts SET status = 'sending' WHERE id = ?").run(contactId);
}

function markSent(db, contactId, renderedMessage) {
  db.prepare(
    "UPDATE contacts SET status = 'sent', rendered_message = ?, sent_at = ? WHERE id = ?"
  ).run(renderedMessage, new Date().toISOString(), contactId);
}

function markFailed(db, contactId, errorMessage) {
  db.prepare("UPDATE contacts SET status = 'failed', error_message = ? WHERE id = ?").run(
    errorMessage,
    contactId
  );
}

module.exports = { getSettings, markSending, markSent, markFailed };
