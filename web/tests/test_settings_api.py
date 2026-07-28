def test_settings_defaults_from_schema(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json() == {
        "dry_run": True,
        "delay_seconds": 60,
        "jitter_seconds": 15,
        "daily_cap": None,
    }


def test_settings_roundtrip(client):
    payload = {"dry_run": False, "delay_seconds": 45, "jitter_seconds": 10, "daily_cap": 200}
    resp = client.put("/api/settings", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {
        "dry_run": False,
        "delay_seconds": 45,
        "jitter_seconds": 10,
        "daily_cap": 200,
    }

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
