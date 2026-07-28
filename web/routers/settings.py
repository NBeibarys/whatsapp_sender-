"""Settings page + JSON API."""

import sqlite3

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app import db as appdb
from web.deps import get_db
from web.templating import templates

router = APIRouter()


class SettingsUpdate(BaseModel):
    dry_run: bool
    delay_seconds: int = Field(ge=0, le=86400)
    jitter_seconds: int = Field(ge=0, le=86400)
    # 0 or null means "no limit" (stored as NULL, save_settings semantics).
    daily_cap: int | None = Field(default=None, ge=0, le=100000)


def _settings_dict(conn: sqlite3.Connection) -> dict:
    delay_seconds, jitter_seconds, daily_cap, dry_run = appdb.get_settings(conn)
    return {
        "dry_run": bool(dry_run),
        "delay_seconds": delay_seconds,
        "jitter_seconds": jitter_seconds,
        "daily_cap": daily_cap,
    }


@router.get("/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {})


@router.get("/api/settings")
def read_settings(conn: sqlite3.Connection = Depends(get_db)):
    return _settings_dict(conn)


@router.put("/api/settings")
def update_settings(payload: SettingsUpdate, conn: sqlite3.Connection = Depends(get_db)):
    appdb.save_settings(
        conn,
        dry_run=payload.dry_run,
        delay_seconds=payload.delay_seconds,
        jitter_seconds=payload.jitter_seconds,
        daily_cap=payload.daily_cap,
    )
    return _settings_dict(conn)
