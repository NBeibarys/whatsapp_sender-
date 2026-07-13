import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection, create_program

st.title("Programs")

conn = get_connection("data/silkroad.db")

with st.form("new_program"):
    name = st.text_input("Program name")
    template_text = st.text_area("Message template (use {{name}}, {{field}} placeholders)")
    submitted = st.form_submit_button("Create program")
    if submitted:
        if not name or not template_text:
            st.error("Name and template are required.")
        else:
            program_id = create_program(conn, name, template_text)
            st.success(f"Created program '{name}' (id {program_id})")

st.subheader("Existing programs")
rows = conn.execute(
    "SELECT id, name, template_text, paused FROM programs ORDER BY created_at DESC"
).fetchall()
for row in rows:
    st.write(f"**{row[1]}** (id {row[0]}) — {'paused' if row[3] else 'active'}")
    st.code(row[2])
