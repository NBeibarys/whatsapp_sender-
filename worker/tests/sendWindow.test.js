const test = require('node:test');
const assert = require('node:assert/strict');
const { evaluateSendWindow, parseHHMM, isValidTimeZone } = require('../sendWindow');

/** An instant that is 08:00 UTC — used to reason about zone offsets. */
function utc(hour, minute = 0) {
  return new Date(Date.UTC(2026, 6, 15, hour, minute, 0));
}

test('parseHHMM accepts valid 24h times and rejects everything else', () => {
  assert.equal(parseHHMM('00:00'), 0);
  assert.equal(parseHHMM('09:30'), 570);
  assert.equal(parseHHMM('23:59'), 1439);
  ['24:00', '9:30', '09:60', 'nope', '', null, undefined].forEach((bad) => {
    assert.equal(parseHHMM(bad), null, `expected ${bad} to be rejected`);
  });
});

test('no window configured means sending is always allowed', () => {
  const result = evaluateSendWindow({ send_timezone: 'UTC' }, utc(3));
  assert.equal(result.allowed, true);
  assert.equal(result.configured, false);
  assert.equal(result.window, null);
});

test('one bound without the other is treated as no window', () => {
  const result = evaluateSendWindow(
    { send_window_start: '09:00', send_window_end: null, send_timezone: 'UTC' },
    utc(3)
  );
  assert.equal(result.allowed, true);
  assert.equal(result.configured, false);
});

test('inside a normal window sending is allowed', () => {
  const result = evaluateSendWindow(
    { send_window_start: '09:00', send_window_end: '18:00', send_timezone: 'UTC' },
    utc(12)
  );
  assert.equal(result.allowed, true);
  assert.equal(result.local_time, '12:00');
  assert.equal(result.window, '09:00–18:00 UTC');
});

test('outside a normal window sending is blocked with a readable reason', () => {
  const result = evaluateSendWindow(
    { send_window_start: '09:00', send_window_end: '18:00', send_timezone: 'UTC' },
    utc(20)
  );
  assert.equal(result.allowed, false);
  assert.match(result.reason, /Outside the sending window \(09:00–18:00 UTC\)/);
  assert.match(result.reason, /local time is 20:00/);
});

test('the window boundaries are inclusive at the start and exclusive at the end', () => {
  const settings = { send_window_start: '09:00', send_window_end: '18:00', send_timezone: 'UTC' };
  assert.equal(evaluateSendWindow(settings, utc(9, 0)).allowed, true);
  assert.equal(evaluateSendWindow(settings, utc(17, 59)).allowed, true);
  assert.equal(evaluateSendWindow(settings, utc(18, 0)).allowed, false);
});

test('a window crossing midnight covers both sides of 00:00', () => {
  const settings = { send_window_start: '22:00', send_window_end: '06:00', send_timezone: 'UTC' };
  assert.equal(evaluateSendWindow(settings, utc(23)).allowed, true, '23:00 is inside');
  assert.equal(evaluateSendWindow(settings, utc(2)).allowed, true, '02:00 is inside');
  assert.equal(evaluateSendWindow(settings, utc(12)).allowed, false, 'noon is outside');
  assert.equal(evaluateSendWindow(settings, utc(6)).allowed, false, 'the end bound is exclusive');
});

test('start equal to end means a full 24 hours', () => {
  const settings = { send_window_start: '09:00', send_window_end: '09:00', send_timezone: 'UTC' };
  assert.equal(evaluateSendWindow(settings, utc(3)).allowed, true);
  assert.equal(evaluateSendWindow(settings, utc(15)).allowed, true);
});

test('the window is evaluated in the configured timezone, not UTC', () => {
  // 03:00 UTC is 08:00 in Almaty (UTC+5) — inside a 07:00–20:00 local window,
  // but outside the same window read as UTC.
  const settings = {
    send_window_start: '07:00',
    send_window_end: '20:00',
    send_timezone: 'Asia/Almaty',
  };
  const result = evaluateSendWindow(settings, utc(3));
  assert.equal(result.local_time, '08:00');
  assert.equal(result.allowed, true);

  const asUtc = evaluateSendWindow({ ...settings, send_timezone: 'UTC' }, utc(3));
  assert.equal(asUtc.allowed, false);
});

test('an unknown timezone falls back to UTC instead of blocking the queue', () => {
  const result = evaluateSendWindow(
    { send_window_start: '00:00', send_window_end: '23:59', send_timezone: 'Mars/Olympus' },
    utc(12)
  );
  assert.equal(result.timezone, 'UTC');
  assert.equal(result.timezone_invalid, true);
  assert.equal(result.allowed, true);
});

test('isValidTimeZone recognises real IANA names only', () => {
  assert.equal(isValidTimeZone('Asia/Almaty'), true);
  assert.equal(isValidTimeZone('America/New_York'), true);
  assert.equal(isValidTimeZone('Mars/Olympus'), false);
  assert.equal(isValidTimeZone(''), false);
});
