from app.csv_import import parse_contacts_rows, map_contact_rows


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



def test_map_contact_rows_accepts_startup_name_as_message_name():
    rows = [
        {
            "Startup Name": "Ai teacher assistant",
            "Telegram @": "@Iskandar1996",
            "email": "Irashidov00@gmail.com",
            "phone": "998945122309",
        }
    ]

    valid, invalid = map_contact_rows(
        rows,
        phone_column="phone",
        name_column="Startup Name",
        startup_name_column="Startup Name",
        email_column="email",
        extra_columns=["Telegram @"],
    )

    assert invalid == []
    assert valid[0]["name"] == "Ai teacher assistant"
    assert valid[0]["phone"].startswith("+")
    assert valid[0]["extra_fields"] == {
        "startup_name": "Ai teacher assistant",
        "email": "Irashidov00@gmail.com",
        "telegram": "@Iskandar1996",
    }


def test_map_contact_rows_summarizes_missing_phone_with_row_number():
    rows = [{"Startup Name": "WardrobeAI", "email": "a@example.com", "phone": ""}]

    valid, invalid = map_contact_rows(
        rows,
        phone_column="phone",
        name_column="Startup Name",
        startup_name_column="Startup Name",
        email_column="email",
    )

    assert valid == []
    assert invalid[0]["row_number"] == 1
    assert invalid[0]["error"] == "Missing phone"


def test_map_contact_rows_normalizes_plus_and_ignored_characters():
    rows = [
        {"Startup Name": "A", "phone": "+998 90 166 40 50"},
        {"Startup Name": "B", "phone": "998 (94) 512-23-09"},
    ]

    valid, invalid = map_contact_rows(
        rows,
        phone_column="phone",
        name_column="Startup Name",
        startup_name_column="Startup Name",
    )

    assert invalid == []
    assert [contact["phone"] for contact in valid] == ["+998901664050", "+998945122309"]
