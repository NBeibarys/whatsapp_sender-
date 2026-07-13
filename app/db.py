import json
import os
import sqlite3
import uuid

TEST_PROGRAM_NAME = "Test"


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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


MEDIA_DIR = "media"


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
    return [{"id": r[0], "file_path": r[1], "file_name": r[2], "media_type": r[3]} for r in rows]


def delete_attachment(conn: sqlite3.Connection, attachment_id: int) -> None:
    row = conn.execute(
        "SELECT file_path FROM program_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    if row is None:
        return
    file_path = row[0]
    with conn:
        conn.execute("DELETE FROM program_attachments WHERE id = ?", (attachment_id,))
    if os.path.exists(file_path):
        os.remove(file_path)


def delete_contact(conn: sqlite3.Connection, contact_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM contacts WHERE id = ? AND status = 'pending'", (contact_id,))
