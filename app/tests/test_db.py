import os
import tempfile
import pytest
from app.db import (
    ADDED_COLUMNS,
    APP_DB_PATH,
    apply_migrations,
    clear_halt,
    get_connection,
    get_heartbeat,
    get_settings,
    get_status_counts,
    create_program,
    insert_contacts,
    add_attachment,
    list_attachments,
    list_contacts,
    list_replies,
    delete_attachment,
    delete_contact,
    delete_contact_from_table,
    retry_failed_contacts,
    save_settings,
)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "schema.sql")


@pytest.fixture
def conn():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    connection = get_connection(db_path)
    with open(SCHEMA_PATH) as f:
        connection.executescript(f.read())
    yield connection
    connection.close()


def test_create_program_returns_id(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")

    assert isinstance(program_id, int)
    row = conn.execute(
        "SELECT name, template_text FROM programs WHERE id = ?", (program_id,)
    ).fetchone()
    assert row[0] == "Fall Cohort"
    assert row[1] == "Hi {{name}}"


def test_insert_contacts_inserts_valid_rows(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {"program": "Fall Cohort"}}]

    inserted, duplicates = insert_contacts(conn, program_id, valid)

    assert inserted == 1
    assert duplicates == []
    row = conn.execute(
        "SELECT phone, status FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()
    assert row[0] == "+77012345678"
    assert row[1] == "pending"


def test_insert_contacts_flags_duplicates(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {}}]
    insert_contacts(conn, program_id, valid)

    inserted, duplicates = insert_contacts(conn, program_id, valid)

    assert inserted == 0
    assert duplicates == ["+77012345678"]


def test_add_attachment_saves_file_and_returns_id(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.MEDIA_DIR", str(tmp_path / "media"))
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")

    attachment_id = add_attachment(conn, program_id, "flyer.png", b"fake-image-bytes")

    assert isinstance(attachment_id, int)
    attachments = list_attachments(conn, program_id)
    assert len(attachments) == 1
    assert attachments[0]["file_name"] == "flyer.png"
    assert attachments[0]["media_type"] == "image"
    assert os.path.exists(attachments[0]["file_path"])


def test_add_attachment_classifies_non_image_as_document(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.MEDIA_DIR", str(tmp_path / "media"))
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")

    add_attachment(conn, program_id, "brochure.pdf", b"fake-pdf-bytes")

    attachments = list_attachments(conn, program_id)
    assert attachments[0]["media_type"] == "document"


def test_delete_attachment_removes_row_and_file(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.MEDIA_DIR", str(tmp_path / "media"))
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    attachment_id = add_attachment(conn, program_id, "flyer.png", b"fake-image-bytes")
    file_path = list_attachments(conn, program_id)[0]["file_path"]

    delete_attachment(conn, attachment_id)

    assert list_attachments(conn, program_id) == []
    assert not os.path.exists(file_path)


def test_delete_contact_removes_pending_contact(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {}}]
    insert_contacts(conn, program_id, valid)
    contact_id = conn.execute(
        "SELECT id FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()[0]

    delete_contact(conn, contact_id)

    row = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    assert row is None


def test_delete_contact_does_not_delete_a_sent_contact(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {}}]
    insert_contacts(conn, program_id, valid)
    contact_id = conn.execute(
        "SELECT id FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()[0]
    conn.execute("UPDATE contacts SET status = 'sent' WHERE id = ?", (contact_id,))
    conn.commit()

    delete_contact(conn, contact_id)

    row = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    assert row is not None


def test_delete_contact_from_table_removes_sent_contact(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {}}]
    insert_contacts(conn, program_id, valid)
    contact_id = conn.execute(
        "SELECT id FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()[0]
    conn.execute("UPDATE contacts SET status = 'sent' WHERE id = ?", (contact_id,))
    conn.commit()

    deleted = delete_contact_from_table(conn, contact_id)

    assert deleted is True
    row = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    assert row is None


def test_delete_contact_from_table_keeps_sending_contact(conn):
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")
    valid = [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {}}]
    insert_contacts(conn, program_id, valid)
    contact_id = conn.execute(
        "SELECT id FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()[0]
    conn.execute("UPDATE contacts SET status = 'sending' WHERE id = ?", (contact_id,))
    conn.commit()

    deleted = delete_contact_from_table(conn, contact_id)

    assert deleted is False
    row = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    assert row is not None


def test_app_db_path_is_repo_anchored_when_cwd_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    expected = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "silkroad.db")
    )

    assert APP_DB_PATH == expected


def test_save_settings_updates_singleton_row(conn):
    save_settings(
        conn,
        dry_run=False,
        delay_seconds=12,
        jitter_seconds=3,
        daily_cap=0,
    )

    row = conn.execute(
        "SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1"
    ).fetchone()
    assert row == (12, 3, None, 0)


def test_save_settings_creates_missing_singleton_row(conn):
    conn.execute("DELETE FROM settings WHERE id = 1")
    conn.commit()

    save_settings(
        conn,
        dry_run=True,
        delay_seconds=20,
        jitter_seconds=4,
        daily_cap=50,
    )

    row = conn.execute(
        "SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1"
    ).fetchone()
    assert row == (20, 4, 50, 1)


def test_get_settings_creates_default_row_when_missing(conn):
    conn.execute("DELETE FROM settings WHERE id = 1")
    conn.commit()

    row = get_settings(conn)

    assert row == (60, 15, None, 1)
    saved = conn.execute(
        "SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1"
    ).fetchone()
    assert saved == row


# --- Additive schema migration (mirror of worker/migrate.js) ---

LEGACY_SCHEMA = """
CREATE TABLE programs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  template_text TEXT NOT NULL,
  paused INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  program_id INTEGER NOT NULL REFERENCES programs(id),
  phone TEXT NOT NULL,
  name TEXT NOT NULL,
  extra_fields TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','sending','sent','failed','needs_review')),
  rendered_message TEXT,
  sent_at TEXT,
  error_message TEXT,
  replied_at TEXT,
  UNIQUE(program_id, phone)
);
CREATE TABLE worker_heartbeat (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_seen TEXT,
  qr_code TEXT,
  disconnect_requested INTEGER NOT NULL DEFAULT 0,
  connected INTEGER NOT NULL DEFAULT 0
);
INSERT INTO worker_heartbeat (id, last_seen) VALUES (1, NULL);
"""


@pytest.fixture
def legacy_conn():
    """A database in the shape it had before delivery tracking existed."""
    tmpdir = tempfile.mkdtemp()
    connection = get_connection(os.path.join(tmpdir, "legacy.db"))
    connection.executescript(LEGACY_SCHEMA)
    yield connection
    connection.close()


def _columns(connection, table):
    return {r[1] for r in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def test_apply_migrations_upgrades_a_pre_migration_database(legacy_conn):
    program_id = create_program(legacy_conn, "Old", "Hi {{name}}")
    legacy_conn.execute(
        "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, '+77010000001', 'Old', 'sent')",
        (program_id,),
    )
    legacy_conn.commit()

    applied = apply_migrations(legacy_conn)

    assert applied
    assert {"wa_message_id", "delivery_state", "ack_error", "delivered_at", "read_at"} <= _columns(
        legacy_conn, "contacts"
    )
    assert {"halted_at", "halt_reason"} <= _columns(legacy_conn, "worker_heartbeat")
    assert legacy_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'replies'"
    ).fetchone()
    # Existing rows survive.
    row = legacy_conn.execute("SELECT status FROM contacts WHERE phone = '+77010000001'").fetchone()
    assert row[0] == "sent"


def test_apply_migrations_is_idempotent(legacy_conn):
    first = apply_migrations(legacy_conn)
    second = apply_migrations(legacy_conn)

    assert first
    assert second == []


def test_apply_migrations_is_a_noop_on_a_current_schema(conn):
    assert apply_migrations(conn) == []


def test_apply_migrations_matches_the_worker_column_list():
    """Both migrations must add the same columns (worker/migrate.js is the mirror)."""
    import json
    import subprocess

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.run(
        [
            "node",
            "-e",
            "console.log(JSON.stringify(require('./worker/migrate').ADDED_COLUMNS))",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) == ADDED_COLUMNS


# --- Delivery state + replies helpers ---


def test_get_status_counts_reports_delivery_counts(conn):
    program_id = create_program(conn, "Delivery", "Hi {{name}}")
    insert_contacts(
        conn,
        program_id,
        [
            {"phone": "+77010000001", "name": "A", "extra_fields": {}},
            {"phone": "+77010000002", "name": "B", "extra_fields": {}},
            {"phone": "+77010000003", "name": "C", "extra_fields": {}},
        ],
    )
    conn.execute("UPDATE contacts SET status='sent', delivery_state='delivered' WHERE phone='+77010000001'")
    conn.execute("UPDATE contacts SET status='sent', delivery_state='read' WHERE phone='+77010000002'")
    conn.execute(
        "UPDATE contacts SET status='failed', delivery_state='rejected', ack_error='463' "
        "WHERE phone='+77010000003'"
    )
    conn.commit()

    status = get_status_counts(conn, program_id)

    assert status["delivery_counts"]["delivered"] == 1
    assert status["delivery_counts"]["read"] == 1
    assert status["delivery_counts"]["rejected"] == 1
    assert status["delivered_count"] == 2  # read implies delivered
    assert status["rejected_count"] == 1


def test_list_contacts_exposes_the_delivery_fields(conn):
    program_id = create_program(conn, "Fields", "Hi {{name}}")
    insert_contacts(conn, program_id, [{"phone": "+77010000001", "name": "A", "extra_fields": {}}])
    conn.execute(
        "UPDATE contacts SET status='failed', delivery_state='rejected', ack_error='463', "
        "wa_message_id='MSG1' WHERE phone='+77010000001'"
    )
    conn.commit()

    contact = list_contacts(conn, program_id)[0]

    assert contact["delivery_state"] == "rejected"
    assert contact["ack_error"] == "463"
    assert contact["wa_message_id"] == "MSG1"
    assert "delivered_at" in contact and "read_at" in contact


def test_retrying_a_rejected_contact_clears_its_delivery_trail(conn):
    program_id = create_program(conn, "Retry", "Hi {{name}}")
    insert_contacts(conn, program_id, [{"phone": "+77010000001", "name": "A", "extra_fields": {}}])
    conn.execute(
        "UPDATE contacts SET status='failed', delivery_state='rejected', ack_error='463', "
        "error_message='nope', wa_message_id='MSG1' WHERE phone='+77010000001'"
    )
    conn.commit()

    assert retry_failed_contacts(conn, program_id) == 1

    contact = list_contacts(conn, program_id)[0]
    assert contact["status"] == "pending"
    assert contact["delivery_state"] is None
    assert contact["ack_error"] is None
    assert contact["wa_message_id"] is None
    assert contact["error_message"] is None


def test_halt_state_round_trips_through_the_heartbeat(conn):
    assert get_heartbeat(conn)["halted"] is False

    conn.execute(
        "UPDATE worker_heartbeat SET halted_at = '2026-07-28T20:00:00Z', halt_reason = 'restricted'"
    )
    conn.commit()
    heartbeat = get_heartbeat(conn)
    assert heartbeat["halted"] is True
    assert heartbeat["halt_reason"] == "restricted"
    assert heartbeat["halted_at"] == "2026-07-28T20:00:00Z"

    clear_halt(conn)
    assert get_heartbeat(conn)["halted"] is False


def test_list_replies_returns_newest_first_for_the_campaign(conn):
    program_id = create_program(conn, "Inbox", "Hi {{name}}")
    insert_contacts(conn, program_id, [{"phone": "+77010000001", "name": "A", "extra_fields": {}}])
    contact_id = conn.execute("SELECT id FROM contacts").fetchone()[0]
    conn.executemany(
        "INSERT INTO replies (contact_id, phone, body, received_at) VALUES (?, ?, ?, ?)",
        [
            (contact_id, "+77010000001", "first", "2026-07-28T10:00:00Z"),
            (contact_id, "+77010000001", "second", "2026-07-28T11:00:00Z"),
        ],
    )
    conn.commit()

    replies = list_replies(conn, program_id)

    assert [r["body"] for r in replies] == ["second", "first"]
    assert replies[0]["name"] == "A"
    assert replies[0]["phone"] == "+77010000001"
