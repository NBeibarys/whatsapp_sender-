def test_settings_defaults_from_schema(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    # Subset: the payload also carries the sending window and cap usage.
    assert resp.json().items() >= {
        "dry_run": True,
        "delay_seconds": 60,
        "jitter_seconds": 15,
        "daily_cap": None,
    }.items()


def test_settings_roundtrip(client):
    payload = {"dry_run": False, "delay_seconds": 45, "jitter_seconds": 10, "daily_cap": 200}
    resp = client.put("/api/settings", json=payload)
    assert resp.status_code == 200
    assert resp.json().items() >= {
        "dry_run": False,
        "delay_seconds": 45,
        "jitter_seconds": 10,
        "daily_cap": 200,
    }.items()

    resp = client.get("/api/settings")
    assert resp.json()["delay_seconds"] == 45


def test_settings_daily_cap_zero_becomes_null(client, conn):
    payload = {"dry_run": True, "delay_seconds": 60, "jitter_seconds": 15, "daily_cap": 0}
    resp = client.put("/api/settings", json=payload)
    assert resp.status_code == 200
    assert resp.json()["daily_cap"] is None

    row = conn.execute("SELECT daily_cap FROM settings WHERE id = 1").fetchone()
    assert row[0] is None


def test_settings_rejects_negative_values(client):
    payload = {"dry_run": True, "delay_seconds": -1, "jitter_seconds": 0, "daily_cap": None}
    resp = client.put("/api/settings", json=payload)
    assert resp.status_code == 422


def test_settings_rejects_values_above_upper_bounds(client):
    over_limit = [
        {"dry_run": True, "delay_seconds": 86401, "jitter_seconds": 0, "daily_cap": None},
        {"dry_run": True, "delay_seconds": 0, "jitter_seconds": 86401, "daily_cap": None},
        {"dry_run": True, "delay_seconds": 0, "jitter_seconds": 0, "daily_cap": 100001},
    ]
    for payload in over_limit:
        resp = client.put("/api/settings", json=payload)
        assert resp.status_code == 422, payload


def test_settings_accepts_exact_upper_bounds(client):
    payload = {
        "dry_run": True,
        "delay_seconds": 86400,
        "jitter_seconds": 86400,
        "daily_cap": 100000,
    }
    resp = client.put("/api/settings", json=payload)
    assert resp.status_code == 200
    assert resp.json()["daily_cap"] == 100000


def test_settings_zero_delay_allowed(client):
    payload = {"dry_run": True, "delay_seconds": 0, "jitter_seconds": 0, "daily_cap": None}
    resp = client.put("/api/settings", json=payload)
    assert resp.status_code == 200
    assert resp.json()["delay_seconds"] == 0


# --- Sending window + daily cap surfacing (Tier 2) ---


def test_settings_default_to_no_window(client):
    body = client.get("/api/settings").json()
    assert body["send_window_start"] is None
    assert body["send_window_end"] is None
    assert body["send_timezone"] == "UTC"
    assert body["send_window"]["allowed"] is True
    assert body["send_window"]["configured"] is False
    assert "Asia/Almaty" in body["timezone_options"]


def test_saving_a_sending_window_round_trips(client):
    saved = client.put(
        "/api/settings",
        json={
            "dry_run": True,
            "delay_seconds": 60,
            "jitter_seconds": 15,
            "daily_cap": 0,
            "send_window_start": "09:00",
            "send_window_end": "21:00",
            "send_timezone": "Asia/Almaty",
        },
    ).json()

    assert saved["send_window_start"] == "09:00"
    assert saved["send_window_end"] == "21:00"
    assert saved["send_timezone"] == "Asia/Almaty"
    assert saved["send_window"]["window"] == "09:00–21:00 Asia/Almaty"
    assert client.get("/api/settings").json()["send_window_start"] == "09:00"


def test_invalid_time_is_rejected(client):
    resp = client.put(
        "/api/settings",
        json={
            "dry_run": True,
            "delay_seconds": 60,
            "jitter_seconds": 15,
            "daily_cap": 0,
            "send_window_start": "9am",
            "send_window_end": "21:00",
        },
    )
    assert resp.status_code == 400
    assert "24-hour" in resp.json()["detail"]


def test_invalid_timezone_is_rejected(client):
    resp = client.put(
        "/api/settings",
        json={
            "dry_run": True,
            "delay_seconds": 60,
            "jitter_seconds": 15,
            "daily_cap": 0,
            "send_window_start": "09:00",
            "send_window_end": "21:00",
            "send_timezone": "Mars/Olympus",
        },
    )
    assert resp.status_code == 400
    assert "not a known time zone" in resp.json()["detail"]


def test_half_configured_window_is_rejected(client):
    resp = client.put(
        "/api/settings",
        json={
            "dry_run": True,
            "delay_seconds": 60,
            "jitter_seconds": 15,
            "daily_cap": 0,
            "send_window_start": "09:00",
            "send_window_end": "",
        },
    )
    assert resp.status_code == 400
    assert "both a start and an end" in resp.json()["detail"]


def test_clearing_the_window_is_allowed(client):
    client.put(
        "/api/settings",
        json={
            "dry_run": True, "delay_seconds": 60, "jitter_seconds": 15, "daily_cap": 0,
            "send_window_start": "09:00", "send_window_end": "21:00",
            "send_timezone": "Asia/Almaty",
        },
    )
    saved = client.put(
        "/api/settings",
        json={
            "dry_run": True, "delay_seconds": 60, "jitter_seconds": 15, "daily_cap": 0,
            "send_window_start": "", "send_window_end": "", "send_timezone": "UTC",
        },
    ).json()

    assert saved["send_window_start"] is None
    assert saved["send_window"]["configured"] is False


def test_daily_cap_effect_is_reported(client, conn):
    program_id = conn.execute(
        "INSERT INTO programs (name, template_text) VALUES ('Cap', 'Hi')"
    ).lastrowid
    conn.execute(
        "INSERT INTO contacts (program_id, phone, name, status, sent_at) "
        "VALUES (?, '+77010000001', 'A', 'sent', datetime('now'))",
        (program_id,),
    )
    conn.commit()

    unlimited = client.get("/api/settings").json()
    assert unlimited["daily_cap"] is None
    assert unlimited["sent_today"] == 1
    assert unlimited["remaining_today"] is None

    capped = client.put(
        "/api/settings",
        json={"dry_run": True, "delay_seconds": 60, "jitter_seconds": 15, "daily_cap": 10},
    ).json()
    assert capped["daily_cap"] == 10
    assert capped["remaining_today"] == 9
