"""Tests for the worker spawn lock/backoff logic in app.worker_supervisor."""

import time

import pytest

import app.worker_supervisor as ws


class DeadProcess:
    """Fake Popen result that dies instantly (bad node install etc.)."""

    def __init__(self):
        self.pid = 999999

    def poll(self):
        return 1


class CleanExitProcess:
    """Fake Popen result that exits 0 (worker cleared a dead session)."""

    def __init__(self):
        self.pid = 999998

    def poll(self):
        return 0


@pytest.fixture
def supervisor(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ws, "WORKER_PID_PATH", str(tmp_path / "worker.pid"))
    monkeypatch.setattr(ws, "WORKER_LOG_PATH", str(tmp_path / "worker.log"))
    # No real worker processes must be found or considered alive.
    monkeypatch.setattr(ws, "_find_existing_worker_pid", lambda: None)
    monkeypatch.setattr(ws, "_is_worker_pid", lambda pid: False)
    # Skip the 2s liveness wait inside the spawn path.
    monkeypatch.setattr(ws.time, "sleep", lambda seconds: None)
    # Reset module-level backoff state (monkeypatch restores after the test).
    monkeypatch.setattr(ws, "_last_spawn_attempt", 0.0)
    monkeypatch.setattr(ws, "_last_spawn_error", "")

    popen_calls = []

    def fake_popen(*args, **kwargs):
        popen_calls.append(args)
        return DeadProcess()

    monkeypatch.setattr(ws.subprocess, "Popen", fake_popen)
    return popen_calls


def test_failed_spawn_reports_log_tail(tmp_path, supervisor):
    (tmp_path / "worker.log").write_text("Error: Cannot find module 'baileys'\n")

    ok, message = ws.ensure_worker_running()

    assert ok is False
    assert "exited right after start" in message
    assert "Cannot find module 'baileys'" in message
    assert len(supervisor) == 1


def test_second_call_within_backoff_does_not_spawn_again(tmp_path, supervisor):
    (tmp_path / "worker.log").write_text("Error: node ABI mismatch\n")

    ok_first, _ = ws.ensure_worker_running()
    ok_second, message = ws.ensure_worker_running()

    assert ok_first is False
    assert ok_second is False
    assert len(supervisor) == 1  # no second Popen within the backoff window
    assert "worker failing to start; retrying in" in message
    assert "last error:" in message
    assert "node ABI mismatch" in message  # cached failure includes log tail


def test_spawn_retried_after_backoff_expires(supervisor):
    ws.ensure_worker_running()
    assert len(supervisor) == 1

    # Pretend the failed attempt happened long ago.
    ws._last_spawn_attempt = time.monotonic() - (ws.SPAWN_BACKOFF_SECONDS + 1)
    ws.ensure_worker_running()
    assert len(supervisor) == 2


def test_clean_exit_is_respawned_immediately(supervisor, monkeypatch):
    """exit(0) means 'restart me' (unlinked device / requested disconnect).

    It must not arm the failure backoff, or sending stays halted for 30s+
    even though a fresh QR is one respawn away.
    """
    monkeypatch.setattr(ws.subprocess, "Popen", lambda *a, **k: (
        supervisor.append(a), CleanExitProcess())[1])

    ok_first, message = ws.ensure_worker_running()
    assert ok_first is False
    assert "exited cleanly" in message
    assert ws._last_spawn_error == ""  # backoff not armed

    # Next status poll (no waiting) spawns it again.
    ok_second, _ = ws.ensure_worker_running()
    assert ok_second is False
    assert len(supervisor) == 2


def test_running_worker_fast_path_never_spawns(tmp_path, supervisor, monkeypatch):
    (tmp_path / "worker.pid").write_text("4242")
    monkeypatch.setattr(ws, "_is_worker_pid", lambda pid: pid == 4242)

    ok, message = ws.ensure_worker_running()

    assert ok is True
    assert "PID 4242" in message
    assert len(supervisor) == 0
