"""FastAPI dependencies."""

from collections.abc import Iterator
import sqlite3

from app.db import get_app_connection


def get_db() -> Iterator[sqlite3.Connection]:
    """Yield an app DB connection, closed after the request. Tests override this."""
    conn = get_app_connection()
    try:
        yield conn
    finally:
        conn.close()
