import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection, create_program, TEST_PROGRAM_NAME

st.title("Campaign")

conn = get_connection("data/silkroad.db")

# --- Overall stats across all real campaigns (excludes the hidden Test program) ---
totals = conn.execute(
    """
    SELECT c.status, COUNT(*) FROM contacts c
    JOIN programs p ON p.id = c.program_id
    WHERE p.name != ?
    GROUP BY c.status
    """,
    (TEST_PROGRAM_NAME,),
).fetchall()
totals_dict = dict(totals)
replied_total = conn.execute(
    """
    SELECT COUNT(*) FROM contacts c
    JOIN programs p ON p.id = c.program_id
    WHERE p.name != ? AND c.replied_at IS NOT NULL
    """,
    (TEST_PROGRAM_NAME,),
).fetchone()[0]
st.caption(
    f"All campaigns — sent: {totals_dict.get('sent', 0)} | "
    f"failed: {totals_dict.get('failed', 0)} | "
    f"pending: {totals_dict.get('pending', 0)} | "
    f"needs_review: {totals_dict.get('needs_review', 0)} | "
    f"replied: {replied_total}"
)

# --- Sidebar: campaign list ---
programs = conn.execute(
    "SELECT id, name, paused FROM programs WHERE name != ? ORDER BY name",
    (TEST_PROGRAM_NAME,),
).fetchall()

if "selected_program_id" not in st.session_state:
    st.session_state.selected_program_id = programs[0][0] if programs else None
if "creating_new_campaign" not in st.session_state:
    st.session_state.creating_new_campaign = not programs

with st.sidebar:
    st.subheader("Campaigns")
    for program_id, name, paused in programs:
        counts = dict(
            conn.execute(
                "SELECT status, COUNT(*) FROM contacts WHERE program_id = ? GROUP BY status",
                (program_id,),
            ).fetchall()
        )
        total = sum(counts.values())
        sent = counts.get("sent", 0)
        label = f"{'⏸ ' if paused else ''}{name} ({sent}/{total})"
        if st.button(label, key=f"select-{program_id}", use_container_width=True):
            st.session_state.selected_program_id = program_id
            st.session_state.creating_new_campaign = False
            st.rerun()

    if st.button("+ New Campaign", use_container_width=True):
        st.session_state.creating_new_campaign = True
        st.rerun()

# --- New campaign form ---
if st.session_state.creating_new_campaign:
    st.subheader("New Campaign")
    with st.form("new_campaign"):
        name = st.text_input("Campaign name")
        template_text = st.text_area(
            "Message template (use {{name}}, {{field}} placeholders)"
        )
        submitted = st.form_submit_button("Create campaign")
        if submitted:
            if not name or not template_text:
                st.error("Name and template are required.")
            elif name == TEST_PROGRAM_NAME:
                st.error(f"'{TEST_PROGRAM_NAME}' is a reserved name — choose another.")
            else:
                program_id = create_program(conn, name, template_text)
                st.session_state.selected_program_id = program_id
                st.session_state.creating_new_campaign = False
                st.success(f"Created campaign '{name}'.")
                st.rerun()
