/**
 * Sending window: only send between send_window_start and send_window_end,
 * measured in send_timezone (an IANA zone name).
 *
 * Uses Intl.DateTimeFormat with a timeZone — no extra dependency, and it
 * follows DST automatically because it formats the *current* instant in that
 * zone rather than applying a fixed offset.
 */

const DEFAULT_TIMEZONE = 'UTC';

/** 'HH:MM' -> minutes since local midnight, or null when unset/invalid. */
function parseHHMM(value) {
  if (typeof value !== 'string') return null;
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(value.trim());
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function formatHHMM(minutes) {
  const h = String(Math.floor(minutes / 60)).padStart(2, '0');
  const m = String(minutes % 60).padStart(2, '0');
  return `${h}:${m}`;
}

function isValidTimeZone(timezone) {
  if (!timezone) return false;
  try {
    new Intl.DateTimeFormat('en-GB', { timeZone: timezone });
    return true;
  } catch {
    return false;
  }
}

/** Minutes since midnight *in that zone* for the given instant. */
function localMinutes(timezone, now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: timezone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(now);
  const hour = Number(parts.find((p) => p.type === 'hour').value) % 24;
  const minute = Number(parts.find((p) => p.type === 'minute').value);
  return hour * 60 + minute;
}

/**
 * Decide whether sending is allowed right now.
 * Returns { allowed, configured, reason, timezone, local_time, window }.
 *
 * - Either bound unset (or unparseable) => no window, always allowed.
 * - start === end => a full 24h window, always allowed.
 * - start > end => the window crosses midnight (e.g. 22:00–06:00).
 * - An unknown timezone falls back to UTC rather than blocking the queue.
 */
function evaluateSendWindow(settings, now = new Date()) {
  const start = parseHHMM(settings && settings.send_window_start);
  const end = parseHHMM(settings && settings.send_window_end);
  const requested = (settings && settings.send_timezone) || DEFAULT_TIMEZONE;
  const timezone = isValidTimeZone(requested) ? requested : DEFAULT_TIMEZONE;
  const timezoneFellBack = timezone !== requested;

  if (start === null || end === null) {
    return {
      allowed: true,
      configured: false,
      reason: null,
      timezone,
      timezone_invalid: timezoneFellBack,
      local_time: formatHHMM(localMinutes(timezone, now)),
      window: null,
    };
  }

  const nowMinutes = localMinutes(timezone, now);
  const allowed =
    start === end ? true : start < end
      ? nowMinutes >= start && nowMinutes < end
      : nowMinutes >= start || nowMinutes < end;

  const label = `${formatHHMM(start)}–${formatHHMM(end)} ${timezone}`;
  return {
    allowed,
    configured: true,
    reason: allowed
      ? null
      : `Outside the sending window (${label}); local time is ${formatHHMM(nowMinutes)}.`,
    timezone,
    timezone_invalid: timezoneFellBack,
    local_time: formatHHMM(nowMinutes),
    window: label,
  };
}

module.exports = {
  DEFAULT_TIMEZONE,
  parseHHMM,
  formatHHMM,
  isValidTimeZone,
  localMinutes,
  evaluateSendWindow,
};
