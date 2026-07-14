import base64
import os
import sys
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection, create_program, insert_contacts, TEST_PROGRAM_NAME
from app.csv_import import parse_contacts_rows

STALE_AFTER_SECONDS = 120
TEST_PROGRAM_TEMPLATE = "Hi {{name}}, this is a test message from the Silkroad WhatsApp Sender."

st.title("Connection")

conn = get_connection("data/silkroad.db")

heartbeat = conn.execute(
    "SELECT last_seen, qr_code, connected FROM worker_heartbeat WHERE id = 1"
).fetchone()
last_seen, qr_code, connected = heartbeat if heartbeat else (None, None, 0)

worker_alive = False

if not last_seen:
    st.error(
        "Worker has never checked in. Start it with `pm2 restart silkroad-whatsapp-worker` "
        "or `node worker/index.js`."
    )
else:
    try:
        last_seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - last_seen_dt).total_seconds()
    except ValueError:
        age_seconds = None

    worker_alive = age_seconds is not None and age_seconds <= STALE_AFTER_SECONDS

    if age_seconds is None:
        st.caption(f"Worker last seen: {last_seen}")
    elif not worker_alive:
        st.error(
            f"Worker may not be running — last seen {int(age_seconds)}s ago. "
            "Start it with `pm2 restart silkroad-whatsapp-worker` or `node worker/index.js`."
        )
    elif connected:
        st.success(f"WhatsApp connected — worker last seen {int(age_seconds)}s ago.")
    else:
        st.warning(
            f"WhatsApp not connected — worker last seen {int(age_seconds)}s ago. "
            "Scan the QR code below to link a number."
        )

if worker_alive and connected:
    if st.button("Disconnect WhatsApp"):
        conn.execute("UPDATE worker_heartbeat SET disconnect_requested = 1 WHERE id = 1")
        conn.commit()
        st.info(
            "Disconnect requested — the worker will log out and show a new QR code "
            "in a few seconds."
        )
        st.rerun()

if qr_code:
    st.subheader("Scan this QR code to connect WhatsApp")
    _, b64data = qr_code.split(",", 1)
    st.image(base64.b64decode(b64data), width=300)
    st.caption("WhatsApp app -> Settings -> Linked Devices -> Link a Device")
    if st.button("Refresh"):
        st.rerun()

st.divider()
st.subheader("Send a test message")
st.caption(
    "Quickly queue a one-off message to verify the pipeline end-to-end, without "
    "hand-writing a script. This reuses (or creates) a dedicated 'Test' program with a "
    "simple default template, and queues one contact as pending — the worker picks it "
    "up on its next poll, same as any other contact."
)

with st.form("test_message_form"):
    test_phone = st.text_input("Phone (with country code, e.g. +77012345678)")
    test_name = st.text_input("Name", value="there")
    test_submitted = st.form_submit_button("Queue test message")

    if test_submitted:
        valid, invalid = parse_contacts_rows([{"phone": test_phone, "name": test_name or "there"}])
        if invalid:
            st.error(invalid[0]["error"])
        else:
            existing = conn.execute(
                "SELECT id FROM programs WHERE name = ?", (TEST_PROGRAM_NAME,)
            ).fetchone()
            test_program_id = existing[0] if existing else create_program(
                conn, TEST_PROGRAM_NAME, TEST_PROGRAM_TEMPLATE
            )
            conn.execute("UPDATE programs SET paused = 0 WHERE id = ?", (test_program_id,))
            conn.commit()
            inserted, duplicates = insert_contacts(conn, test_program_id, valid)
            if inserted:
                st.success(
                    f"Queued a test message to {valid[0]['phone']} in the '{TEST_PROGRAM_NAME}' "
                    "program. Check pm2 logs or the DB to watch it get picked up."
                )
            if duplicates:
                st.warning(
                    f"{duplicates[0]} is already queued in the '{TEST_PROGRAM_NAME}' program "
                    "— use a different number, or wait for it to be sent."
                )
