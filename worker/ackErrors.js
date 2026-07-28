/**
 * Plain-language text for WhatsApp ack error codes.
 *
 * WhatsApp answers a sent message with an <ack> stanza; when it carries an
 * `error` attribute the message was NOT delivered, even though sendMessage()
 * resolved successfully. Baileys surfaces that as a messages.update with
 * status = ERROR and messageStubParameters = [code] (see
 * node_modules/@whiskeysockets/baileys/lib/Socket/messages-recv.js,
 * handleBadAck).
 */

const RESTRICTED_463 =
  'WhatsApp is refusing new conversations from this linked device. ' +
  'Your account is restricted from starting new chats — this usually clears ' +
  'on its own after a while.';

// Used by the ack-timeout sweep, which fails rows that never got any ack.
const ACK_TIMEOUT_MESSAGE = 'No confirmation from WhatsApp within 60s.';

function ackErrorMessage(code) {
  if (code === 'timeout') return 'No confirmation from WhatsApp in time.';

  const numeric = Number(code);
  switch (numeric) {
    case 463:
      return RESTRICTED_463;
    case 401:
    case 403:
      return 'WhatsApp rejected this message (not authorized).';
    case 408:
      return 'No confirmation from WhatsApp in time.';
    default:
      return `WhatsApp rejected this message (error ${code}).`;
  }
}

/** Banner text for the auto-halt: why we stopped, in plain language. */
function haltReason(consecutiveRejections, code) {
  return (
    `${consecutiveRejections} messages in a row were not accepted by WhatsApp. ` +
    ackErrorMessage(code)
  );
}

module.exports = { ackErrorMessage, haltReason, ACK_TIMEOUT_MESSAGE, RESTRICTED_463 };
