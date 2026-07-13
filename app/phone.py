import phonenumbers


class InvalidPhoneNumber(Exception):
    pass


def normalize_phone(raw: str, default_region: str = "KZ") -> str:
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException as e:
        raise InvalidPhoneNumber(f"Could not parse phone number: {raw}") from e

    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneNumber(f"Invalid phone number: {raw}")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
