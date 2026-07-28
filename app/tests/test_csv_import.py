from app.csv_import import parse_contacts_rows, map_contact_rows, parse_pasted_contacts


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


def test_paste_tab_separated_phone_first():
    valid, invalid = parse_pasted_contacts("+7 701 234 5678\tAigerim")

    assert invalid == []
    assert valid == [{"phone": "+77012345678", "name": "Aigerim", "extra_fields": {}}]


def test_paste_comma_separated_name_first():
    valid, invalid = parse_pasted_contacts("Bekzat, +7 701 234 5679")

    assert invalid == []
    assert valid[0]["phone"] == "+77012345679"
    assert valid[0]["name"] == "Bekzat"


def test_paste_semicolon_extra_tokens_become_extra_fields():
    valid, invalid = parse_pasted_contacts("77012345678; Dana; Astana Hub; seed round")

    assert invalid == []
    assert valid[0]["phone"] == "+77012345678"
    assert valid[0]["name"] == "Dana"
    assert valid[0]["extra_fields"] == {"extra_1": "Astana Hub", "extra_2": "seed round"}


def test_paste_multi_space_separated():
    valid, invalid = parse_pasted_contacts("Aigerim   +7 (701) 234-56-78")

    assert invalid == []
    assert valid[0]["phone"] == "+77012345678"
    assert valid[0]["name"] == "Aigerim"


def test_paste_header_line_skipped_silently():
    valid, invalid = parse_pasted_contacts("Phone Number\tName\n+77012345678\tAigerim")

    assert invalid == []
    assert len(valid) == 1
    assert valid[0]["name"] == "Aigerim"


def test_paste_no_phone_after_first_line_is_invalid():
    valid, invalid = parse_pasted_contacts("+77012345678\tAigerim\njust some words")

    assert len(valid) == 1
    assert len(invalid) == 1
    assert invalid[0]["error"] == "No phone number found in line"


def test_paste_blank_lines_skipped_and_row_numbers_original():
    valid, invalid = parse_pasted_contacts("\n+77012345678\tAigerim\n\nAstana office only\n")

    assert len(valid) == 1
    assert len(invalid) == 1
    assert invalid[0]["row_number"] == 4
    assert invalid[0]["line"] == "Astana office only"


def test_paste_phone_only_line_missing_name():
    valid, invalid = parse_pasted_contacts("+77012345678")

    assert valid == []
    assert invalid[0]["error"] == "Missing name"


def test_paste_bad_phone_reports_error():
    valid, invalid = parse_pasted_contacts("+712345678901\tAigerim")

    assert valid == []
    assert len(invalid) == 1
    assert "phone" in invalid[0]["error"].lower()
