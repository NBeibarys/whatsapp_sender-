import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection

DB_PATH = os.path.join(_PROJECT_ROOT, "data", "silkroad.db")
DEFAULT_SETTINGS = (60, 0, None, 1)


def save_settings(conn, *, dry_run, delay_seconds, jitter_seconds, daily_cap):
    cap_value = daily_cap if daily_cap and daily_cap > 0 else None
    with conn:
        conn.execute(
            """
            INSERT INTO settings (id, dry_run, delay_seconds, jitter_seconds, daily_cap)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                dry_run = excluded.dry_run,
                delay_seconds = excluded.delay_seconds,
                jitter_seconds = excluded.jitter_seconds,
                daily_cap = excluded.daily_cap
            """,
            (int(dry_run), delay_seconds, jitter_seconds, cap_value),
        )


def get_settings(conn):
    row = conn.execute(
        "SELECT delay_seconds, jitter_seconds, daily_cap, dry_run FROM settings WHERE id = 1"
    ).fetchone()
    if row is not None:
        return row

    delay_seconds, jitter_seconds, daily_cap, dry_run = DEFAULT_SETTINGS
    save_settings(
        conn,
        dry_run=bool(dry_run),
        delay_seconds=delay_seconds,
        jitter_seconds=jitter_seconds,
        daily_cap=daily_cap,
    )
    return DEFAULT_SETTINGS


st.title("Settings")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
conn = get_connection(DB_PATH)

delay_seconds, jitter_seconds, daily_cap, dry_run = get_settings(conn)

new_dry_run = st.checkbox(
    "Dry run (log instead of actually sending)",
    value=bool(dry_run),
    key="settings_dry_run_input",
)
new_delay = st.number_input(
    "Delay between sends (seconds)",
    min_value=1,
    value=delay_seconds,
    step=1,
    key="settings_delay_seconds_input",
)
new_jitter = st.number_input(
    "Random jitter added to delay (seconds)",
    min_value=0,
    value=jitter_seconds,
    step=1,
    key="settings_jitter_seconds_input",
)
new_cap = st.number_input(
    "Daily cap (max sends per day, across all programs — 0 means no limit)",
    min_value=0,
    value=daily_cap if daily_cap is not None else 0,
    step=1,
    key="settings_daily_cap_input",
)

if st.button("Save settings", type="primary"):
    save_settings(
        conn,
        dry_run=new_dry_run,
        delay_seconds=new_delay,
        jitter_seconds=new_jitter,
        daily_cap=new_cap,
    )
    saved_delay, saved_jitter, saved_cap, saved_dry_run = get_settings(conn)
    cap_label = saved_cap if saved_cap is not None else "no limit"
    mode_label = "dry run" if saved_dry_run else "live sending"
    st.success(
        f"Settings saved: delay {saved_delay}s, jitter {saved_jitter}s, "
        f"daily cap {cap_label}, {mode_label}."
    )
