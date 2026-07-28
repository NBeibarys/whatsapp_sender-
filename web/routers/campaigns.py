"""Campaign pages + JSON API.

Template-preview helpers were ported from the retired Streamlit Campaign page
(_placeholder_fields / _render_template_preview / _contact_preview_fields /
_sample_preview_fields / _attachment_context_lines semantics).
"""

import json
import mimetypes
import os
import re
import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db as appdb
from app.csv_import import map_contact_rows, parse_contacts_rows, parse_pasted_contacts
from app.send_window import evaluate_send_window
from web import csv_helpers
from web.deps import get_db
from web.templating import templates

router = APIRouter()

TEMPLATE_FIELD_PATTERN = re.compile(r"\{\{(\w+)\}\}")
ALLOWED_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".doc", ".docx"}
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB per attachment file
CONTACT_STATUSES = ("pending", "sending", "sent", "failed", "needs_review")
MAX_PASTE_BYTES = 1 * 1024 * 1024  # 1 MB of pasted contact lines
FIRST_ATTACHMENT_CAPTION_NOTE = "message text is sent as this attachment's caption"
OTHER_ATTACHMENT_CAPTION_NOTE = "sent immediately after, without a caption"


# --- Pydantic models ---


class CampaignCreate(BaseModel):
    name: str
    template_text: str


class TemplateUpdate(BaseModel):
    template_text: str


class ManualContact(BaseModel):
    phone: str
    name: str


class PastedContacts(BaseModel):
    text: str


class ContactIds(BaseModel):
    ids: list[int]


class NeedsReviewResolution(BaseModel):
    to: str


# --- Preview helpers (ported from 1_Campaign.py) ---


def _placeholder_fields(template_text: str) -> list[str]:
    fields = []
    for match in TEMPLATE_FIELD_PATTERN.finditer(template_text or ""):
        field = match.group(1)
        if field not in fields:
            fields.append(field)
    return fields


def _render_template_preview(template_text: str, fields: dict) -> tuple[str, list[str]]:
    missing = []

    def replace_field(match):
        field = match.group(1)
        if field not in fields:
            if field not in missing:
                missing.append(field)
            return match.group(0)
        return str(fields[field])

    return TEMPLATE_FIELD_PATTERN.sub(replace_field, template_text or ""), missing


def _contact_preview_fields(contact_row) -> dict:
    if contact_row is None:
        return {"name": "Example Name"}

    _, name, extra_fields_json = contact_row
    try:
        extra_fields = json.loads(extra_fields_json or "{}")
    except json.JSONDecodeError:
        extra_fields = {}
    if not isinstance(extra_fields, dict):
        extra_fields = {}

    return {"name": name, **extra_fields}


def _sample_preview_fields(template_text: str) -> dict:
    fields = {}
    for field in _placeholder_fields(template_text):
        fields[field] = "Example Name" if field == "name" else f"Example {field}"
    return fields or {"name": "Example Name"}


def _attachment_entries(attachments: list[dict]) -> list[dict]:
    entries = []
    for index, attachment in enumerate(attachments, start=1):
        entries.append(
            {
                "id": attachment["id"],
                "position": index,
                "media_type": attachment["media_type"],
                "file_name": attachment["file_name"],
                "caption_note": (
                    FIRST_ATTACHMENT_CAPTION_NOTE
                    if index == 1
                    else OTHER_ATTACHMENT_CAPTION_NOTE
                ),
            }
        )
    return entries


# --- Shared route helpers ---


def _require_program(conn: sqlite3.Connection, program_id: int) -> dict:
    program = appdb.get_program(conn, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return program


async def _read_csv_rows(file: UploadFile) -> list[dict]:
    if not csv_helpers.is_csv_filename(file.filename):
        raise HTTPException(status_code=400, detail="Please upload a .csv file for contacts.")
    content = await file.read()
    if len(content) > csv_helpers.MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV file too large (max 5 MB).")
    rows = csv_helpers.parse_csv_bytes(content)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV has no data rows.")
    return rows


def _parse_extra_columns(extra_columns: str | None) -> list[str]:
    if not extra_columns:
        return []
    try:
        parsed = json.loads(extra_columns)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="extra_columns must be a JSON list of column names.")
    if not isinstance(parsed, list) or not all(isinstance(c, str) for c in parsed):
        raise HTTPException(status_code=400, detail="extra_columns must be a JSON list of column names.")
    return parsed


def _mapped_contacts(
    rows: list[dict],
    phone_column: str,
    name_column: str,
    startup_name_column: str | None,
    email_column: str | None,
    extra_columns: str | None,
):
    return map_contact_rows(
        rows,
        phone_column=phone_column,
        name_column=name_column,
        startup_name_column=startup_name_column or None,
        email_column=email_column or None,
        extra_columns=_parse_extra_columns(extra_columns),
    )


def _flatten_valid_contact(contact: dict) -> dict:
    return {
        "phone": contact["phone"],
        "name": contact["name"],
        **contact.get("extra_fields", {}),
    }


# --- Page ---


@router.get("/campaigns")
def campaigns_page(
    request: Request,
    selected: int | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    programs = appdb.list_programs(conn)
    selected_program = None
    if selected is not None:
        selected_program = appdb.get_program(conn, selected)
    if selected_program is None and programs:
        selected_program = appdb.get_program(conn, programs[0]["id"])
    return templates.TemplateResponse(
        request,
        "campaign.html",
        {"programs": programs, "selected_program": selected_program},
    )


# --- Campaign CRUD ---


@router.get("/api/campaigns")
def list_campaigns(conn: sqlite3.Connection = Depends(get_db)):
    return appdb.list_programs(conn)


@router.post("/api/campaigns", status_code=201)
def create_campaign(payload: CampaignCreate, conn: sqlite3.Connection = Depends(get_db)):
    name = payload.name.strip()
    if not name or not payload.template_text.strip():
        raise HTTPException(status_code=400, detail="Name and template are required.")
    if name == appdb.TEST_PROGRAM_NAME:
        raise HTTPException(
            status_code=400,
            detail=f"'{appdb.TEST_PROGRAM_NAME}' is a reserved name — choose another.",
        )
    try:
        program_id = appdb.create_program(conn, name, payload.template_text)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"A campaign named '{name}' already exists.")
    return {"id": program_id, "name": name, "template_text": payload.template_text, "paused": False}


@router.post("/api/campaigns/{program_id}/pause")
def pause_campaign(program_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_program(conn, program_id)
    appdb.set_program_paused(conn, program_id, True)
    return {"id": program_id, "paused": True}


@router.post("/api/campaigns/{program_id}/resume")
def resume_campaign(program_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_program(conn, program_id)
    appdb.set_program_paused(conn, program_id, False)
    return {"id": program_id, "paused": False}


@router.put("/api/campaigns/{program_id}/template")
def update_template(
    program_id: int, payload: TemplateUpdate, conn: sqlite3.Connection = Depends(get_db)
):
    _require_program(conn, program_id)
    appdb.update_program_template(conn, program_id, payload.template_text)
    return {"id": program_id, "template_text": payload.template_text}


# --- Attachments ---


@router.post("/api/campaigns/{program_id}/attachments")
async def upload_attachments(
    program_id: int,
    files: list[UploadFile] = File(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_program(conn, program_id)
    saved = []
    skipped = []
    for file in files:
        file_name = file.filename or ""
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
            skipped.append(
                {
                    "file_name": file_name,
                    "reason": f"Unsupported file type '{ext or '(none)'}'. "
                    "Allowed: .png, .jpg, .jpeg, .pdf, .doc, .docx.",
                }
            )
            continue
        content = await file.read()
        if len(content) > MAX_ATTACHMENT_BYTES:
            skipped.append(
                {"file_name": file_name, "reason": "file too large (max 25 MB)"}
            )
            continue
        attachment_id = appdb.add_attachment(conn, program_id, file_name, content)
        attachment = next(
            a for a in appdb.list_attachments(conn, program_id) if a["id"] == attachment_id
        )
        saved.append(
            {
                "id": attachment_id,
                "file_name": file_name,
                "media_type": attachment["media_type"],
            }
        )
    return {"saved": saved, "skipped": skipped}


@router.get("/api/campaigns/{program_id}/attachments")
def list_campaign_attachments(program_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_program(conn, program_id)
    return [
        {"id": a["id"], "file_name": a["file_name"], "media_type": a["media_type"]}
        for a in appdb.list_attachments(conn, program_id)
    ]


@router.get("/api/attachments/{attachment_id}/file")
def attachment_file(attachment_id: int, conn: sqlite3.Connection = Depends(get_db)):
    """Serve attachment bytes for the WhatsApp-style preview mock.

    Lookup is strictly by DB id; the stored path may be legacy-relative,
    resolved the same way the worker does (app.db._resolve_media_path).
    """
    row = conn.execute(
        "SELECT file_path, file_name FROM program_attachments WHERE id = ?",
        (attachment_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    file_path = os.path.realpath(appdb._resolve_media_path(row[0]))
    media_root = os.path.realpath(appdb.MEDIA_DIR)
    if not file_path.startswith(media_root + os.sep):
        raise HTTPException(status_code=404, detail="Attachment file not found")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Attachment file not found")
    media_type = mimetypes.guess_type(row[1] or "")[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=media_type, filename=row[1])


@router.delete("/api/attachments/{attachment_id}")
def delete_attachment(attachment_id: int, conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute(
        "SELECT id FROM program_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    appdb.delete_attachment(conn, attachment_id)
    return {"deleted": attachment_id}


# --- Preview ---


@router.get("/api/campaigns/{program_id}/preview")
def preview_campaign(program_id: int, conn: sqlite3.Connection = Depends(get_db)):
    program = _require_program(conn, program_id)
    contact = appdb.get_first_contact(conn, program_id)
    if contact is None:
        fields = _sample_preview_fields(program["template_text"])
    else:
        fields = _contact_preview_fields(contact)
    message, missing_fields = _render_template_preview(program["template_text"], fields)
    attachments = _attachment_entries(appdb.list_attachments(conn, program_id))
    return {
        "message": message,
        "missing_fields": missing_fields,
        "attachments": attachments,
        "using_sample_values": contact is None,
        "preview_contact_name": contact[1] if contact else None,
    }


# --- CSV import ---


@router.post("/api/campaigns/{program_id}/contacts/csv/columns")
async def csv_columns(
    program_id: int,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_program(conn, program_id)
    rows = await _read_csv_rows(file)
    columns = list(rows[0].keys())
    return {"columns": columns, "guessed": csv_helpers.guess_all_columns(columns)}


@router.post("/api/campaigns/{program_id}/contacts/csv")
async def csv_preview(
    program_id: int,
    file: UploadFile = File(...),
    phone_column: str = Form(...),
    name_column: str = Form(...),
    startup_name_column: str | None = Form(None),
    email_column: str | None = Form(None),
    extra_columns: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_program(conn, program_id)
    rows = await _read_csv_rows(file)
    valid, invalid = _mapped_contacts(
        rows, phone_column, name_column, startup_name_column, email_column, extra_columns
    )
    invalid_rows = []
    for item in invalid:
        row = item["row"] or {}
        entry = {"row_number": item.get("row_number"), "error": item["error"], "row": row}
        # Like the old Streamlit rejected-table: surface the mapped
        # startup_name / email values when those columns were provided.
        if startup_name_column:
            entry["startup_name"] = row.get(startup_name_column, "")
        if email_column:
            entry["email"] = row.get(email_column, "")
        invalid_rows.append(entry)
    return {
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "valid_preview": [_flatten_valid_contact(contact) for contact in valid[:10]],
        "invalid_rows": invalid_rows,
    }


@router.post("/api/campaigns/{program_id}/contacts/csv/commit")
async def csv_commit(
    program_id: int,
    file: UploadFile = File(...),
    phone_column: str = Form(...),
    name_column: str = Form(...),
    startup_name_column: str | None = Form(None),
    email_column: str | None = Form(None),
    extra_columns: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_program(conn, program_id)
    rows = await _read_csv_rows(file)
    valid, invalid = _mapped_contacts(
        rows, phone_column, name_column, startup_name_column, email_column, extra_columns
    )
    inserted, duplicates = appdb.insert_contacts(conn, program_id, valid)
    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "invalid_count": len(invalid),
    }


# --- Paste-list import ---


def _parse_paste_text(text: str):
    if len(text.encode("utf-8")) > MAX_PASTE_BYTES:
        raise HTTPException(status_code=413, detail="Pasted text too large (max 1 MB).")
    return parse_pasted_contacts(text)


@router.post("/api/campaigns/{program_id}/contacts/paste")
def paste_preview(
    program_id: int, payload: PastedContacts, conn: sqlite3.Connection = Depends(get_db)
):
    _require_program(conn, program_id)
    valid, invalid = _parse_paste_text(payload.text)
    return {
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "valid_preview": [_flatten_valid_contact(contact) for contact in valid[:10]],
        "invalid_rows": invalid,
    }


@router.post("/api/campaigns/{program_id}/contacts/paste/commit")
def paste_commit(
    program_id: int, payload: PastedContacts, conn: sqlite3.Connection = Depends(get_db)
):
    _require_program(conn, program_id)
    valid, invalid = _parse_paste_text(payload.text)
    inserted, duplicates = appdb.insert_contacts(conn, program_id, valid)
    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "invalid_count": len(invalid),
    }


# --- Contacts ---


@router.post("/api/campaigns/{program_id}/contacts", status_code=201)
def add_manual_contact(
    program_id: int, payload: ManualContact, conn: sqlite3.Connection = Depends(get_db)
):
    _require_program(conn, program_id)
    valid, invalid = parse_contacts_rows([{"phone": payload.phone, "name": payload.name}])
    if invalid:
        raise HTTPException(status_code=400, detail=invalid[0]["error"])
    inserted, duplicates = appdb.insert_contacts(conn, program_id, valid)
    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "contact": {"phone": valid[0]["phone"], "name": valid[0]["name"]},
    }


@router.get("/api/campaigns/{program_id}/status")
def campaign_status(program_id: int, conn: sqlite3.Connection = Depends(get_db)):
    program = _require_program(conn, program_id)
    status = appdb.get_status_counts(conn, program_id)
    settings = appdb.get_full_settings(conn)
    delay_seconds = settings["delay_seconds"]
    send_window = evaluate_send_window(
        settings["send_window_start"], settings["send_window_end"], settings["send_timezone"]
    )
    pending_count = status["counts"]["pending"]
    # No ETA while nothing can go out (paused, or outside the sending window).
    eta_minutes = (
        round(pending_count * delay_seconds / 60, 1)
        if pending_count and not program["paused"] and send_window["allowed"]
        else None
    )
    heartbeat = appdb.get_heartbeat(conn)
    sent_today = appdb.count_sent_today(conn)
    cap = settings["daily_cap"]
    return {
        "counts": status["counts"],
        "replied_count": status["replied_count"],
        "delivery_counts": status["delivery_counts"],
        "delivered_count": status["delivered_count"],
        "rejected_count": status["rejected_count"],
        # Plain language, never a bare error number.
        "rejection_reason": status["rejection_reason"],
        "send_window": send_window,
        "daily_cap": cap,
        "sent_today": sent_today,
        "remaining_today": None if cap is None else max(cap - sent_today, 0),
        "eta_minutes": eta_minutes,
        "paused": program["paused"],
        "delay_seconds": delay_seconds,
        # The halt is global, but it must be visible on the campaign card too.
        "halted": heartbeat["halted"],
        "halt_reason": heartbeat["halt_reason"],
    }


@router.get("/api/campaigns/{program_id}/replies")
def campaign_replies(program_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_program(conn, program_id)
    return appdb.list_replies(conn, program_id)


@router.get("/api/campaigns/{program_id}/contacts")
def campaign_contacts(
    program_id: int,
    status: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_program(conn, program_id)
    if status is not None and status not in CONTACT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status filter. Allowed: {', '.join(CONTACT_STATUSES)}.",
        )
    return appdb.list_contacts(conn, program_id, status)


@router.post("/api/campaigns/{program_id}/contacts/retry-failed")
def retry_all_failed(program_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_program(conn, program_id)
    return {"retried": appdb.retry_failed_contacts(conn, program_id)}


@router.post("/api/contacts/retry")
def retry_selected(payload: ContactIds, conn: sqlite3.Connection = Depends(get_db)):
    return {"retried": appdb.retry_contacts_by_ids(conn, payload.ids)}


@router.post("/api/contacts/delete")
def delete_selected(payload: ContactIds, conn: sqlite3.Connection = Depends(get_db)):
    deleted = 0
    skipped_ids = []
    for contact_id in payload.ids:
        if appdb.delete_contact_from_table(conn, contact_id):
            deleted += 1
        else:
            skipped_ids.append(contact_id)
    return {"deleted": deleted, "skipped_ids": skipped_ids}


@router.post("/api/campaigns/{program_id}/needs-review/resolve")
def resolve_needs_review(
    program_id: int,
    payload: NeedsReviewResolution,
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_program(conn, program_id)
    if payload.to not in ("sent", "pending"):
        raise HTTPException(status_code=400, detail="'to' must be 'sent' or 'pending'.")
    return {"updated": appdb.resolve_needs_review(conn, program_id, payload.to)}
