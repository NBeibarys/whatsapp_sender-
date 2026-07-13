import os
import tempfile
import pytest
from app.db import get_connection, create_program, insert_contacts

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
