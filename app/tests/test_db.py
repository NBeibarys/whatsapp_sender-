import os
import tempfile
import pytest
from app.db import (
    APP_DB_PATH,
    get_connection,
    get_settings,
    create_program,
    insert_contacts,
    add_attachment,
    list_attachments,
    delete_attachment,
    delete_contact,
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
    monkeypatch.chdir(tmp_path)
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")

    attachment_id = add_attachment(conn, program_id, "flyer.png", b"fake-image-bytes")

    assert isinstance(attachment_id, int)
    attachments = list_attachments(conn, program_id)
    assert len(attachments) == 1
    assert attachments[0]["file_name"] == "flyer.png"
    assert attachments[0]["media_type"] == "image"
    assert os.path.exists(attachments[0]["file_path"])


def test_add_attachment_classifies_non_image_as_document(conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    program_id = create_program(conn, "Fall Cohort", "Hi {{name}}")

    add_attachment(conn, program_id, "brochure.pdf", b"fake-pdf-bytes")

    attachments = list_attachments(conn, program_id)
    assert attachments[0]["media_type"] == "document"


def test_delete_attachment_removes_row_and_file(conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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

    assert row == (60, 0, None, 1)
    saved = conn.execute(
        "SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1"
    ).fetchone()
    assert saved == row
