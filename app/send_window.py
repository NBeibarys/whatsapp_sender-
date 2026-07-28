"""Sending window evaluation (mirror of worker/sendWindow.js).

The worker decides whether to send; this module lets the UI explain the same
decision. Keep the two implementations in sync.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "UTC"

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Curated shortlist for the Settings picker. Any IANA name is still accepted —
# the input is a free-text field backed by this datalist.
COMMON_TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Moscow",
    "Asia/Dubai",
    "Asia/Almaty",
    "Asia/Tashkent",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
]


def parse_hhmm(value: str | None) -> int | None:
    """'HH:MM' -> minutes since local midnight, or None when unset/invalid."""
    if not isinstance(value, str):
        return None
    match = _HHMM.match(value.strip())
    if match is None:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def format_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def is_valid_timezone(name: str | None) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def evaluate_send_window(
    start: str | None,
    end: str | None,
    timezone: str | None,
    now: datetime | None = None,
) -> dict:
    """Same rules as worker/sendWindow.js.

    Either bound unset means no window. start == end means a full 24h window.
    start > end crosses midnight. An unknown timezone falls back to UTC rather
    than blocking the queue.
    """
    requested = timezone or DEFAULT_TIMEZONE
    resolved = requested if is_valid_timezone(requested) else DEFAULT_TIMEZONE
    zone = ZoneInfo(resolved)
    moment = (now or datetime.now(tz=zone)).astimezone(zone)
    now_minutes = moment.hour * 60 + moment.minute

    start_minutes = parse_hhmm(start)
    end_minutes = parse_hhmm(end)

    if start_minutes is None or end_minutes is None:
        return {
            "allowed": True,
            "configured": False,
            "reason": None,
            "timezone": resolved,
            "timezone_invalid": resolved != requested,
            "local_time": format_hhmm(now_minutes),
            "window": None,
        }

    if start_minutes == end_minutes:
        allowed = True
    elif start_minutes < end_minutes:
        allowed = start_minutes <= now_minutes < end_minutes
    else:  # crosses midnight
        allowed = now_minutes >= start_minutes or now_minutes < end_minutes

    label = f"{format_hhmm(start_minutes)}–{format_hhmm(end_minutes)} {resolved}"
    return {
        "allowed": allowed,
        "configured": True,
        "reason": None
        if allowed
        else f"Outside the sending window ({label}); local time is {format_hhmm(now_minutes)}.",
        "timezone": resolved,
        "timezone_invalid": resolved != requested,
        "local_time": format_hhmm(now_minutes),
        "window": label,
    }
