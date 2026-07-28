"""CSV upload helpers: parsing and column-guess heuristics.

The guess heuristics were ported verbatim from the retired Streamlit
Campaign page (_normalized_column_name / _guess_column).
"""

import csv
import io

# Sane upper bound for uploaded contact CSVs (localhost internal tool).
MAX_CSV_BYTES = 5 * 1024 * 1024

PHONE_CANDIDATES = ["phone", "phone number", "mobile", "whatsapp"]
NAME_CANDIDATES = ["name", "full name", "recipient", "startup name", "company"]
STARTUP_CANDIDATES = ["startup name", "startup", "company", "project"]
EMAIL_CANDIDATES = ["email", "e-mail", "mail"]


def normalized_column_name(column_name: str) -> str:
    return "".join(ch.lower() for ch in column_name if ch.isalnum())


def guess_column(columns: list[str], candidates: list[str]) -> str | None:
    normalized_candidates = {normalized_column_name(candidate) for candidate in candidates}
    for column in columns:
        if normalized_column_name(column) in normalized_candidates:
            return column
    for column in columns:
        normalized = normalized_column_name(column)
        if any(candidate in normalized for candidate in normalized_candidates):
            return column
    return None


def guess_all_columns(columns: list[str]) -> dict:
    return {
        "phone": guess_column(columns, PHONE_CANDIDATES),
        "name": guess_column(columns, NAME_CANDIDATES),
        "startup": guess_column(columns, STARTUP_CANDIDATES),
        "email": guess_column(columns, EMAIL_CANDIDATES),
    }


def is_csv_filename(file_name: str | None) -> bool:
    import os

    return os.path.splitext(file_name or "")[1].lower() == ".csv"


def parse_csv_bytes(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))
