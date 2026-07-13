import csv
import io
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import get_connection, insert_contacts
from app.csv_import import parse_contacts_rows

st.title("Upload Contacts")

conn = get_connection("data/silkroad.db")
programs = conn.execute("SELECT id, name FROM programs ORDER BY name").fetchall()

if not programs:
    st.warning("Create a program first on the Programs page.")
else:
    program_choice = st.selectbox("Program", programs, format_func=lambda p: p[1])
    uploaded = st.file_uploader("Contacts CSV (columns: phone, name, plus any extra fields)", type="csv")

    if uploaded is not None:
        text = io.TextIOWrapper(uploaded, encoding="utf-8")
        rows = list(csv.DictReader(text))
        valid, invalid = parse_contacts_rows(rows)

        st.write(f"{len(valid)} valid row(s), {len(invalid)} invalid row(s)")

        if valid:
            st.subheader("Preview (first 3)")
            for v in valid[:3]:
                st.write(v)

        if invalid:
            st.subheader("Rejected rows")
            for i in invalid:
                st.write(i["row"], "->", i["error"])

        if valid and st.button("Queue these contacts"):
            inserted, duplicates = insert_contacts(conn, program_choice[0], valid)
            st.success(f"Queued {inserted} contact(s).")
            if duplicates:
                st.warning(
                    f"Skipped {len(duplicates)} duplicate(s) already in this program: {duplicates}"
                )

    st.subheader("Or add one contact manually")
    with st.form("manual_contact"):
        manual_phone = st.text_input("Phone (with country code, e.g. +77012345678)")
        manual_name = st.text_input("Name")
        manual_submitted = st.form_submit_button("Add contact")

        if manual_submitted:
            valid, invalid = parse_contacts_rows([{"phone": manual_phone, "name": manual_name}])
            if invalid:
                st.error(invalid[0]["error"])
            else:
                inserted, duplicates = insert_contacts(conn, program_choice[0], valid)
                if inserted:
                    st.success(f"Added {valid[0]['name']} ({valid[0]['phone']}) to the queue.")
                if duplicates:
                    st.warning(f"{duplicates[0]} is already in this program.")
