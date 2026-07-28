import io
import json
import os


def _create_campaign(client, name="Fall Cohort", template="Hi {{name}}"):
    resp = client.post("/api/campaigns", json={"name": name, "template_text": template})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_create_campaign_strips_name(client):
    resp = client.post(
        "/api/campaigns", json={"name": "  Fall Cohort  ", "template_text": "Hi {{name}}"}
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Fall Cohort"


def test_create_campaign_rejects_empty_name_and_template(client):
    resp = client.post("/api/campaigns", json={"name": "   ", "template_text": "Hi"})
    assert resp.status_code == 400
    resp = client.post("/api/campaigns", json={"name": "Cohort", "template_text": "  "})
    assert resp.status_code == 400


def test_create_campaign_rejects_reserved_name(client):
    resp = client.post("/api/campaigns", json={"name": "Test", "template_text": "Hi"})
    assert resp.status_code == 400
    assert "reserved" in resp.json()["detail"]


def test_create_campaign_rejects_duplicate_name(client):
    _create_campaign(client)
    resp = client.post(
        "/api/campaigns", json={"name": "Fall Cohort", "template_text": "Hi {{name}}"}
    )
    assert resp.status_code == 409


def test_list_campaigns_excludes_test_program(client, conn):
    _create_campaign(client)
    conn.execute(
        "INSERT INTO programs (name, template_text) VALUES ('Test', 'Hi {{name}}')"
    )
    conn.commit()
    resp = client.get("/api/campaigns")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert names == ["Fall Cohort"]


def test_pause_and_resume(client):
    program_id = _create_campaign(client)

    resp = client.post(f"/api/campaigns/{program_id}/pause")
    assert resp.status_code == 200
    assert resp.json() == {"id": program_id, "paused": True}

    resp = client.post(f"/api/campaigns/{program_id}/resume")
    assert resp.status_code == 200
    assert resp.json() == {"id": program_id, "paused": False}


def test_pause_missing_campaign_404(client):
    resp = client.post("/api/campaigns/999/pause")
    assert resp.status_code == 404


def test_update_template(client, conn):
    program_id = _create_campaign(client)
    resp = client.put(
        f"/api/campaigns/{program_id}/template", json={"template_text": "Hello {{name}}!"}
    )
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT template_text FROM programs WHERE id = ?", (program_id,)
    ).fetchone()
    assert row[0] == "Hello {{name}}!"


def test_manual_contact_add_and_invalid_phone(client):
    program_id = _create_campaign(client)

    resp = client.post(
        f"/api/campaigns/{program_id}/contacts",
        json={"phone": "+77012345678", "name": "Aigerim"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["inserted"] == 1
    assert body["contact"]["phone"] == "+77012345678"

    resp = client.post(
        f"/api/campaigns/{program_id}/contacts",
        json={"phone": "not-a-phone", "name": "Aigerim"},
    )
    assert resp.status_code == 400


def test_manual_contact_duplicate_reported(client):
    program_id = _create_campaign(client)
    payload = {"phone": "+77012345678", "name": "Aigerim"}
    client.post(f"/api/campaigns/{program_id}/contacts", json=payload)
    resp = client.post(f"/api/campaigns/{program_id}/contacts", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["inserted"] == 0
    assert body["duplicates"] == ["+77012345678"]


CSV_CONTENT = (
    "Phone Number,Full Name,Startup,E-mail,City\n"
    "+77012345678,Aigerim,Acme,a@example.com,Almaty\n"
    "+77012345679,Bekzat,Beta,b@example.com,Astana\n"
    "bad,NoPhone,Gamma,c@example.com,Atyrau\n"
)


def _csv_file():
    return {"file": ("contacts.csv", io.BytesIO(CSV_CONTENT.encode()), "text/csv")}


def test_csv_columns_guesses(client):
    program_id = _create_campaign(client)
    resp = client.post(f"/api/campaigns/{program_id}/contacts/csv/columns", files=_csv_file())
    assert resp.status_code == 200
    body = resp.json()
    assert body["columns"] == ["Phone Number", "Full Name", "Startup", "E-mail", "City"]
    assert body["guessed"]["phone"] == "Phone Number"
    assert body["guessed"]["name"] == "Full Name"
    assert body["guessed"]["startup"] == "Startup"
    assert body["guessed"]["email"] == "E-mail"


def test_csv_columns_rejects_non_csv(client):
    program_id = _create_campaign(client)
    resp = client.post(
        f"/api/campaigns/{program_id}/contacts/csv/columns",
        files={"file": ("contacts.xlsx", io.BytesIO(b"junk"), "application/octet-stream")},
    )
    assert resp.status_code == 400


def test_csv_preview(client):
    program_id = _create_campaign(client)
    resp = client.post(
        f"/api/campaigns/{program_id}/contacts/csv",
        files=_csv_file(),
        data={
            "phone_column": "Phone Number",
            "name_column": "Full Name",
            "startup_name_column": "Startup",
            "email_column": "E-mail",
            "extra_columns": json.dumps(["City"]),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid_count"] == 2
    assert body["invalid_count"] == 1
    assert body["valid_preview"][0] == {
        "phone": "+77012345678",
        "name": "Aigerim",
        "startup_name": "Acme",
        "email": "a@example.com",
        "city": "Almaty",
    }
    assert body["invalid_rows"][0]["row_number"] == 3


def test_csv_commit_and_duplicates(client, conn):
    program_id = _create_campaign(client)
    data = {
        "phone_column": "Phone Number",
        "name_column": "Full Name",
        "extra_columns": json.dumps([]),
    }

    resp = client.post(
        f"/api/campaigns/{program_id}/contacts/csv/commit", files=_csv_file(), data=data
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] == 2
    assert body["duplicates"] == []
    assert body["invalid_count"] == 1

    count = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()[0]
    assert count == 2

    # Committing the same file again reports duplicates.
    resp = client.post(
        f"/api/campaigns/{program_id}/contacts/csv/commit", files=_csv_file(), data=data
    )
    body = resp.json()
    assert body["inserted"] == 0
    assert sorted(body["duplicates"]) == ["+77012345678", "+77012345679"]


def test_status_counts_and_eta(client, conn):
    program_id = _create_campaign(client)
    for phone, status in [
        ("+77012345671", "pending"),
        ("+77012345672", "pending"),
        ("+77012345673", "sent"),
        ("+77012345674", "failed"),
    ]:
        conn.execute(
            "INSERT INTO contacts (program_id, phone, name, status) VALUES (?, ?, 'X', ?)",
            (program_id, phone, status),
        )
    conn.execute(
        "UPDATE contacts SET replied_at = '2026-01-01T00:00:00Z' WHERE phone = '+77012345673'"
    )
    conn.commit()

    resp = client.get(f"/api/campaigns/{program_id}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {
        "pending": 2,
        "sending": 0,
        "sent": 1,
        "failed": 1,
        "needs_review": 0,
    }
    assert body["replied_count"] == 1
    assert body["paused"] is False
    assert body["delay_seconds"] == 60
    assert body["eta_minutes"] == 2.0

    # ETA is null when paused.
    client.post(f"/api/campaigns/{program_id}/pause")
    resp = client.get(f"/api/campaigns/{program_id}/status")
    assert resp.json()["eta_minutes"] is None


def test_contacts_list_and_status_filter(client, conn):
    program_id = _create_campaign(client)
    conn.execute(
        "INSERT INTO contacts (program_id, phone, name, status) "
        "VALUES (?, '+77012345671', 'A', 'pending'), (?, '+77012345672', 'B', 'failed')",
        (program_id, program_id),
    )
    conn.commit()

    resp = client.get(f"/api/campaigns/{program_id}/contacts")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = client.get(f"/api/campaigns/{program_id}/contacts", params={"status": "failed"})
    assert [c["phone"] for c in resp.json()] == ["+77012345672"]

    resp = client.get(f"/api/campaigns/{program_id}/contacts", params={"status": "bogus"})
    assert resp.status_code == 400


def test_retry_failed_all_and_selected(client, conn):
    program_id = _create_campaign(client)
    conn.execute(
        "INSERT INTO contacts (program_id, phone, name, status, error_message) "
        "VALUES (?, '+77012345671', 'A', 'failed', 'boom'), "
        "(?, '+77012345672', 'B', 'failed', 'boom'), "
        "(?, '+77012345673', 'C', 'sent', NULL)",
        (program_id, program_id, program_id),
    )
    conn.commit()
    ids = {
        row[1]: row[0]
        for row in conn.execute(
            "SELECT id, phone FROM contacts WHERE program_id = ?", (program_id,)
        ).fetchall()
    }

    # Selected retry only touches failed contacts.
    resp = client.post(
        "/api/contacts/retry", json={"ids": [ids["+77012345671"], ids["+77012345673"]]}
    )
    assert resp.status_code == 200
    assert resp.json()["retried"] == 1
    status = conn.execute(
        "SELECT status FROM contacts WHERE id = ?", (ids["+77012345673"],)
    ).fetchone()[0]
    assert status == "sent"

    # Retry-all resets the remaining failed one.
    resp = client.post(f"/api/campaigns/{program_id}/contacts/retry-failed")
    assert resp.json()["retried"] == 1
    remaining_failed = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE program_id = ? AND status = 'failed'",
        (program_id,),
    ).fetchone()[0]
    assert remaining_failed == 0


def test_delete_selected_skips_sending(client, conn):
    program_id = _create_campaign(client)
    conn.execute(
        "INSERT INTO contacts (program_id, phone, name, status) "
        "VALUES (?, '+77012345671', 'A', 'pending'), (?, '+77012345672', 'B', 'sending')",
        (program_id, program_id),
    )
    conn.commit()
    ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM contacts WHERE program_id = ? ORDER BY id", (program_id,)
        ).fetchall()
    ]

    resp = client.post("/api/contacts/delete", json={"ids": ids})
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert body["skipped_ids"] == [ids[1]]

    remaining = conn.execute(
        "SELECT status FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchall()
    assert remaining == [("sending",)]


def test_needs_review_resolve(client, conn):
    program_id = _create_campaign(client)
    conn.execute(
        "INSERT INTO contacts (program_id, phone, name, status, error_message) "
        "VALUES (?, '+77012345671', 'A', 'needs_review', 'interrupted')",
        (program_id,),
    )
    conn.commit()

    resp = client.post(
        f"/api/campaigns/{program_id}/needs-review/resolve", json={"to": "pending"}
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    row = conn.execute(
        "SELECT status, error_message FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()
    assert row == ("pending", None)

    resp = client.post(
        f"/api/campaigns/{program_id}/needs-review/resolve", json={"to": "deleted"}
    )
    assert resp.status_code == 400


def test_attachments_upload_reject_and_delete(client, conn):
    program_id = _create_campaign(client)
    resp = client.post(
        f"/api/campaigns/{program_id}/attachments",
        files=[
            ("files", ("flyer.png", io.BytesIO(b"png-bytes"), "image/png")),
            ("files", ("notes.txt", io.BytesIO(b"text"), "text/plain")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["saved"]) == 1
    assert body["saved"][0]["file_name"] == "flyer.png"
    assert body["saved"][0]["media_type"] == "image"
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["file_name"] == "notes.txt"

    attachment_id = body["saved"][0]["id"]
    resp = client.delete(f"/api/attachments/{attachment_id}")
    assert resp.status_code == 200
    resp = client.delete(f"/api/attachments/{attachment_id}")
    assert resp.status_code == 404


def test_attachment_file_served_back(client, conn):
    program_id = _create_campaign(client)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"fake-image-payload"
    resp = client.post(
        f"/api/campaigns/{program_id}/attachments",
        files=[("files", ("logo.png", io.BytesIO(png_bytes), "image/png"))],
    )
    attachment_id = resp.json()["saved"][0]["id"]

    resp = client.get(f"/api/attachments/{attachment_id}/file")
    assert resp.status_code == 200
    assert resp.content == png_bytes
    assert resp.headers["content-type"].startswith("image/png")


def test_attachment_file_404s(client, conn):
    assert client.get("/api/attachments/9999/file").status_code == 404

    # Row exists but file is gone from disk -> 404 too.
    program_id = _create_campaign(client)
    resp = client.post(
        f"/api/campaigns/{program_id}/attachments",
        files=[("files", ("gone.png", io.BytesIO(b"bytes"), "image/png"))],
    )
    attachment_id = resp.json()["saved"][0]["id"]
    path = conn.execute(
        "SELECT file_path FROM program_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()[0]
    os.remove(path)
    assert client.get(f"/api/attachments/{attachment_id}/file").status_code == 404


def test_attachment_file_rejects_path_escaping_media_dir(client, conn):
    # A tampered DB row pointing outside the media dir must 404, even if
    # the target file exists on disk.
    program_id = _create_campaign(client)
    with conn:
        cur = conn.execute(
            "INSERT INTO program_attachments (program_id, file_path, file_name, media_type) "
            "VALUES (?, ?, ?, ?)",
            (program_id, "../../etc/hostname", "hostname.png", "image"),
        )
        tampered_id = cur.lastrowid
    assert client.get(f"/api/attachments/{tampered_id}/file").status_code == 404


def test_attachment_upload_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setattr("web.routers.campaigns.MAX_ATTACHMENT_BYTES", 16)
    program_id = _create_campaign(client)
    resp = client.post(
        f"/api/campaigns/{program_id}/attachments",
        files=[
            ("files", ("big.png", io.BytesIO(b"x" * 17), "image/png")),
            ("files", ("small.png", io.BytesIO(b"x" * 16), "image/png")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [item["file_name"] for item in body["saved"]] == ["small.png"]
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["file_name"] == "big.png"
    assert body["skipped"][0]["reason"] == "file too large (max 25 MB)"


def test_preview_with_sample_and_contact_values(client):
    program_id = _create_campaign(client, template="Hi {{name}} from {{city}}")

    resp = client.get(f"/api/campaigns/{program_id}/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Hi Example Name from Example city"
    assert body["missing_fields"] == []
    assert body["using_sample_values"] is True
    assert body["attachments"] == []

    # With a queued contact, its values are used and missing fields reported.
    client.post(
        f"/api/campaigns/{program_id}/contacts",
        json={"phone": "+77012345678", "name": "Aigerim"},
    )
    resp = client.get(f"/api/campaigns/{program_id}/preview")
    body = resp.json()
    assert body["message"] == "Hi Aigerim from {{city}}"
    assert body["missing_fields"] == ["city"]
    assert body["using_sample_values"] is False
    assert body["preview_contact_name"] == "Aigerim"


def test_preview_attachment_caption_notes(client, conn):
    program_id = _create_campaign(client)
    for name in ("first.png", "second.pdf"):
        client.post(
            f"/api/campaigns/{program_id}/attachments",
            files=[("files", (name, io.BytesIO(b"bytes"), "application/octet-stream"))],
        )
    resp = client.get(f"/api/campaigns/{program_id}/preview")
    attachments = resp.json()["attachments"]
    assert all(isinstance(a["id"], int) for a in attachments)
    assert attachments[0]["position"] == 1
    assert "caption" in attachments[0]["caption_note"]
    assert attachments[1]["position"] == 2
    assert "without a caption" in attachments[1]["caption_note"]


PASTE_TEXT = (
    "+77012345678\tAigerim\n"
    "Bekzat, +77012345679\n"
    "just some words\n"
)


def test_paste_preview_counts_and_rows(client):
    program_id = _create_campaign(client)
    resp = client.post(
        f"/api/campaigns/{program_id}/contacts/paste", json={"text": PASTE_TEXT}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid_count"] == 2
    assert body["invalid_count"] == 1
    assert body["valid_preview"][0] == {"phone": "+77012345678", "name": "Aigerim"}
    assert body["valid_preview"][1]["name"] == "Bekzat"
    assert body["invalid_rows"][0]["row_number"] == 3
    assert body["invalid_rows"][0]["line"] == "just some words"
    assert body["invalid_rows"][0]["error"] == "No phone number found in line"


def test_paste_preview_unknown_campaign_404(client):
    resp = client.post("/api/campaigns/999/contacts/paste", json={"text": "x"})
    assert resp.status_code == 404


def test_paste_oversize_413(client):
    program_id = _create_campaign(client)
    big = "x" * (1024 * 1024 + 1)
    resp = client.post(f"/api/campaigns/{program_id}/contacts/paste", json={"text": big})
    assert resp.status_code == 413
    resp = client.post(
        f"/api/campaigns/{program_id}/contacts/paste/commit", json={"text": big}
    )
    assert resp.status_code == 413


def test_paste_commit_inserts_and_reports_duplicates(client):
    program_id = _create_campaign(client)
    resp = client.post(
        f"/api/campaigns/{program_id}/contacts/paste/commit", json={"text": PASTE_TEXT}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inserted"] == 2
    assert body["duplicates"] == []
    assert body["invalid_count"] == 1

    resp = client.post(
        f"/api/campaigns/{program_id}/contacts/paste/commit",
        json={"text": "+7 701 234 5678\tAigerim"},
    )
    body = resp.json()
    assert body["inserted"] == 0
    assert body["duplicates"] == ["+77012345678"]

    contacts = client.get(f"/api/campaigns/{program_id}/contacts").json()
    assert len(contacts) == 2


def test_campaigns_page_renders(client):
    _create_campaign(client)
    resp = client.get("/campaigns")
    assert resp.status_code == 200
    assert "Fall Cohort" in resp.text


def test_root_redirects_to_campaigns(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/campaigns"


# --- Truthful delivery status + reply inbox ---


def _campaign_with_contact(client, conn, name="Delivery", phone="+77010000099"):
    program_id = client.post(
        "/api/campaigns", json={"name": name, "template_text": "Hi {{name}}"}
    ).json()["id"]
    client.post(f"/api/campaigns/{program_id}/contacts", json={"phone": phone, "name": "A"})
    contact_id = conn.execute(
        "SELECT id FROM contacts WHERE program_id = ?", (program_id,)
    ).fetchone()[0]
    return program_id, contact_id


def test_status_returns_delivery_counts(client, conn):
    program_id, contact_id = _campaign_with_contact(client, conn)
    conn.execute(
        "UPDATE contacts SET status='sent', delivery_state='delivered' WHERE id = ?", (contact_id,)
    )
    conn.commit()

    body = client.get(f"/api/campaigns/{program_id}/status").json()

    assert body["delivered_count"] == 1
    assert body["delivery_counts"]["delivered"] == 1
    assert body["rejected_count"] == 0
    assert body["halted"] is False


def test_status_reports_rejections_and_the_halt(client, conn):
    program_id, contact_id = _campaign_with_contact(client, conn, name="Rejected")
    conn.execute(
        "UPDATE contacts SET status='failed', delivery_state='rejected', ack_error='463' "
        "WHERE id = ?",
        (contact_id,),
    )
    conn.execute(
        "UPDATE worker_heartbeat SET halted_at = '2026-07-28T20:00:00Z', "
        "halt_reason = '3 messages in a row were not accepted by WhatsApp.'"
    )
    conn.commit()

    body = client.get(f"/api/campaigns/{program_id}/status").json()

    assert body["rejected_count"] == 1
    assert body["halted"] is True
    assert "not accepted by WhatsApp" in body["halt_reason"]


def test_contacts_endpoint_returns_the_new_delivery_fields(client, conn):
    program_id, contact_id = _campaign_with_contact(client, conn, name="Fields")
    conn.execute(
        "UPDATE contacts SET status='sent', delivery_state='read', wa_message_id='MSG1', "
        "delivered_at='2026-07-28T20:00:00Z', read_at='2026-07-28T20:01:00Z' WHERE id = ?",
        (contact_id,),
    )
    conn.commit()

    contact = client.get(f"/api/campaigns/{program_id}/contacts").json()[0]

    assert contact["delivery_state"] == "read"
    assert contact["wa_message_id"] == "MSG1"
    assert contact["delivered_at"] == "2026-07-28T20:00:00Z"
    assert contact["read_at"] == "2026-07-28T20:01:00Z"
    assert contact["ack_error"] is None


def test_replies_endpoint_returns_bodies_newest_first(client, conn):
    program_id, contact_id = _campaign_with_contact(client, conn, name="Inbox")
    conn.executemany(
        "INSERT INTO replies (contact_id, phone, body, received_at) VALUES (?, ?, ?, ?)",
        [
            (contact_id, "+77010000099", "first", "2026-07-28T10:00:00Z"),
            (contact_id, "+77010000099", "second", "2026-07-28T11:00:00Z"),
        ],
    )
    conn.commit()

    replies = client.get(f"/api/campaigns/{program_id}/replies").json()

    assert [r["body"] for r in replies] == ["second", "first"]
    assert replies[0]["contact_id"] == contact_id
    assert replies[0]["name"] == "A"


def test_replies_endpoint_is_empty_for_a_campaign_without_replies(client, conn):
    program_id, _ = _campaign_with_contact(client, conn, name="Quiet")

    assert client.get(f"/api/campaigns/{program_id}/replies").json() == []


def test_replies_endpoint_404s_for_an_unknown_campaign(client):
    assert client.get("/api/campaigns/9999/replies").status_code == 404


def test_status_reports_the_sending_window_and_daily_cap(client, conn):
    program_id, contact_id = _campaign_with_contact(client, conn, name="Window")
    conn.execute("UPDATE contacts SET status='pending' WHERE id = ?", (contact_id,))
    conn.execute(
        "UPDATE settings SET send_window_start='09:00', send_window_end='09:00', "
        "send_timezone='Asia/Almaty', daily_cap=10 WHERE id = 1"
    )
    conn.commit()

    body = client.get(f"/api/campaigns/{program_id}/status").json()

    # start == end is a full day, so sending is allowed right now.
    assert body["send_window"]["allowed"] is True
    assert body["send_window"]["timezone"] == "Asia/Almaty"
    assert body["daily_cap"] == 10
    assert body["remaining_today"] == 10 - body["sent_today"]


def test_status_suppresses_eta_outside_the_sending_window(client, conn):
    program_id, contact_id = _campaign_with_contact(client, conn, name="Closed")
    conn.execute("UPDATE contacts SET status='pending' WHERE id = ?", (contact_id,))
    # A one-minute window that is almost certainly closed right now.
    conn.execute(
        "UPDATE settings SET send_window_start='03:00', send_window_end='03:01', "
        "send_timezone='UTC' WHERE id = 1"
    )
    conn.commit()

    body = client.get(f"/api/campaigns/{program_id}/status").json()

    if not body["send_window"]["allowed"]:
        assert body["eta_minutes"] is None
        assert "Outside the sending window" in body["send_window"]["reason"]


def test_status_exposes_the_plain_language_rejection_reason(client, conn):
    program_id, contact_id = _campaign_with_contact(client, conn, name="Why")
    conn.execute(
        "UPDATE contacts SET status='failed', delivery_state='rejected', ack_error='463', "
        "error_message='WhatsApp is refusing new conversations from this linked device.' "
        "WHERE id = ?",
        (contact_id,),
    )
    conn.commit()

    body = client.get(f"/api/campaigns/{program_id}/status").json()

    assert body["rejection_reason"] == (
        "WhatsApp is refusing new conversations from this linked device."
    )
    assert "463" not in body["rejection_reason"]
