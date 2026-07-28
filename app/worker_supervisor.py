import os
import subprocess
import threading
import time

from app.config import DATA_DIR, PROJECT_ROOT

WORKER_PID_PATH = os.path.join(DATA_DIR, "worker.pid")
WORKER_LOG_PATH = os.path.join(DATA_DIR, "worker.log")
WORKER_SCRIPT = os.path.join(PROJECT_ROOT, "worker", "index.js")

# Spawn coordination: ensure_worker_running() is called from startup AND every
# status poll — without a lock, overlapping calls could each Popen a worker,
# and the workers would fight over the shared auth/ WhatsApp session.
_spawn_lock = threading.Lock()
# Backoff after a failed spawn so a crash-looping worker isn't respawned on
# every 2s status poll.
SPAWN_BACKOFF_SECONDS = 30
_last_spawn_attempt = 0.0
_last_spawn_error = ""


def _cmdline_for_pid(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _is_worker_pid(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    cmdline = _cmdline_for_pid(pid)
    return "node" in cmdline and "worker/index.js" in cmdline


def _read_worker_pid():
    try:
        return int(open(WORKER_PID_PATH).read().strip())
    except (OSError, ValueError):
        return None


def _find_existing_worker_pid():
    proc_dir = "/proc"
    if not os.path.isdir(proc_dir):
        return None

    for name in os.listdir(proc_dir):
        if not name.isdigit():
            continue
        pid = int(name)
        if _is_worker_pid(pid):
            return pid
    return None


def ensure_worker_running():
    global _last_spawn_attempt, _last_spawn_error

    os.makedirs(DATA_DIR, exist_ok=True)

    # Fast path (lock-free, no sleep): status polls while the worker is
    # healthy must not serialize or block.
    pid = _read_worker_pid()
    if pid and _is_worker_pid(pid):
        return True, f"WhatsApp worker running (PID {pid})."

    with _spawn_lock:
        # Re-check under the lock — another caller may have spawned it while
        # we were waiting.
        pid = _read_worker_pid()
        if pid and _is_worker_pid(pid):
            return True, f"WhatsApp worker running (PID {pid})."

        pid = _find_existing_worker_pid()
        if pid:
            with open(WORKER_PID_PATH, "w") as f:
                f.write(str(pid))
            return True, f"WhatsApp worker running (PID {pid})."

        # Backoff: if a spawn attempt failed recently, don't retry yet.
        elapsed = time.monotonic() - _last_spawn_attempt
        if _last_spawn_error and elapsed < SPAWN_BACKOFF_SECONDS:
            retry_in = max(1, int(SPAWN_BACKOFF_SECONDS - elapsed))
            return False, (
                f"worker failing to start; retrying in {retry_in}s; "
                f"last error: {_last_spawn_error}"
            )

        _last_spawn_attempt = time.monotonic()
        log = open(WORKER_LOG_PATH, "a", buffering=1)
        try:
            process = subprocess.Popen(
                ["node", WORKER_SCRIPT],
                cwd=PROJECT_ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            _last_spawn_error = f"Could not start WhatsApp worker: {exc}"
            return False, _last_spawn_error
        finally:
            # The child inherited the fd; the parent's copy must not leak.
            log.close()

        with open(WORKER_PID_PATH, "w") as f:
            f.write(str(process.pid))

        # Give the worker a moment to boot, then confirm it is actually alive
        # (a bad node install/ABI mismatch makes it die instantly).
        time.sleep(2)
        returncode = process.poll()
        if returncode == 0:
            # A clean exit is a restart request, not a failure: the worker exits
            # 0 after clearing a dead WhatsApp session (device unlinked) or after
            # an app-requested disconnect. Must NOT arm the failure backoff —
            # the next status poll has to respawn it immediately so a fresh QR
            # appears instead of sending staying halted for 30s+.
            _last_spawn_error = ""
            return False, (
                "WhatsApp worker exited cleanly (session cleared); "
                "restarting it on the next check."
            )
        if returncode is not None or not _is_worker_pid(process.pid):
            _last_spawn_error = (
                f"WhatsApp worker exited right after start (PID {process.pid}). "
                f"Last log lines:\n{_tail_worker_log()}"
            )
            return False, _last_spawn_error

        _last_spawn_error = ""
        return True, f"WhatsApp worker started (PID {process.pid})."


def _tail_worker_log(lines: int = 5) -> str:
    try:
        with open(WORKER_LOG_PATH, "r", errors="ignore") as f:
            return "".join(f.readlines()[-lines:]).strip() or "(worker log is empty)"
    except OSError:
        return "(worker log unavailable)"
