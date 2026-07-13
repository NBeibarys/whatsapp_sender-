import os
import tempfile
import pytest
from app.db import get_connection, create_program

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
