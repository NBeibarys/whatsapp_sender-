import base64
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection

st.title("Campaign Status")

conn = get_connection("data/silkroad.db")

heartbeat = conn.execute("SELECT last_seen FROM worker_heartbeat WHERE id = 1").fetchone()
st.caption(f"Worker last seen: {heartbeat[0] if heartbeat and heartbeat[0] else 'never'}")

qr_row = conn.execute("SELECT qr_code FROM worker_heartbeat WHERE id = 1").fetchone()
if qr_row and qr_row[0]:
    st.subheader("Scan this QR code to connect WhatsApp")
    _, b64data = qr_row[0].split(",", 1)
    st.image(base64.b64decode(b64data), width=300)
    st.caption("WhatsApp app -> Settings -> Linked Devices -> Link a Device")
    if st.button("Refresh"):
        st.rerun()

programs = conn.execute("SELECT id, name, paused FROM programs ORDER BY name").fetchall()

for program_id, name, paused in programs:
    st.subheader(f"{name} {'(paused)' if paused else ''}")

    counts = conn.execute(
        "SELECT status, COUNT(*) FROM contacts WHERE program_id = ? GROUP BY status",
        (program_id,),
    ).fetchall()
    counts_dict = dict(counts)
    st.write(counts_dict)

    col1, col2, col3 = st.columns(3)
    with col1:
        if paused:
            if st.button(f"Resume {name}", key=f"resume-{program_id}"):
                conn.execute("UPDATE programs SET paused = 0 WHERE id = ?", (program_id,))
                conn.commit()
                st.rerun()
        else:
            if st.button(f"Pause {name}", key=f"pause-{program_id}"):
                conn.execute("UPDATE programs SET paused = 1 WHERE id = ?", (program_id,))
                conn.commit()
                st.rerun()
    with col2:
        failed_count = counts_dict.get("failed", 0)
        if failed_count and st.button(f"Retry {failed_count} failed", key=f"retry-{program_id}"):
            conn.execute(
                "UPDATE contacts SET status = 'pending', error_message = NULL "
                "WHERE program_id = ? AND status = 'failed'",
                (program_id,),
            )
            conn.commit()
            st.rerun()
    with col3:
        needs_review_count = counts_dict.get("needs_review", 0)
        if needs_review_count:
            st.write(f"{needs_review_count} need review")
            if st.button(f"Mark reviewed as sent", key=f"resolve-sent-{program_id}"):
                conn.execute(
                    "UPDATE contacts SET status = 'sent' WHERE program_id = ? AND status = 'needs_review'",
                    (program_id,),
                )
                conn.commit()
                st.rerun()
            if st.button(f"Mark reviewed as pending", key=f"resolve-pending-{program_id}"):
                conn.execute(
                    "UPDATE contacts SET status = 'pending', error_message = NULL WHERE program_id = ? AND status = 'needs_review'",
                    (program_id,),
                )
                conn.commit()
                st.rerun()
