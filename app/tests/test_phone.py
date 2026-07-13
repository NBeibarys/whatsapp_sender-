import pytest
from app.phone import normalize_phone, InvalidPhoneNumber


def test_normalize_phone_with_country_code():
    assert normalize_phone("+77012345678") == "+77012345678"


def test_normalize_phone_local_format_uses_default_region():
    assert normalize_phone("7012345678", default_region="KZ") == "+77012345678"


def test_normalize_phone_invalid_raises():
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone("not-a-phone")
