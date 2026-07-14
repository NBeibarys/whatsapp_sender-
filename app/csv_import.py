from app.phone import normalize_phone, InvalidPhoneNumber


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


def _field_key(column_name):
    key = "".join(ch.lower() if ch.isalnum() else "_" for ch in column_name.strip())
    key = "_".join(part for part in key.split("_") if part)
    return key or "field"


def _normalize_mapped_phone(raw, default_region):
    value = str(raw).strip()
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return normalize_phone(value, default_region)

    normalized_input = f"+{digits}"
    if value.startswith("+"):
        normalized_input = f"+{digits}"

    return normalize_phone(normalized_input, default_region)


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
