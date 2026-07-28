import os

# Must be set before web.main is imported anywhere: no worker may be spawned
# from tests, and the lifespan/connection endpoints check this at call time.
os.environ["SKIP_AUTO_WORKER"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.db import get_connection
from web.deps import get_db
from web.main import app

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "schema.sql"
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    conn = get_connection(path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()
    return path


@pytest.fixture
def conn(db_path):
    connection = get_connection(db_path)
    yield connection
    connection.close()


@pytest.fixture
def client(db_path, tmp_path, monkeypatch):
    # Attachment uploads must land in a tmp dir, never the real media/.
    monkeypatch.setattr("app.db.MEDIA_DIR", str(tmp_path / "media"))

    def override_get_db():
        connection = get_connection(db_path)
        try:
            yield connection
        finally:
            connection.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
