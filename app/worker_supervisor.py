import os
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
WORKER_PID_PATH = os.path.join(DATA_DIR, "worker.pid")
WORKER_LOG_PATH = os.path.join(DATA_DIR, "worker.log")
WORKER_SCRIPT = os.path.join(PROJECT_ROOT, "worker", "index.js")


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
    os.makedirs(DATA_DIR, exist_ok=True)

    pid = _read_worker_pid()
    if pid and _is_worker_pid(pid):
        return True, f"WhatsApp worker running (PID {pid})."

    pid = _find_existing_worker_pid()
    if pid:
        with open(WORKER_PID_PATH, "w") as f:
            f.write(str(pid))
        return True, f"WhatsApp worker running (PID {pid})."

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
        log.close()
        return False, f"Could not start WhatsApp worker: {exc}"

    with open(WORKER_PID_PATH, "w") as f:
        f.write(str(process.pid))
    return True, f"WhatsApp worker started (PID {process.pid})."
