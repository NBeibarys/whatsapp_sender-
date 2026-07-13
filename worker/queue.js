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

function recoverStuckSends(db) {
  const result = db
    .prepare("UPDATE contacts SET status = 'needs_review' WHERE status = 'sending'")
    .run();
  return result.changes;
}

function getNextPendingContact(db) {
  return db
    .prepare(
      `
    SELECT c.* FROM contacts c
    JOIN programs p ON p.id = c.program_id
    WHERE c.status = 'pending' AND p.paused = 0
    ORDER BY
      (SELECT MAX(sent_at) FROM contacts c2 WHERE c2.program_id = c.program_id AND c2.status = 'sent') IS NULL DESC,
      (SELECT MAX(sent_at) FROM contacts c2 WHERE c2.program_id = c.program_id AND c2.status = 'sent') ASC,
      c.id ASC
    LIMIT 1
  `
    )
    .get();
}

function countSentToday(db) {
  const row = db
    .prepare("SELECT COUNT(*) AS count FROM contacts WHERE status = 'sent' AND date(sent_at) = date('now')")
    .get();
  return row.count;
}

function markReplied(db, phone) {
  const result = db
    .prepare(
      "UPDATE contacts SET replied_at = ? WHERE phone = ? AND status = 'sent' AND replied_at IS NULL"
    )
    .run(new Date().toISOString(), phone);
  return result.changes;
}

module.exports = {
  getSettings,
  markSending,
  markSent,
  markFailed,
  recoverStuckSends,
  getNextPendingContact,
  countSentToday,
  markReplied,
};
