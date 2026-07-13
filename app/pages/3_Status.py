import base64
import os
import sys
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection

STALE_AFTER_SECONDS = 120

st.title("Campaign Status")

conn = get_connection("data/silkroad.db")

heartbeat = conn.execute("SELECT last_seen FROM worker_heartbeat WHERE id = 1").fetchone()
last_seen = heartbeat[0] if heartbeat else None

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

    if age_seconds is None:
        st.caption(f"Worker last seen: {last_seen}")
    elif age_seconds > STALE_AFTER_SECONDS:
        st.error(
            f"Worker may not be running — last seen {int(age_seconds)}s ago. "
            "Start it with `pm2 restart silkroad-whatsapp-worker` or `node worker/index.js`."
        )
    else:
        st.success(f"Worker running — last seen {int(age_seconds)}s ago.")

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
    st.caption(
        " | ".join(f"{status}: {count}" for status, count in counts_dict.items())
        or "No contacts yet"
    )

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

    all_contacts = conn.execute(
        "SELECT phone, name, status, sent_at, error_message FROM contacts "
        "WHERE program_id = ? ORDER BY status, id",
        (program_id,),
    ).fetchall()

    if not all_contacts:
        st.info("No contacts yet — add some on the Upload Contacts page.")
    else:
        status_filter = st.multiselect(
            "Filter by status",
            options=["pending", "sending", "sent", "failed", "needs_review"],
            default=[],
            key=f"status-filter-{program_id}",
        )
        rows_to_show = [
            {
                "phone": c[0],
                "name": c[1],
                "status": c[2],
                "sent_at": c[3],
                "error_message": c[4],
            }
            for c in all_contacts
            if not status_filter or c[2] in status_filter
        ]
        st.dataframe(rows_to_show, use_container_width=True, hide_index=True)
