from datetime import datetime, timedelta, timezone


def test_connection_status_no_heartbeat(client):
    resp = client.get("/api/connection/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["worker_alive"] is False
    assert body["age_seconds"] is None
    assert body["connected"] is False
    assert body["qr_data_url"] is None
    assert body["disconnect_requested"] is False
    # SKIP_AUTO_WORKER=1 in tests: supervisor not invoked.
    assert body["worker_message"] is None


def test_connection_status_fresh_heartbeat(client, conn):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE worker_heartbeat SET last_seen = ?, connected = 1 WHERE id = 1", (now,)
    )
    conn.commit()

    body = client.get("/api/connection/status").json()
    assert body["worker_alive"] is True
    assert body["connected"] is True
    assert body["age_seconds"] < 120


def test_connection_status_stale_heartbeat(client, conn):
    stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    conn.execute("UPDATE worker_heartbeat SET last_seen = ? WHERE id = 1", (stale,))
    conn.commit()

    body = client.get("/api/connection/status").json()
    assert body["worker_alive"] is False
    assert body["age_seconds"] > 120


def test_connection_status_clears_qr_when_connected(client, conn):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE worker_heartbeat SET last_seen = ?, connected = 1, "
        "qr_code = 'data:image/png;base64,AAAA' WHERE id = 1",
        (now,),
    )
    conn.commit()

    body = client.get("/api/connection/status").json()
    assert body["qr_data_url"] is None
    row = conn.execute("SELECT qr_code FROM worker_heartbeat WHERE id = 1").fetchone()
    assert row[0] is None


def test_connection_status_returns_qr_when_not_connected(client, conn):
    conn.execute(
        "UPDATE worker_heartbeat SET qr_code = 'data:image/png;base64,AAAA' WHERE id = 1"
    )
    conn.commit()

    body = client.get("/api/connection/status").json()
    assert body["qr_data_url"] == "data:image/png;base64,AAAA"


def test_disconnect_sets_flags(client, conn):
    conn.execute(
        "UPDATE worker_heartbeat SET connected = 1, qr_code = 'data:image/png;base64,AAAA' "
        "WHERE id = 1"
    )
    conn.commit()

    resp = client.post("/api/connection/disconnect")
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT disconnect_requested, connected, qr_code FROM worker_heartbeat WHERE id = 1"
    ).fetchone()
    assert row == (1, 0, None)


def test_test_message_queues_via_test_program(client, conn):
    resp = client.post(
        "/api/connection/test-message", json={"phone": "+77012345678", "name": "Aliya"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "queued": True,
        "duplicate": False,
        "phone": "+77012345678",
        "program": "Test",
    }

    row = conn.execute(
        "SELECT p.name, p.paused, c.phone, c.status FROM contacts c "
        "JOIN programs p ON p.id = c.program_id"
    ).fetchone()
    assert row == ("Test", 0, "+77012345678", "pending")

    # Same phone again: duplicate, not queued twice.
    resp = client.post(
        "/api/connection/test-message", json={"phone": "+77012345678", "name": "Aliya"}
    )
    body = resp.json()
    assert body["queued"] is False
    assert body["duplicate"] is True


def test_test_message_invalid_phone(client):
    resp = client.post("/api/connection/test-message", json={"phone": "abc", "name": "X"})
    assert resp.status_code == 400


def test_test_message_unpauses_existing_test_program(client, conn):
    conn.execute(
        "INSERT INTO programs (name, template_text, paused) VALUES ('Test', 'Hi {{name}}', 1)"
    )
    conn.commit()

    client.post("/api/connection/test-message", json={"phone": "+77012345678", "name": "X"})
    row = conn.execute("SELECT paused FROM programs WHERE name = 'Test'").fetchone()
    assert row[0] == 0


def test_connection_page_renders(client):
    resp = client.get("/connection")
    assert resp.status_code == 200


def test_connection_status_reports_the_halt(client, conn):
    conn.execute(
        "UPDATE worker_heartbeat SET halted_at = '2026-07-28T20:00:00Z', "
        "halt_reason = 'WhatsApp is refusing new conversations from this linked device.'"
    )
    conn.commit()

    body = client.get("/api/connection/status").json()

    assert body["halted"] is True
    assert body["halted_at"] == "2026-07-28T20:00:00Z"
    assert "refusing new conversations" in body["halt_reason"]


def test_resume_sending_clears_the_halt_but_leaves_programs_paused(client, conn):
    conn.execute("INSERT INTO programs (name, template_text, paused) VALUES ('P', 'Hi', 1)")
    conn.execute(
        "UPDATE worker_heartbeat SET halted_at = '2026-07-28T20:00:00Z', halt_reason = 'nope'"
    )
    conn.commit()

    body = client.post("/api/sending/resume").json()

    assert body["halted"] is False
    assert body["programs_still_paused"] is True
    status = client.get("/api/connection/status").json()
    assert status["halted"] is False
    assert status["halt_reason"] is None
    # The worker paused these for a reason — resuming must not unpause them.
    assert conn.execute("SELECT paused FROM programs WHERE name = 'P'").fetchone()[0] == 1


def test_connection_status_is_not_halted_by_default(client):
    body = client.get("/api/connection/status").json()
    assert body["halted"] is False
    assert body["halt_reason"] is None
