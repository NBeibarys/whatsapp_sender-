"""Connection page + JSON API (replicates the retired Streamlit Connection page logic)."""

import os
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import db as appdb
from app.config import WORKER_STALE_AFTER_SECONDS
from app.csv_import import parse_contacts_rows
from app.worker_supervisor import ensure_worker_running
from web.deps import get_db
from web.templating import templates

router = APIRouter()

TEST_PROGRAM_TEMPLATE = "Hi {{name}}, this is a test message from the Silkroad WhatsApp Sender."


class TestMessage(BaseModel):
    phone: str
    name: str = "there"


def _auto_worker_enabled() -> bool:
    return os.environ.get("SKIP_AUTO_WORKER") != "1"


@router.get("/connection")
def connection_page(request: Request):
    return templates.TemplateResponse(request, "connection.html", {})


@router.get("/api/connection/status")
def connection_status(conn: sqlite3.Connection = Depends(get_db)):
    worker_message = None
    if _auto_worker_enabled():
        _, worker_message = ensure_worker_running()

    heartbeat = appdb.get_heartbeat(conn)
    qr_code = heartbeat["qr_code"]

    # Once connected, any lingering QR code is stale — clear it.
    if heartbeat["connected"] and qr_code:
        appdb.clear_qr_code(conn)
        qr_code = None

    age_seconds = None
    if heartbeat["last_seen"]:
        try:
            last_seen_dt = datetime.fromisoformat(heartbeat["last_seen"].replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - last_seen_dt).total_seconds()
        except ValueError:
            age_seconds = None

    worker_alive = age_seconds is not None and age_seconds <= WORKER_STALE_AFTER_SECONDS

    return {
        "worker_alive": worker_alive,
        "age_seconds": age_seconds,
        "connected": heartbeat["connected"],
        "qr_data_url": qr_code,
        "disconnect_requested": heartbeat["disconnect_requested"],
        "worker_message": worker_message,
        # Auto-halt state — the global banner polls this endpoint only.
        "halted": heartbeat["halted"],
        "halt_reason": heartbeat["halt_reason"],
        "halted_at": heartbeat["halted_at"],
    }


@router.post("/api/sending/resume")
def resume_sending(conn: sqlite3.Connection = Depends(get_db)):
    """Clear the worker's auto-halt. Campaigns stay paused on purpose."""
    appdb.clear_halt(conn)
    return {"halted": False, "programs_still_paused": True}


@router.post("/api/connection/disconnect")
def disconnect(conn: sqlite3.Connection = Depends(get_db)):
    appdb.request_disconnect(conn)
    if _auto_worker_enabled():
        ensure_worker_running()
    return {"disconnect_requested": True}


@router.post("/api/connection/test-message")
def queue_test_message(payload: TestMessage, conn: sqlite3.Connection = Depends(get_db)):
    name = payload.name.strip() or "there"
    valid, invalid = parse_contacts_rows([{"phone": payload.phone, "name": name}])
    if invalid:
        raise HTTPException(status_code=400, detail=invalid[0]["error"])

    existing = appdb.get_program_by_name(conn, appdb.TEST_PROGRAM_NAME)
    if existing:
        test_program_id = existing["id"]
    else:
        test_program_id = appdb.create_program(
            conn, appdb.TEST_PROGRAM_NAME, TEST_PROGRAM_TEMPLATE
        )
    appdb.set_program_paused(conn, test_program_id, False)

    inserted, duplicates = appdb.insert_contacts(conn, test_program_id, valid)
    return {
        "queued": inserted > 0,
        "duplicate": len(duplicates) > 0,
        "phone": valid[0]["phone"],
        "program": appdb.TEST_PROGRAM_NAME,
    }
