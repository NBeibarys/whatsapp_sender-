import streamlit as st
from app.db import get_connection

st.title("Settings")

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
