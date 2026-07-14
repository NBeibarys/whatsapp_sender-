import csv
import io
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
from app.db import (
    get_connection,
    create_program,
    add_attachment,
    list_attachments,
    delete_attachment,
    insert_contacts,
    TEST_PROGRAM_NAME,
)
from app.csv_import import parse_contacts_rows

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

# --- Workspace for the selected campaign ---
elif st.session_state.selected_program_id is not None:
    program_id = st.session_state.selected_program_id
    program = conn.execute(
        "SELECT id, name, template_text, paused FROM programs WHERE id = ?", (program_id,)
    ).fetchone()

    if program is None:
        st.session_state.selected_program_id = None
        st.rerun()

    _, name, template_text, paused = program
    st.subheader(name)

    if paused:
        if st.button("▶ Resume campaign"):
            conn.execute("UPDATE programs SET paused = 0 WHERE id = ?", (program_id,))
            conn.commit()
            st.rerun()
    else:
        if st.button("⏸ Pause campaign"):
            conn.execute("UPDATE programs SET paused = 1 WHERE id = ?", (program_id,))
            conn.commit()
            st.rerun()

    st.markdown("**Template**")
    st.caption("Placeholders: {{name}} plus any extra CSV columns for this campaign's contacts.")
    new_template = st.text_area("Message text", value=template_text, key=f"template-{program_id}")
    if st.button("Save template", key=f"save-template-{program_id}"):
        conn.execute(
            "UPDATE programs SET template_text = ? WHERE id = ?", (new_template, program_id)
        )
        conn.commit()
        st.success("Template saved.")
        st.rerun()

    st.markdown("**Attachments**")
    upload_key = f"upload-{program_id}-{st.session_state.get(f'upload-nonce-{program_id}', 0)}"
    uploaded_files = st.file_uploader(
        "Add images or documents to send with every message",
        type=["png", "jpg", "jpeg", "pdf", "doc", "docx"],
        accept_multiple_files=True,
        key=upload_key,
    )
    if uploaded_files and st.button("Save attachments", key=f"save-attachments-{program_id}"):
        for f in uploaded_files:
            add_attachment(conn, program_id, f.name, f.read())
        st.session_state[f"upload-nonce-{program_id}"] = (
            st.session_state.get(f"upload-nonce-{program_id}", 0) + 1
        )
        st.success(f"Added {len(uploaded_files)} attachment(s).")
        st.rerun()

    attachments = list_attachments(conn, program_id)
    for a in attachments:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.write(f"{a['file_name']} ({a['media_type']})")
        with col2:
            if st.button("Remove", key=f"remove-attachment-{a['id']}"):
                delete_attachment(conn, a["id"])
                st.rerun()

    st.markdown("**Add contacts**")
    tab_csv, tab_manual = st.tabs(["Import CSV", "Add one"])

    with tab_csv:
        uploaded_csv = st.file_uploader(
            "Contacts CSV (columns: phone, name, plus any extra fields)",
            type="csv",
            key=f"csv-{program_id}",
        )
        if uploaded_csv is not None:
            text = io.TextIOWrapper(uploaded_csv, encoding="utf-8")
            rows = list(csv.DictReader(text))
            valid, invalid = parse_contacts_rows(rows)

            st.write(f"{len(valid)} valid row(s), {len(invalid)} invalid row(s)")
            if valid:
                st.caption("Preview (first 3): " + str(valid[:3]))
            if invalid:
                st.caption("Rejected: " + str(invalid))

            if valid and st.button("Queue these contacts", key=f"queue-csv-{program_id}"):
                inserted, duplicates = insert_contacts(conn, program_id, valid)
                st.success(f"Queued {inserted} contact(s).")
                if duplicates:
                    st.warning(f"Skipped {len(duplicates)} duplicate(s): {duplicates}")
                st.rerun()

    with tab_manual:
        with st.form(f"manual-contact-{program_id}"):
            manual_phone = st.text_input("Phone (with country code, e.g. +77012345678)")
            manual_name = st.text_input("Name")
            manual_submitted = st.form_submit_button("Add contact")
            if manual_submitted:
                valid, invalid = parse_contacts_rows(
                    [{"phone": manual_phone, "name": manual_name}]
                )
                if invalid:
                    st.error(invalid[0]["error"])
                else:
                    inserted, duplicates = insert_contacts(conn, program_id, valid)
                    if inserted:
                        st.success(f"Added {valid[0]['name']} ({valid[0]['phone']}).")
                    if duplicates:
                        st.warning(f"{duplicates[0]} is already in this campaign.")
