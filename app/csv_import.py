import re

from app.phone import normalize_phone, InvalidPhoneNumber

# Token that plausibly is a phone number once spaces/dashes/parens are removed.
_PHONE_TOKEN_RE = re.compile(r"^\+?\d{6,15}$")
_PHONE_CLEAN_RE = re.compile(r"[\s\-()]")


def parse_contacts_rows(rows, default_region="KZ"):
    """rows: list of dict per CSV row (at least 'phone' and 'name' columns).

    Returns (valid, invalid):
      valid: list of {"phone": str, "name": str, "extra_fields": dict}
      invalid: list of {"row": dict, "error": str}
    """
    valid = []
    invalid = []

    for row in rows:
        if not row.get("phone"):
            invalid.append({"row": row, "error": "Missing phone"})
            continue
        if not row.get("name"):
            invalid.append({"row": row, "error": "Missing name"})
            continue
        try:
            phone = normalize_phone(row["phone"], default_region)
        except InvalidPhoneNumber as e:
            invalid.append({"row": row, "error": str(e)})
            continue

        extra_fields = {k: v for k, v in row.items() if k not in ("phone", "name")}
        valid.append({"phone": phone, "name": row["name"], "extra_fields": extra_fields})

    return valid, invalid


def _split_paste_line(line):
    """Split a pasted line into fields by the first matching delimiter.

    Priority: tab (Excel paste), ';', ',', then runs of 2+ spaces.
    Single spaces are NOT delimiters so '+7 701 234 5678' stays one token.
    """
    for delimiter in ("\t", ";", ","):
        if delimiter in line:
            parts = line.split(delimiter)
            break
    else:
        parts = re.split(r" {2,}", line)
    return [part.strip() for part in parts if part.strip()]


def _looks_like_phone(token):
    return bool(_PHONE_TOKEN_RE.match(_PHONE_CLEAN_RE.sub("", token)))


def parse_pasted_contacts(text, default_region="KZ"):
    """Parse pasted 'one contact per line' text (e.g. straight from Excel).

    Each line holds a phone plus a name in any order; the first token that
    looks like a phone is the phone, the first remaining token is the name,
    and further tokens are kept as extra_1, extra_2, ... extra fields.

    Returns (valid, invalid):
      valid: list of {"phone": str, "name": str, "extra_fields": dict}
      invalid: list of {"row_number": int, "line": str, "error": str}
    """
    valid = []
    invalid = []
    seen_content_line = False

    for line_number, raw_line in enumerate((text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        is_first_content_line = not seen_content_line
        seen_content_line = True

        tokens = _split_paste_line(line)
        phone_index = next(
            (i for i, token in enumerate(tokens) if _looks_like_phone(token)), None
        )
        if phone_index is None:
            lowered = line.lower()
            if is_first_content_line and ("phone" in lowered or "name" in lowered):
                # Header row pasted along with the data — skip silently.
                continue
            invalid.append(
                {
                    "row_number": line_number,
                    "line": line,
                    "error": "No phone number found in line",
                }
            )
            continue

        rest = tokens[:phone_index] + tokens[phone_index + 1:]
        if not rest:
            invalid.append(
                {"row_number": line_number, "line": line, "error": "Missing name"}
            )
            continue

        try:
            phone = _normalize_mapped_phone(tokens[phone_index], default_region)
        except InvalidPhoneNumber as e:
            invalid.append({"row_number": line_number, "line": line, "error": str(e)})
            continue

        extra_fields = {
            f"extra_{i}": value for i, value in enumerate(rest[1:], start=1)
        }
        valid.append({"phone": phone, "name": rest[0], "extra_fields": extra_fields})

    return valid, invalid


def _field_key(column_name):
    key = "".join(ch.lower() if ch.isalnum() else "_" for ch in column_name.strip())
    key = "_".join(part for part in key.split("_") if part)
    return key or "field"


def _normalize_mapped_phone(raw, default_region):
    value = str(raw).strip()
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return normalize_phone(value, default_region)

    return normalize_phone(f"+{digits}", default_region)


def map_contact_rows(
    rows,
    *,
    phone_column,
    name_column,
    startup_name_column=None,
    email_column=None,
    extra_columns=None,
    default_region="KZ",
):
    """Map arbitrary CSV columns into the app contact shape.

    `name_column` is the value used by {{name}} in messages. Other selected
    columns are preserved in extra_fields using placeholder-friendly keys.
    """
    extra_columns = extra_columns or []
    valid = []
    invalid = []

    for index, row in enumerate(rows, start=1):
        mapped = {
            "phone": row.get(phone_column, "") if phone_column else "",
            "name": row.get(name_column, "") if name_column else "",
        }
        extra_fields = {}
        if startup_name_column:
            extra_fields["startup_name"] = row.get(startup_name_column, "")
        if email_column:
            extra_fields["email"] = row.get(email_column, "")
        for column in extra_columns:
            if column in (phone_column, name_column, startup_name_column, email_column):
                continue
            extra_fields[_field_key(column)] = row.get(column, "")

        if not mapped["phone"]:
            invalid.append({"row_number": index, "row": row, "error": "Missing phone"})
            continue
        if not mapped["name"]:
            invalid.append({"row_number": index, "row": row, "error": "Missing name"})
            continue
        try:
            phone = _normalize_mapped_phone(mapped["phone"], default_region)
        except InvalidPhoneNumber as e:
            invalid.append({"row_number": index, "row": row, "error": str(e)})
            continue

        valid.append({"phone": phone, "name": mapped["name"], "extra_fields": extra_fields})

    return valid, invalid
