import csv
import io
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
