# Silkroad WhatsApp Sender

Internal tool for sending templated WhatsApp campaigns (message + optional
attachments) to contact lists imported from CSV, with human-like pacing to
reduce block risk.

## Architecture

- **FastAPI web app** (`web/`) — pages (Jinja2 + vanilla JS, "Green Forge" dark
  UI in `web/static/green-forge.css`) and a JSON API under `/api/...`.
  On startup it auto-launches the worker (skip with `SKIP_AUTO_WORKER=1`).
- **Node worker** (`worker/index.js`) — connects to WhatsApp via Baileys, polls
  the DB for pending contacts, sends messages/attachments, writes heartbeat +
  QR code for pairing. Managed by pm2 (`ecosystem.config.js`).
- **SQLite** (`data/silkroad.db`) — shared state: programs (campaigns),
  contacts, attachments, settings, worker heartbeat.

## Run

```bash
# Python deps (venv) + Node deps
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
npm install

# Web app (also auto-starts the worker)
.venv/bin/uvicorn web.main:app --host 127.0.0.1 --port 8501
```

Port 8501 is the default (kept from the old Streamlit UI so existing bookmarks
work). The app has no authentication and controls a live WhatsApp session, so
keep it bound to `127.0.0.1` — exposing it on the LAN requires a reverse proxy
with authentication in front. To use another port, change `--port`, e.g. honoring `$PORT`:

```bash
.venv/bin/uvicorn web.main:app --host 127.0.0.1 --port "${PORT:-8501}"
```

Under pm2, both processes are defined in `ecosystem.config.js`
(edit the `args` of `silkroad-web` to change the port):

```bash
pm2 start ecosystem.config.js
```

## Test

```bash
.venv/bin/python -m pytest app/tests web/tests -q   # Python (db, csv, phone, API)
npm test                                            # Node worker tests
```

Tests run against temporary databases and never touch `data/silkroad.db`.

## Pacing & settings (Settings page)

- **Dry run** — worker logs what it would send instead of sending. Turn OFF
  for live sending (the UI shows a loud LIVE SENDING badge).
- **Delay / jitter** — each send waits `delay` to `delay + jitter` seconds
  (jitter is a random extra wait that makes pacing look human). Recommended:
  60s delay + 15s jitter to reduce WhatsApp block risk.
- **Daily cap** — max sends per day across all campaigns (0 = no limit).
