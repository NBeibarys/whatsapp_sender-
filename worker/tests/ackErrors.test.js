const test = require('node:test');
const assert = require('node:assert/strict');
const { ackErrorMessage, haltReason, ACK_TIMEOUT_MESSAGE } = require('../ackErrors');

test('463 explains the linked-device restriction in plain language', () => {
  const message = ackErrorMessage(463);
  assert.match(message, /refusing new conversations/);
  assert.match(message, /restricted from starting new chats/);
  assert.doesNotMatch(message, /463/, 'the user should not have to decode an error number');
});

test('463 maps the same way when the code arrives as a string', () => {
  assert.equal(ackErrorMessage('463'), ackErrorMessage(463));
});

test('401 and 403 map to the not-authorized wording', () => {
  assert.equal(ackErrorMessage(401), 'WhatsApp rejected this message (not authorized).');
  assert.equal(ackErrorMessage(403), 'WhatsApp rejected this message (not authorized).');
});

test('408 and timeout map to the no-confirmation wording', () => {
  assert.equal(ackErrorMessage(408), 'No confirmation from WhatsApp in time.');
  assert.equal(ackErrorMessage('timeout'), 'No confirmation from WhatsApp in time.');
});

test('unknown codes still produce a readable sentence including the code', () => {
  assert.equal(ackErrorMessage(999), 'WhatsApp rejected this message (error 999).');
});

test('the ack timeout message states the 60s window', () => {
  assert.equal(ACK_TIMEOUT_MESSAGE, 'No confirmation from WhatsApp within 60s.');
});

test('haltReason combines the count with the plain-language cause', () => {
  const reason = haltReason(3, 463);
  assert.match(reason, /^3 messages in a row were not accepted by WhatsApp\./);
  assert.match(reason, /restricted from starting new chats/);
});
