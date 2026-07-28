"""Settings page + JSON API."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import db as appdb
from app.send_window import (
    COMMON_TIMEZONES,
    evaluate_send_window,
    is_valid_timezone,
    parse_hhmm,
)
from web.deps import get_db
from web.templating import templates

router = APIRouter()


class SettingsUpdate(BaseModel):
    dry_run: bool
    delay_seconds: int = Field(ge=0, le=86400)
    jitter_seconds: int = Field(ge=0, le=86400)
    # 0 or null means "no limit" (stored as NULL, save_settings semantics).
    daily_cap: int | None = Field(default=None, ge=0, le=100000)
    # Empty string / null means "no window": send at any hour.
    send_window_start: str | None = None
    send_window_end: str | None = None
    send_timezone: str | None = None


def _settings_dict(conn: sqlite3.Connection) -> dict:
    settings = appdb.get_full_settings(conn)
    window = evaluate_send_window(
        settings["send_window_start"],
        settings["send_window_end"],
        settings["send_timezone"],
    )
    sent_today = appdb.count_sent_today(conn)
    cap = settings["daily_cap"]
    return {
        **settings,
        "send_window": window,
        "sent_today": sent_today,
        "remaining_today": None if cap is None else max(cap - sent_today, 0),
        "timezone_options": COMMON_TIMEZONES,
    }


def _validate_window(payload: SettingsUpdate) -> None:
    for label, value in (
        ("send_window_start", payload.send_window_start),
        ("send_window_end", payload.send_window_end),
    ):
        if value and parse_hhmm(value) is None:
            raise HTTPException(
                status_code=400, detail=f"{label} must be a time like 09:00 (24-hour HH:MM)."
            )
    if payload.send_timezone and not is_valid_timezone(payload.send_timezone):
        raise HTTPException(
            status_code=400,
            detail=f"'{payload.send_timezone}' is not a known time zone. "
            "Use an IANA name such as Asia/Almaty.",
        )
    # One bound without the other would silently disable the window.
    if bool(payload.send_window_start) != bool(payload.send_window_end):
        raise HTTPException(
            status_code=400,
            detail="Set both a start and an end time for the sending window, or leave both empty.",
        )


@router.get("/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {})


@router.get("/api/settings")
def read_settings(conn: sqlite3.Connection = Depends(get_db)):
    return _settings_dict(conn)


@router.put("/api/settings")
def update_settings(payload: SettingsUpdate, conn: sqlite3.Connection = Depends(get_db)):
    _validate_window(payload)
    appdb.save_settings(
        conn,
        dry_run=payload.dry_run,
        delay_seconds=payload.delay_seconds,
        jitter_seconds=payload.jitter_seconds,
        daily_cap=payload.daily_cap,
        send_window_start=payload.send_window_start,
        send_window_end=payload.send_window_end,
        send_timezone=payload.send_timezone,
    )
    return _settings_dict(conn)
