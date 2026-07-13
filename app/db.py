import json
import sqlite3


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
