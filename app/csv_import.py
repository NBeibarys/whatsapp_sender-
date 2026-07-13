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
