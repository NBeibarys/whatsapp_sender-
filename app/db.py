import json
import os
import sqlite3
import uuid

from app.config import DB_PATH as APP_DB_PATH
from app.config import MEDIA_DIR, PROJECT_ROOT

TEST_PROGRAM_NAME = "Test"


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = APP_DB_PATH
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # check_same_thread=False: FastAPI serves sync deps and async endpoints on
    # different threads; each connection is still used by one request at a time.
    conn = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_app_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(APP_DB_PATH), exist_ok=True)
    return get_connection(APP_DB_PATH)


DEFAULT_DELAY_SECONDS = 60
DEFAULT_JITTER_SECONDS = 15
DEFAULT_DAILY_CAP = None
DEFAULT_DRY_RUN = 1


def save_settings(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    delay_seconds: int,
    jitter_seconds: int,
    daily_cap: int | None,
) -> None:
    cap_value = daily_cap if daily_cap and daily_cap > 0 else None
    with conn:
        conn.execute(
            """
            INSERT INTO settings (id, dry_run, delay_seconds, jitter_seconds, daily_cap)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                dry_run = excluded.dry_run,
                delay_seconds = excluded.delay_seconds,
                jitter_seconds = excluded.jitter_seconds,
                daily_cap = excluded.daily_cap
            """,
            (int(dry_run), delay_seconds, jitter_seconds, cap_value),
        )


def get_settings(conn: sqlite3.Connection) -> tuple[int, int, int | None, int]:
    row = conn.execute(
        "SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1"
    ).fetchone()
    if row is not None:
        return row

    save_settings(
        conn,
        dry_run=bool(DEFAULT_DRY_RUN),
        delay_seconds=DEFAULT_DELAY_SECONDS,
        jitter_seconds=DEFAULT_JITTER_SECONDS,
        daily_cap=DEFAULT_DAILY_CAP,
    )
    return (DEFAULT_DELAY_SECONDS, DEFAULT_JITTER_SECONDS, DEFAULT_DAILY_CAP, DEFAULT_DRY_RUN)


def create_program(conn: sqlite3.Connection, name: str, template_text: str) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO programs (name, template_text) VALUES (?, ?)",
            (name, template_text),
        )
        return cur.lastrowid


def insert_contacts(conn: sqlite3.Connection, program_id: int, valid_contacts: list) -> tuple:
    """valid_contacts: list of {"phone", "name", "extra_fields"}.
    Returns (inserted_count, duplicate_phones).
    """
    inserted = 0
    duplicates = []
    with conn:
        for c in valid_contacts:
            try:
                conn.execute(
                    "INSERT INTO contacts (program_id, phone, name, extra_fields, status) "
                    "VALUES (?, ?, ?, ?, 'pending')",
                    (program_id, c["phone"], c["name"], json.dumps(c["extra_fields"])),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                duplicates.append(c["phone"])
    return inserted, duplicates


def _resolve_media_path(file_path: str) -> str:
    """Existing rows may store relative paths like 'media/2/xxx.png';
    resolve those against PROJECT_ROOT. New rows store absolute paths."""
    if os.path.isabs(file_path):
        return file_path
    return os.path.join(PROJECT_ROOT, file_path)


def add_attachment(conn: sqlite3.Connection, program_id: int, file_name: str, content: bytes) -> int:
    ext = os.path.splitext(file_name)[1].lower()
    media_type = "image" if ext in (".jpg", ".jpeg", ".png") else "document"
    program_dir = os.path.join(MEDIA_DIR, str(program_id))
    os.makedirs(program_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}-{os.path.basename(file_name)}"
    file_path = os.path.join(program_dir, stored_name)
    with open(file_path, "wb") as f:
        f.write(content)
    with conn:
        cur = conn.execute(
            "INSERT INTO program_attachments (program_id, file_path, file_name, media_type) "
            "VALUES (?, ?, ?, ?)",
            (program_id, file_path, file_name, media_type),
        )
        return cur.lastrowid


def list_attachments(conn: sqlite3.Connection, program_id: int) -> list:
    rows = conn.execute(
        "SELECT id, file_path, file_name, media_type FROM program_attachments "
        "WHERE program_id = ? ORDER BY id",
        (program_id,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "file_path": _resolve_media_path(r[1]),
            "file_name": r[2],
            "media_type": r[3],
        }
        for r in rows
    ]


def delete_attachment(conn: sqlite3.Connection, attachment_id: int) -> None:
    row = conn.execute(
        "SELECT file_path FROM program_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    if row is None:
        return
    file_path = _resolve_media_path(row[0])
    with conn:
        conn.execute("DELETE FROM program_attachments WHERE id = ?", (attachment_id,))
    if os.path.exists(file_path):
        os.remove(file_path)


def delete_contact(conn: sqlite3.Connection, contact_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM contacts WHERE id = ? AND status = 'pending'", (contact_id,))


def delete_contact_from_table(conn: sqlite3.Connection, contact_id: int) -> bool:
    with conn:
        result = conn.execute(
            "DELETE FROM contacts WHERE id = ? AND status != 'sending'", (contact_id,)
        )
    return result.rowcount > 0


# --- Helpers for the web UI (keep SQL out of route handlers) ---


def get_program(conn: sqlite3.Connection, program_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, name, template_text, paused FROM programs WHERE id = ?",
        (program_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "template_text": row[2], "paused": bool(row[3])}


def get_program_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute(
        "SELECT id, name, template_text, paused FROM programs WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "template_text": row[2], "paused": bool(row[3])}


def list_programs(conn: sqlite3.Connection) -> list[dict]:
    """Sidebar list: campaigns (excluding the reserved Test program) with sent/total."""
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.paused,
               COALESCE(SUM(CASE WHEN c.status = 'sent' THEN 1 ELSE 0 END), 0) AS sent,
               COUNT(c.id) AS total
        FROM programs p
        LEFT JOIN contacts c ON c.program_id = p.id
        WHERE p.name != ?
        GROUP BY p.id
        ORDER BY p.name
        """,
        (TEST_PROGRAM_NAME,),
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "paused": bool(r[2]), "sent": r[3], "total": r[4]}
        for r in rows
    ]


def set_program_paused(conn: sqlite3.Connection, program_id: int, paused: bool) -> None:
    with conn:
        conn.execute("UPDATE programs SET paused = ? WHERE id = ?", (int(paused), program_id))


def update_program_template(conn: sqlite3.Connection, program_id: int, template_text: str) -> None:
    with conn:
        conn.execute(
            "UPDATE programs SET template_text = ? WHERE id = ?", (template_text, program_id)
        )


def get_status_counts(conn: sqlite3.Connection, program_id: int) -> dict:
    counts = dict(
        conn.execute(
            "SELECT status, COUNT(*) FROM contacts WHERE program_id = ? GROUP BY status",
            (program_id,),
        ).fetchall()
    )
    replied_count = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE program_id = ? AND replied_at IS NOT NULL",
        (program_id,),
    ).fetchone()[0]
    return {
        "counts": {
            status: counts.get(status, 0)
            for status in ("pending", "sending", "sent", "failed", "needs_review")
        },
        "replied_count": replied_count,
    }


def list_contacts(conn: sqlite3.Connection, program_id: int, status: str | None = None) -> list[dict]:
    query = (
        "SELECT id, phone, name, status, sent_at, replied_at, error_message FROM contacts "
        "WHERE program_id = ?"
    )
    params: list = [program_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY status, id"
    rows = conn.execute(query, params).fetchall()
    return [
        {
            "id": r[0],
            "phone": r[1],
            "name": r[2],
            "status": r[3],
            "sent_at": r[4],
            "replied_at": r[5],
            "error_message": r[6],
        }
        for r in rows
    ]


def get_first_contact(conn: sqlite3.Connection, program_id: int):
    """First queued contact (for template preview): (id, name, extra_fields_json) or None."""
    return conn.execute(
        "SELECT id, name, extra_fields FROM contacts WHERE program_id = ? ORDER BY id LIMIT 1",
        (program_id,),
    ).fetchone()


def retry_failed_contacts(conn: sqlite3.Connection, program_id: int) -> int:
    with conn:
        result = conn.execute(
            "UPDATE contacts SET status = 'pending', error_message = NULL "
            "WHERE program_id = ? AND status = 'failed'",
            (program_id,),
        )
    return result.rowcount


def retry_contacts_by_ids(conn: sqlite3.Connection, contact_ids: list[int]) -> int:
    retried = 0
    with conn:
        for contact_id in contact_ids:
            result = conn.execute(
                "UPDATE contacts SET status = 'pending', error_message = NULL "
                "WHERE id = ? AND status = 'failed'",
                (contact_id,),
            )
            retried += result.rowcount
    return retried


def resolve_needs_review(conn: sqlite3.Connection, program_id: int, to_status: str) -> int:
    if to_status not in ("sent", "pending"):
        raise ValueError(f"Invalid needs_review resolution: {to_status}")
    with conn:
        if to_status == "sent":
            result = conn.execute(
                "UPDATE contacts SET status = 'sent' "
                "WHERE program_id = ? AND status = 'needs_review'",
                (program_id,),
            )
        else:
            result = conn.execute(
                "UPDATE contacts SET status = 'pending', error_message = NULL "
                "WHERE program_id = ? AND status = 'needs_review'",
                (program_id,),
            )
    return result.rowcount


def get_heartbeat(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT last_seen, qr_code, connected, disconnect_requested "
        "FROM worker_heartbeat WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"last_seen": None, "qr_code": None, "connected": False, "disconnect_requested": False}
    return {
        "last_seen": row[0],
        "qr_code": row[1],
        "connected": bool(row[2]),
        "disconnect_requested": bool(row[3]),
    }


def clear_qr_code(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("UPDATE worker_heartbeat SET qr_code = NULL WHERE id = 1")


def request_disconnect(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            "UPDATE worker_heartbeat "
            "SET disconnect_requested = 1, connected = 0, qr_code = NULL WHERE id = 1"
        )
