/**
 * Turns Baileys 'messages.update' events into truthful contact rows.
 *
 * Why messages.update and nothing else (baileys 6.7.23, verified in
 * node_modules/@whiskeysockets/baileys/lib/Socket/messages-recv.js):
 *   - handleReceipt (line ~543) emits `messages.update` with
 *     `update.status` for 1:1 chats — SERVER_ACK / DELIVERY_ACK / READ.
 *     `message-receipt.update` is only emitted for GROUP / status-broadcast
 *     jids (line ~535), so it is useless for our 1:1 sends.
 *   - handleBadAck (line ~825) logs 'received error in ack' AND emits
 *     `messages.update` with status ERROR and
 *     messageStubParameters = [attrs.error]. So the error code is available
 *     as a normal event; no pino log interception is needed.
 *
 * proto.WebMessageInfo.Status in this version is
 * ERROR:0, PENDING:1, SERVER_ACK:2, DELIVERY_ACK:3, READ:4, PLAYED:5
 * (verified at runtime, not assumed).
 */

const {
  findContactByWaMessageId,
  markServerAck,
  markDelivered,
  markRead,
  markRejected,
  haltSending,
  isHalted,
} = require('./queue');
const { ackErrorMessage, haltReason } = require('./ackErrors');

const WA_STATUS = { ERROR: 0, PENDING: 1, SERVER_ACK: 2, DELIVERY_ACK: 3, READ: 4, PLAYED: 5 };

const HALT_AFTER_CONSECUTIVE_REJECTIONS = 3;

// Never downgrade: receipts can arrive out of order.
const DELIVERY_RANK = { pending_ack: 1, server_ack: 2, delivered: 3, read: 4 };

function jidToPhone(jid) {
  if (!jid) return null;
  const user = String(jid).split('@')[0].split(':')[0];
  return `+${user}`;
}

function digits(value) {
  return String(value == null ? '' : value).replace(/\D/g, '');
}

/**
 * Creates the stateful ack tracker (consecutive-rejection counter lives here,
 * not in the DB — a worker restart legitimately resets it).
 */
function createAckTracker(db, options = {}) {
  const haltAfter = options.haltAfter || HALT_AFTER_CONSECUTIVE_REJECTIONS;
  const log = options.log || console;
  let consecutiveRejections = 0;

  function registerSuccess() {
    consecutiveRejections = 0;
  }

  /** Count a rejection and auto-halt once too many pile up. */
  function registerRejection(code) {
    consecutiveRejections += 1;
    if (consecutiveRejections < haltAfter) return false;
    if (isHalted(db)) return false;
    const reason = haltReason(consecutiveRejections, code);
    haltSending(db, reason);
    log.error(`SENDING HALTED after ${consecutiveRejections} rejections in a row: ${reason}`);
    return true;
  }

  function applySuccessState(contact, state) {
    const current = DELIVERY_RANK[contact.delivery_state] || 0;
    if (contact.delivery_state === 'rejected') return false;
    if (DELIVERY_RANK[state] <= current) return false;
    if (state === 'server_ack') markServerAck(db, contact.id);
    else if (state === 'delivered') markDelivered(db, contact.id);
    else if (state === 'read') markRead(db, contact.id);
    return true;
  }

  /**
   * Handle one { key, update } entry. Unknown ids (messages sent from the
   * phone, or from a previous run) are ignored.
   */
  function handleUpdate(entry) {
    const key = entry && entry.key;
    const update = (entry && entry.update) || {};
    if (!key || !key.fromMe) return null;
    const status = update.status;
    if (status == null) return null;

    const contact = findContactByWaMessageId(db, key.id);
    if (!contact) return null;

    // Sanity-check the recipient: an id collision must not corrupt a row.
    const eventPhone = jidToPhone(key.remoteJid);
    if (eventPhone && digits(eventPhone) !== digits(contact.phone)) {
      log.warn(
        `Ignoring ack for ${key.id}: phone mismatch (event ${eventPhone}, contact ${contact.phone})`
      );
      return null;
    }

    if (status === WA_STATUS.ERROR) {
      const code =
        (Array.isArray(update.messageStubParameters) && update.messageStubParameters[0]) ||
        'unknown';
      const message = ackErrorMessage(code);
      markRejected(db, contact.id, code, message);
      log.error(`WhatsApp rejected the message to ${contact.phone} (error ${code}): ${message}`);
      const halted = registerRejection(code);
      return { contactId: contact.id, state: 'rejected', code: String(code), halted };
    }

    let state = null;
    if (status === WA_STATUS.SERVER_ACK) state = 'server_ack';
    else if (status === WA_STATUS.DELIVERY_ACK) state = 'delivered';
    else if (status === WA_STATUS.READ || status === WA_STATUS.PLAYED) state = 'read';
    if (!state) return null;

    // Any confirmation from WhatsApp means the account is working again.
    registerSuccess();
    const applied = applySuccessState(contact, state);
    return { contactId: contact.id, state, applied, halted: false };
  }

  /** The timeout sweep found silently-dropped sends: they count as rejections. */
  function registerTimeouts(count) {
    let halted = false;
    for (let i = 0; i < count; i += 1) {
      halted = registerRejection('timeout') || halted;
    }
    return halted;
  }

  return {
    handleUpdate,
    handleUpdates(entries) {
      return (entries || []).map(handleUpdate);
    },
    registerTimeouts,
    get consecutiveRejections() {
      return consecutiveRejections;
    },
  };
}

module.exports = {
  createAckTracker,
  WA_STATUS,
  HALT_AFTER_CONSECUTIVE_REJECTIONS,
  jidToPhone,
};
