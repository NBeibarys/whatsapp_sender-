"""Sending window rules (mirror of worker/tests/sendWindow.test.js)."""

from datetime import datetime, timezone

import pytest

from app.send_window import (
    COMMON_TIMEZONES,
    evaluate_send_window,
    is_valid_timezone,
    parse_hhmm,
)


def utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "value,expected",
    [("00:00", 0), ("09:30", 570), ("23:59", 1439)],
)
def test_parse_hhmm_accepts_valid_times(value, expected):
    assert parse_hhmm(value) == expected


@pytest.mark.parametrize("value", ["24:00", "9:30", "09:60", "nope", "", None])
def test_parse_hhmm_rejects_everything_else(value):
    assert parse_hhmm(value) is None


def test_no_window_means_always_allowed():
    result = evaluate_send_window(None, None, "UTC", utc(3))
    assert result["allowed"] is True
    assert result["configured"] is False
    assert result["window"] is None


def test_one_bound_alone_is_treated_as_no_window():
    result = evaluate_send_window("09:00", None, "UTC", utc(3))
    assert result["allowed"] is True
    assert result["configured"] is False


def test_inside_and_outside_a_normal_window():
    inside = evaluate_send_window("09:00", "18:00", "UTC", utc(12))
    assert inside["allowed"] is True
    assert inside["window"] == "09:00–18:00 UTC"

    outside = evaluate_send_window("09:00", "18:00", "UTC", utc(20))
    assert outside["allowed"] is False
    assert "Outside the sending window" in outside["reason"]
    assert "20:00" in outside["reason"]


def test_window_bounds_are_start_inclusive_end_exclusive():
    assert evaluate_send_window("09:00", "18:00", "UTC", utc(9, 0))["allowed"] is True
    assert evaluate_send_window("09:00", "18:00", "UTC", utc(17, 59))["allowed"] is True
    assert evaluate_send_window("09:00", "18:00", "UTC", utc(18, 0))["allowed"] is False


def test_window_crossing_midnight():
    assert evaluate_send_window("22:00", "06:00", "UTC", utc(23))["allowed"] is True
    assert evaluate_send_window("22:00", "06:00", "UTC", utc(2))["allowed"] is True
    assert evaluate_send_window("22:00", "06:00", "UTC", utc(12))["allowed"] is False
    assert evaluate_send_window("22:00", "06:00", "UTC", utc(6))["allowed"] is False


def test_equal_bounds_mean_a_full_day():
    assert evaluate_send_window("09:00", "09:00", "UTC", utc(3))["allowed"] is True
    assert evaluate_send_window("09:00", "09:00", "UTC", utc(15))["allowed"] is True


def test_window_is_evaluated_in_the_configured_timezone():
    # 03:00 UTC is 08:00 in Almaty (UTC+5).
    almaty = evaluate_send_window("07:00", "20:00", "Asia/Almaty", utc(3))
    assert almaty["local_time"] == "08:00"
    assert almaty["allowed"] is True

    assert evaluate_send_window("07:00", "20:00", "UTC", utc(3))["allowed"] is False


def test_unknown_timezone_falls_back_to_utc_without_blocking():
    result = evaluate_send_window("00:00", "23:59", "Mars/Olympus", utc(12))
    assert result["timezone"] == "UTC"
    assert result["timezone_invalid"] is True
    assert result["allowed"] is True


def test_is_valid_timezone():
    assert is_valid_timezone("Asia/Almaty") is True
    assert is_valid_timezone("America/New_York") is True
    assert is_valid_timezone("Mars/Olympus") is False
    assert is_valid_timezone("") is False


def test_curated_list_covers_the_zones_the_operator_asked_for():
    for zone in (
        "UTC",
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Berlin",
        "Asia/Almaty",
        "Asia/Dubai",
        "Asia/Singapore",
    ):
        assert zone in COMMON_TIMEZONES
        assert is_valid_timezone(zone)
