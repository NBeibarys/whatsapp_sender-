const { ACK_TIMEOUT_MESSAGE } = require('./ackErrors');

// A message is only truly "sent" once WhatsApp acknowledges it. Rows sit in
// status='sending' / delivery_state='pending_ack' until an ack arrives; the
// sweep below fails the ones that never get one.
const ACK_TIMEOUT_MS = 60000;

function getSettings(db) {
  return db
    .prepare(
      `SELECT delay_seconds, jitter_seconds, daily_cap, dry_run,
              send_window_start, send_window_end, send_timezone
       FROM settings WHERE id = 1`
    )
    .get();
}

function markSending(db, contactId) {
  db.prepare("UPDATE contacts SET status = 'sending' WHERE id = ?").run(contactId);
}

/**
 * Claim the contact BEFORE touching the socket.
 *
 * If the process dies between the socket call and markSendAttempted, the row
 * is left as status='sending' with delivery_state NULL — recoverStuckSends
 * turns that into needs_review at the next startup, so a message that may
 * already have gone out is never silently re-sent.
 *
 * sent_at is the SEND ATTEMPT time and is written exactly once, here. Acks
 * never move it, so a late ack cannot shift a message into another day's
 * daily-cap bucket.
 */
function markSendStarted(db, contactId) {
  db.prepare(
    `UPDATE contacts SET
       status = 'sending',
       sent_at = ?,
       delivery_state = NULL,
       wa_message_id = NULL,
       ack_error = NULL,
       error_message = NULL,
       delivered_at = NULL,
       read_at = NULL
     WHERE id = ?`
  ).run(new Date().toISOString(), contactId);
}

/**
 * The socket accepted the payload. That is NOT delivery: park the contact in
 * pending_ack (status stays 'sending') and wait for WhatsApp's ack. sent_at is
 * left alone — markSendStarted owns it (COALESCE only backfills a row that was
 * never claimed, which the ack-timeout sweep relies on).
 */
function markSendAttempted(db, contactId, waMessageId, renderedMessage) {
  db.prepare(
    `UPDATE contacts SET
       status = 'sending',
       delivery_state = 'pending_ack',
       wa_message_id = ?,
       rendered_message = ?,
       sent_at = COALESCE(sent_at, ?)
     WHERE id = ?`
  ).run(waMessageId || null, renderedMessage, new Date().toISOString(), contactId);
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

/**
 * WhatsApp acknowledged the message: it reached the server.
 * sent_at is NOT rewritten — it is the attempt time (see markSendStarted).
 */
function markServerAck(db, contactId) {
  db.prepare(
    `UPDATE contacts SET
       status = 'sent',
       delivery_state = 'server_ack',
       sent_at = COALESCE(sent_at, ?),
       ack_error = NULL,
       error_message = NULL
     WHERE id = ?`
  ).run(new Date().toISOString(), contactId);
}

/** Delivery receipt: it reached the recipient's device. */
function markDelivered(db, contactId) {
  const now = new Date().toISOString();
  db.prepare(
    `UPDATE contacts SET
       status = 'sent',
       delivery_state = 'delivered',
       delivered_at = ?,
       sent_at = COALESCE(sent_at, ?),
       ack_error = NULL,
       error_message = NULL
     WHERE id = ?`
  ).run(now, now, contactId);
}

/** Read receipt: the recipient opened it. */
function markRead(db, contactId) {
  const now = new Date().toISOString();
  db.prepare(
    `UPDATE contacts SET
       status = 'sent',
       delivery_state = 'read',
       read_at = ?,
       delivered_at = COALESCE(delivered_at, ?),
       sent_at = COALESCE(sent_at, ?),
       ack_error = NULL,
       error_message = NULL
     WHERE id = ?`
  ).run(now, now, now, contactId);
}

/** WhatsApp refused the message (ack error, or no ack at all). */
function markRejected(db, contactId, ackError, errorMessage) {
  db.prepare(
    `UPDATE contacts SET
       status = 'failed',
       delivery_state = 'rejected',
       ack_error = ?,
       error_message = ?
     WHERE id = ?`
  ).run(String(ackError), errorMessage, contactId);
}

function findContactByWaMessageId(db, waMessageId) {
  if (!waMessageId) return undefined;
  return db
    .prepare(
      `SELECT id, program_id, phone, status, delivery_state
       FROM contacts WHERE wa_message_id = ? ORDER BY id DESC LIMIT 1`
    )
    .get(waMessageId);
}

/**
 * Startup recovery. Rows left in 'sending' by a killed worker are unknowable,
 * so they go to needs_review — EXCEPT rows legitimately awaiting an ack, which
 * the timeout sweep resolves on its own (it would otherwise clobber a send
 * that is only a few seconds old).
 */
function recoverStuckSends(db) {
  const result = db
    .prepare(
      `UPDATE contacts SET status = 'needs_review'
       WHERE status = 'sending'
         AND (delivery_state IS NULL OR delivery_state <> 'pending_ack')`
    )
    .run();
  return result.changes;
}

/**
 * Fail every contact that has been waiting for an ack longer than timeoutMs.
 * Returns the affected rows so the caller can count them as rejections.
 */
function sweepAckTimeouts(db, timeoutMs = ACK_TIMEOUT_MS) {
  const cutoff = new Date(Date.now() - timeoutMs).toISOString();
  const rows = db
    .prepare(
      `SELECT id, phone FROM contacts
       WHERE delivery_state = 'pending_ack' AND (sent_at IS NULL OR sent_at <= ?)`
    )
    .all(cutoff);
  for (const row of rows) {
    markRejected(db, row.id, 'timeout', ACK_TIMEOUT_MESSAGE);
  }
  return rows;
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

const MAX_REPLY_BODY = 2000;

/**
 * Persist the reply body. Linked to the most recently sent contact with that
 * phone (the same number can appear in several campaigns); contact_id stays
 * NULL when we never messaged them.
 */
function recordReply(db, phone, body) {
  const contact = db
    .prepare(
      `SELECT id FROM contacts WHERE phone = ?
       ORDER BY (sent_at IS NULL), sent_at DESC, id DESC LIMIT 1`
    )
    .get(phone);
  const trimmed = body == null ? null : String(body).slice(0, MAX_REPLY_BODY);
  const result = db
    .prepare('INSERT INTO replies (contact_id, phone, body, received_at) VALUES (?, ?, ?, ?)')
    .run(contact ? contact.id : null, phone, trimmed, new Date().toISOString());
  return result.lastInsertRowid;
}

/** Stop sending entirely: pause every program and record why. */
function haltSending(db, reason) {
  const halt = db.transaction(() => {
    db.prepare('UPDATE programs SET paused = 1').run();
    db.prepare('UPDATE worker_heartbeat SET halted_at = ?, halt_reason = ? WHERE id = 1').run(
      new Date().toISOString(),
      reason
    );
  });
  halt();
}

function getHalt(db) {
  const row = db.prepare('SELECT halted_at, halt_reason FROM worker_heartbeat WHERE id = 1').get();
  return row || { halted_at: null, halt_reason: null };
}

function isHalted(db) {
  return Boolean(getHalt(db).halted_at);
}

function clearHalt(db) {
  db.prepare('UPDATE worker_heartbeat SET halted_at = NULL, halt_reason = NULL WHERE id = 1').run();
}

function getAttachments(db, programId) {
  return db
    .prepare('SELECT * FROM program_attachments WHERE program_id = ? ORDER BY id ASC')
    .all(programId);
}

module.exports = {
  ACK_TIMEOUT_MS,
  MAX_REPLY_BODY,
  getSettings,
  markSending,
  markSendStarted,
  markSendAttempted,
  markSent,
  markFailed,
  markServerAck,
  markDelivered,
  markRead,
  markRejected,
  findContactByWaMessageId,
  recoverStuckSends,
  sweepAckTimeouts,
  getNextPendingContact,
  countSentToday,
  markReplied,
  recordReply,
  haltSending,
  getHalt,
  isHalted,
  clearHalt,
  getAttachments,
};
