import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection, create_program, insert_contacts
from app.csv_import import parse_contacts_rows

st.title("Settings")

TEST_PROGRAM_NAME = "Test"
TEST_PROGRAM_TEMPLATE = "Hi {{name}}, this is a test message from the Silkroad WhatsApp Sender."

conn = get_connection("data/silkroad.db")

row = conn.execute(
    "SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1"
).fetchone()
delay_seconds, jitter_seconds, daily_cap, dry_run = row

st.caption(
    "Delay, jitter, and daily cap apply on the next polling cycle — no restart needed. "
    "Turning dry run OFF (to start actually sending) requires restarting the worker "
    "process (e.g. `pm2 restart silkroad-whatsapp-worker`) so it can connect to WhatsApp."
)

with st.form("settings_form"):
    new_dry_run = st.checkbox("Dry run (log instead of actually sending)", value=bool(dry_run))
    new_delay = st.number_input("Delay between sends (seconds)", min_value=1, value=delay_seconds, step=1)
    new_jitter = st.number_input("Random jitter added to delay (seconds)", min_value=0, value=jitter_seconds, step=1)
    has_cap = st.checkbox("Set a daily send cap", value=daily_cap is not None)
    new_cap = st.number_input(
        "Daily cap (max sends per day, across all programs)",
        min_value=0,
        value=daily_cap if daily_cap is not None else 0,
        step=1,
        disabled=not has_cap,
    )
    submitted = st.form_submit_button("Save settings")

    if submitted:
        cap_value = new_cap if has_cap else None
        conn.execute(
            "UPDATE settings SET dry_run = ?, delay_seconds = ?, jitter_seconds = ?, daily_cap = ? WHERE id = 1",
            (int(new_dry_run), new_delay, new_jitter, cap_value),
        )
        conn.commit()
        st.success("Settings saved.")
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
            inserted, duplicates = insert_contacts(conn, test_program_id, valid)
            if inserted:
                st.success(
                    f"Queued a test message to {valid[0]['phone']} in the '{TEST_PROGRAM_NAME}' "
                    "program. Check the Status page to watch it get picked up."
                )
            if duplicates:
                st.warning(
                    f"{duplicates[0]} is already queued in the '{TEST_PROGRAM_NAME}' program "
                    "— use the Status page to retry it if needed, or use a different number."
                )
