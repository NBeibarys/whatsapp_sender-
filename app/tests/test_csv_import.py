from app.csv_import import parse_contacts_rows


def test_parse_valid_rows():
    rows = [{"phone": "+77012345678", "name": "Aigerim", "program": "Fall Cohort"}]
    valid, invalid = parse_contacts_rows(rows)

    assert len(valid) == 1
    assert valid[0]["phone"] == "+77012345678"
    assert valid[0]["name"] == "Aigerim"
    assert valid[0]["extra_fields"] == {"program": "Fall Cohort"}
    assert invalid == []


def test_parse_rejects_missing_phone():
    rows = [{"phone": "", "name": "Aigerim"}]
    valid, invalid = parse_contacts_rows(rows)

    assert valid == []
    assert len(invalid) == 1
    assert invalid[0]["error"] == "Missing phone"


def test_parse_rejects_missing_name():
    rows = [{"phone": "+77012345678", "name": ""}]
    valid, invalid = parse_contacts_rows(rows)

    assert valid == []
    assert len(invalid) == 1
    assert invalid[0]["error"] == "Missing name"


def test_parse_rejects_invalid_phone():
    rows = [{"phone": "not-a-phone", "name": "Aigerim"}]
    valid, invalid = parse_contacts_rows(rows)

    assert valid == []
    assert len(invalid) == 1
    assert "phone" in invalid[0]["error"].lower()
