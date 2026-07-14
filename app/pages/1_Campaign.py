import csv
import hashlib
import html
import io
import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import streamlit as st
from app.db import (
    get_connection,
    create_program,
    add_attachment,
    list_attachments,
    delete_attachment,
    insert_contacts,
    delete_contact_from_table,
    TEST_PROGRAM_NAME,
)
from app.csv_import import parse_contacts_rows, map_contact_rows

# pandas 3.x defaults string columns to an Arrow-backed dtype; on this system that
# path segfaults inside pyarrow when st.data_editor builds the contact table
# (verified via PYTHONFAULTHANDLER trace). Force the legacy object dtype instead.
pd.set_option("future.infer_string", False)

_TEMPLATE_FIELD_PATTERN = re.compile(r"\{\{(\w+)\}\}")
_ALLOWED_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".doc", ".docx"}


def _file_extension(file_name: str) -> str:
    return os.path.splitext(file_name or "")[1].lower()


def _is_csv_upload(file_name: str) -> bool:
    return _file_extension(file_name) == ".csv"


def _is_allowed_attachment(file_name: str) -> bool:
    return _file_extension(file_name) in _ALLOWED_ATTACHMENT_EXTENSIONS


def _normalized_column_name(column_name: str) -> str:
    return "".join(ch.lower() for ch in column_name if ch.isalnum())


def _guess_column(columns: list[str], candidates: list[str]) -> str | None:
    normalized_candidates = {_normalized_column_name(candidate) for candidate in candidates}
    for column in columns:
        if _normalized_column_name(column) in normalized_candidates:
            return column
    for column in columns:
        normalized = _normalized_column_name(column)
        if any(candidate in normalized for candidate in normalized_candidates):
            return column
    return None


def _column_index(options: list[str], value: str | None) -> int:
    if value in options:
        return options.index(value)
    return 0


def _flatten_valid_contact(contact: dict) -> dict:
    return {
        "phone": contact["phone"],
        "name": contact["name"],
        **contact.get("extra_fields", {}),
    }


def _template_text_height(text: str) -> int:
    visual_lines = 0
    for line in (text or "").splitlines() or [""]:
        visual_lines += max(1, (len(line) // 88) + 1)
    return max(180, visual_lines * 24 + 72)


def _placeholder_fields(template_text: str) -> list[str]:
    fields = []
    for match in _TEMPLATE_FIELD_PATTERN.finditer(template_text or ""):
        field = match.group(1)
        if field not in fields:
            fields.append(field)
    return fields


def _render_template_preview(template_text: str, fields: dict) -> tuple[str, list[str]]:
    missing = []

    def replace_field(match):
        field = match.group(1)
        if field not in fields:
            if field not in missing:
                missing.append(field)
            return match.group(0)
        return str(fields[field])

    return _TEMPLATE_FIELD_PATTERN.sub(replace_field, template_text or ""), missing


def _contact_preview_fields(contact_row) -> dict:
    if contact_row is None:
        return {"name": "Example Name"}

    _, name, extra_fields_json = contact_row
    try:
        extra_fields = json.loads(extra_fields_json or "{}")
    except json.JSONDecodeError:
        extra_fields = {}
    if not isinstance(extra_fields, dict):
        extra_fields = {}

    return {"name": name, **extra_fields}


def _sample_preview_fields(template_text: str) -> dict:
    fields = {}
    for field in _placeholder_fields(template_text):
        fields[field] = "Example Name" if field == "name" else f"Example {field}"
    return fields or {"name": "Example Name"}


def _attachment_context_lines(attachments: list[dict]) -> list[str]:
    if not attachments:
        return ["Text-only WhatsApp message. No attachments will be sent."]

    lines = [
        f"1. {attachments[0]['media_type'].title()}: {attachments[0]['file_name']} "
        "(message text is sent as this attachment's caption)"
    ]
    for index, attachment in enumerate(attachments[1:], start=2):
        lines.append(
            f"{index}. {attachment['media_type'].title()}: {attachment['file_name']} "
            "(sent immediately after, without a caption)"
        )
    return lines


def _show_message_preview(message_text: str, attachments: list[dict]) -> None:
    attachment_context = "\n".join(_attachment_context_lines(attachments))
    st.markdown(
        f"""
        <div class="campaign-message-preview">
            <div class="campaign-message-preview__label">Outgoing message</div>
            <pre>{html.escape(message_text)}</pre>
            <div class="campaign-message-preview__label">Attachment context</div>
            <pre>{html.escape(attachment_context)}</pre>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.markdown(
    """
    <style>
    div[data-testid="stTextArea"] textarea {
        font-size: 1rem !important;
        line-height: 1.5 !important;
    }

    div[data-testid="stFileUploaderDropzone"] {
        min-height: 8rem;
        border: 2px dashed rgba(37, 99, 235, 0.42);
        border-radius: 8px;
        background: rgba(37, 99, 235, 0.035);
    }

    div[data-testid="stFileUploaderDropzone"]:hover {
        border-color: rgba(37, 99, 235, 0.72);
        background: rgba(37, 99, 235, 0.07);
    }

    .campaign-message-preview {
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-radius: 8px;
        padding: 0.875rem 1rem;
        margin: 0.5rem 0 1rem;
        background: rgba(250, 250, 250, 0.85);
    }

    .campaign-message-preview__label {
        color: rgba(49, 51, 63, 0.68);
        font-size: 0.85rem;
        font-weight: 600;
        margin: 0.25rem 0 0.35rem;
    }

    .campaign-message-preview pre {
        color: rgb(49, 51, 63);
        font-family: inherit;
        font-size: 1rem;
        line-height: 1.5;
        margin: 0 0 0.75rem;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
    }

    .campaign-message-preview pre:last-child {
        margin-bottom: 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Campaign")

conn = get_connection("data/silkroad.db")



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
            "Message template (use {{name}}, {{field}} placeholders)",
            height=_template_text_height(""),
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
        if st.button("▶ Resume campaign", key=f"resume-{program_id}"):
            conn.execute("UPDATE programs SET paused = 0 WHERE id = ?", (program_id,))
            conn.commit()
            st.rerun()
    else:
        if st.button("⏸ Pause campaign", key=f"pause-{program_id}"):
            conn.execute("UPDATE programs SET paused = 1 WHERE id = ?", (program_id,))
            conn.commit()
            st.rerun()

    st.markdown("**Template**")
    st.caption("Placeholders: {{name}} plus any extra CSV columns for this campaign's contacts.")
    template_nonce = st.session_state.get(f"template-nonce-{program_id}", 0)
    template_key = f"template-{program_id}-{template_nonce}"
    current_template_text = st.session_state.get(template_key, template_text)
    new_template = st.text_area(
        "Message text",
        value=template_text,
        key=template_key,
        height=_template_text_height(current_template_text),
    )
    if st.button("Save template", key=f"save-template-{program_id}"):
        conn.execute(
            "UPDATE programs SET template_text = ? WHERE id = ?", (new_template, program_id)
        )
        conn.commit()
        st.session_state[f"template-nonce-{program_id}"] = template_nonce + 1
        st.success("Template saved.")
        st.rerun()

    st.markdown("**Attachments**")
    upload_key = f"upload-{program_id}-{st.session_state.get(f'upload-nonce-{program_id}', 0)}"
    uploaded_files = st.file_uploader(
        "Add images or documents to send with every message",
        accept_multiple_files=True,
        key=upload_key,
    )
    valid_attachment_files = []
    skipped_attachment_files = []
    for f in uploaded_files or []:
        if _is_allowed_attachment(f.name):
            valid_attachment_files.append(f)
        else:
            skipped_attachment_files.append(f.name)
    if skipped_attachment_files:
        st.warning(
            "Skipped unsupported attachment file(s): "
            + ", ".join(skipped_attachment_files)
            + ". Use the contacts CSV importer below for .csv files."
        )
    if valid_attachment_files and st.button("Save attachments", key=f"save-attachments-{program_id}"):
        for f in valid_attachment_files:
            add_attachment(conn, program_id, f.name, f.read())
        st.session_state[f"upload-nonce-{program_id}"] = (
            st.session_state.get(f"upload-nonce-{program_id}", 0) + 1
        )
        st.success(f"Added {len(valid_attachment_files)} attachment(s).")
        st.rerun()

    attachments = list_attachments(conn, program_id)
    if not attachments:
        st.caption("No attachments yet.")
    for a in attachments:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.write(f"{a['file_name']} ({a['media_type']})")
        with col2:
            if st.button("Remove", key=f"remove-attachment-{a['id']}"):
                delete_attachment(conn, a["id"])
                st.rerun()

    st.markdown("**Preview**")
    preview_contact = conn.execute(
        "SELECT id, name, extra_fields FROM contacts WHERE program_id = ? ORDER BY id LIMIT 1",
        (program_id,),
    ).fetchone()
    if preview_contact is None:
        preview_fields = _sample_preview_fields(new_template)
        st.caption("Using sample values because no contacts are queued yet.")
    else:
        preview_fields = _contact_preview_fields(preview_contact)
        st.caption(f"Using the first queued contact: {preview_contact[1]}.")

    preview_message, missing_fields = _render_template_preview(new_template, preview_fields)
    if missing_fields:
        st.warning(
            "Missing value(s) for this preview and for the actual send: "
            + ", ".join(f"{{{{{field}}}}}" for field in missing_fields)
        )
    _show_message_preview(preview_message, attachments)


    st.markdown("**Add contacts**")
    tab_csv, tab_manual = st.tabs(["Import CSV", "Add one"])

    with tab_csv:
        csv_nonce = st.session_state.get(f"csv-nonce-{program_id}", 0)
        uploaded_csv = st.file_uploader(
            "Drop contacts CSV here or click to browse",
            key=f"csv-{program_id}-{csv_nonce}",
        )
        if uploaded_csv is not None:
            if not _is_csv_upload(uploaded_csv.name):
                st.error("Please upload a .csv file for contacts.")
                rows = []
            else:
                csv_text = io.TextIOWrapper(uploaded_csv, encoding="utf-8-sig")
                rows = list(csv.DictReader(csv_text))

            if rows:
                columns = list(rows[0].keys())
                skip_option = "-- skip --"
                optional_columns = [skip_option, *columns]
                guessed_phone = _guess_column(columns, ["phone", "phone number", "mobile", "whatsapp"])
                guessed_name = _guess_column(
                    columns, ["name", "full name", "recipient", "startup name", "company"]
                )
                guessed_startup = _guess_column(columns, ["startup name", "startup", "company", "project"])
                guessed_email = _guess_column(columns, ["email", "e-mail", "mail"])

                st.markdown("**Map columns**")
                map_col1, map_col2, map_col3, map_col4 = st.columns(4)
                with map_col1:
                    phone_column = st.selectbox(
                        "Phone",
                        columns,
                        index=_column_index(columns, guessed_phone),
                        key=f"csv-phone-column-{program_id}-{csv_nonce}",
                    )
                with map_col2:
                    name_column = st.selectbox(
                        "Name used in message",
                        columns,
                        index=_column_index(columns, guessed_name),
                        key=f"csv-name-column-{program_id}-{csv_nonce}",
                    )
                with map_col3:
                    startup_name_column = st.selectbox(
                        "Startup name",
                        optional_columns,
                        index=_column_index(optional_columns, guessed_startup),
                        key=f"csv-startup-column-{program_id}-{csv_nonce}",
                    )
                with map_col4:
                    email_column = st.selectbox(
                        "Email",
                        optional_columns,
                        index=_column_index(optional_columns, guessed_email),
                        key=f"csv-email-column-{program_id}-{csv_nonce}",
                    )

                mapped_columns = {phone_column, name_column}
                if startup_name_column != skip_option:
                    mapped_columns.add(startup_name_column)
                if email_column != skip_option:
                    mapped_columns.add(email_column)
                extra_column_options = [column for column in columns if column not in mapped_columns]
                extra_columns = st.multiselect(
                    "Other columns to keep for placeholders",
                    extra_column_options,
                    default=extra_column_options,
                    key=f"csv-extra-columns-{program_id}-{csv_nonce}",
                )

                valid, invalid = map_contact_rows(
                    rows,
                    phone_column=phone_column,
                    name_column=name_column,
                    startup_name_column=(
                        startup_name_column if startup_name_column != skip_option else None
                    ),
                    email_column=email_column if email_column != skip_option else None,
                    extra_columns=extra_columns,
                )

                st.write(f"{len(valid)} valid row(s), {len(invalid)} invalid row(s)")
                if valid:
                    st.caption("Preview")
                    st.dataframe(
                        pd.DataFrame([_flatten_valid_contact(contact) for contact in valid[:10]]),
                        hide_index=True,
                        use_container_width=True,
                    )
                if invalid:
                    st.caption("Rejected")
                    rejected_rows = []
                    for item in invalid:
                        row = item["row"]
                        rejected_rows.append(
                            {
                                "row": item.get("row_number"),
                                "error": item["error"],
                                "phone": row.get(phone_column, ""),
                                "name": row.get(name_column, ""),
                                "startup_name": (
                                    row.get(startup_name_column, "")
                                    if startup_name_column != skip_option
                                    else ""
                                ),
                                "email": row.get(email_column, "")
                                if email_column != skip_option
                                else "",
                            }
                        )
                    st.dataframe(
                        pd.DataFrame(rejected_rows),
                        hide_index=True,
                        use_container_width=True,
                    )

                if valid and st.button("Queue these contacts", key=f"queue-csv-{program_id}"):
                    inserted, duplicates = insert_contacts(conn, program_id, valid)
                    st.session_state[f"csv-nonce-{program_id}"] = csv_nonce + 1
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

    @st.fragment(run_every="2s")
    def render_status_and_contacts() -> None:
        status_conn = get_connection("data/silkroad.db")
        try:
            st.markdown("**Status**")
            program_state = status_conn.execute(
                "SELECT paused FROM programs WHERE id = ?", (program_id,)
            ).fetchone()
            current_paused = bool(program_state[0]) if program_state else paused
            counts = dict(
                status_conn.execute(
                    "SELECT status, COUNT(*) FROM contacts WHERE program_id = ? GROUP BY status",
                    (program_id,),
                ).fetchall()
            )
            replied_count = status_conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE program_id = ? AND replied_at IS NOT NULL",
                (program_id,),
            ).fetchone()[0]
            delay_seconds = status_conn.execute(
                "SELECT delay_seconds FROM settings WHERE id = 1"
            ).fetchone()[0]
            pending_count = counts.get("pending", 0)
            sent_count = counts.get("sent", 0)
            failed_count = counts.get("failed", 0)
            needs_review_count = counts.get("needs_review", 0)
            eta_minutes = round(pending_count * delay_seconds / 60, 1)
            show_eta = pending_count and not current_paused
            eta_text = (
                f"~{eta_minutes} min remaining"
                if show_eta
                else ("Paused" if pending_count and current_paused else "No active queue")
            )
            failed_note = "Needs retry" if failed_count else "No failures"
            needs_review_tile = (
                f"""
                <div class="campaign-status-card campaign-status-card--review">
                    <div class="campaign-status-label">Needs review</div>
                    <div class="campaign-status-value">{needs_review_count}</div>
                    <div class="campaign-status-note">Manual decision</div>
                </div>
                """
                if needs_review_count
                else ""
            )
            st.markdown(
                f"""
                <style>
                .campaign-status-panel {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                    gap: 0.75rem;
                    margin: 0.35rem 0 1rem;
                }}
                .campaign-status-card {{
                    border: 1px solid rgba(49, 51, 63, 0.18);
                    border-radius: 8px;
                    padding: 1rem 1.1rem;
                    background: #ffffff;
                    box-shadow: 0 1px 2px rgba(49, 51, 63, 0.06);
                }}
                .campaign-status-card--failed {{
                    border-color: rgba(190, 18, 60, 0.42);
                    background: #fff5f6;
                }}
                .campaign-status-card--review {{
                    border-color: rgba(126, 34, 206, 0.32);
                    background: #fbf7ff;
                }}
                .campaign-status-label {{
                    color: rgba(49, 51, 63, 0.72);
                    font-size: 0.9rem;
                    font-weight: 650;
                    line-height: 1.2;
                }}
                .campaign-status-value {{
                    color: #262730;
                    font-size: 2.35rem;
                    font-weight: 750;
                    line-height: 1.05;
                    margin-top: 0.35rem;
                }}
                .campaign-status-card--failed .campaign-status-value {{ color: #be123c; }}
                .campaign-status-note {{
                    color: rgba(49, 51, 63, 0.62);
                    font-size: 0.82rem;
                    line-height: 1.3;
                    margin-top: 0.45rem;
                }}
                </style>
                <div class="campaign-status-panel">
                    <div class="campaign-status-card">
                        <div class="campaign-status-label">Pending</div>
                        <div class="campaign-status-value">{pending_count}</div>
                        <div class="campaign-status-note">{eta_text}</div>
                    </div>
                    <div class="campaign-status-card">
                        <div class="campaign-status-label">Sent</div>
                        <div class="campaign-status-value">{sent_count}</div>
                        <div class="campaign-status-note">Delivered by sender</div>
                    </div>
                    <div class="campaign-status-card">
                        <div class="campaign-status-label">Replied</div>
                        <div class="campaign-status-value">{replied_count}</div>
                        <div class="campaign-status-note">Contacts with replies</div>
                    </div>
                    <div class="campaign-status-card campaign-status-card--failed">
                        <div class="campaign-status-label">Failed</div>
                        <div class="campaign-status-value">{failed_count}</div>
                        <div class="campaign-status-note">{failed_note}</div>
                    </div>
                    {needs_review_tile}
                </div>
                """,
                unsafe_allow_html=True,
            )

            col1, col2, col3 = st.columns(3)
            with col1:
                if failed_count and st.button(f"Retry all {failed_count} failed", key=f"retry-all-{program_id}"):
                    status_conn.execute(
                        "UPDATE contacts SET status = 'pending', error_message = NULL "
                        "WHERE program_id = ? AND status = 'failed'",
                        (program_id,),
                    )
                    status_conn.commit()
                    st.rerun()
            with col2:
                if needs_review_count and st.button(
                    "Mark needs_review as sent", key=f"resolve-sent-{program_id}"
                ):
                    status_conn.execute(
                        "UPDATE contacts SET status = 'sent' WHERE program_id = ? AND status = 'needs_review'",
                        (program_id,),
                    )
                    status_conn.commit()
                    st.rerun()
            with col3:
                if needs_review_count and st.button(
                    "Mark needs_review as pending", key=f"resolve-pending-{program_id}"
                ):
                    status_conn.execute(
                        "UPDATE contacts SET status = 'pending', error_message = NULL "
                        "WHERE program_id = ? AND status = 'needs_review'",
                        (program_id,),
                    )
                    status_conn.commit()
                    st.rerun()

            all_contacts = status_conn.execute(
                "SELECT id, phone, name, status, sent_at, replied_at, error_message FROM contacts "
                "WHERE program_id = ? ORDER BY status, id",
                (program_id,),
            ).fetchall()

            if not all_contacts:
                st.info("No contacts yet -- use Add contacts above.")
                return

            status_filter = st.multiselect(
                "Filter by status",
                options=["pending", "sending", "sent", "failed", "needs_review"],
                default=[],
                key=f"status-filter-{program_id}",
            )
            selected_contact_ids_key = f"selected-contact-ids-{program_id}"
            rows_to_show = [
                {
                    "id": c[0],
                    "phone": c[1],
                    "name": c[2],
                    "status": c[3],
                    "sent_at": c[4] or "",
                    "replied_at": c[5] or "",
                    "error_message": c[6] or "",
                }
                for c in all_contacts
                if not status_filter or c[3] in status_filter
            ]
            shown_ids = [r["id"] for r in rows_to_show]
            shown_id_set = set(shown_ids)
            selected_ids = [
                contact_id
                for contact_id in st.session_state.get(selected_contact_ids_key, [])
                if contact_id in shown_id_set
            ]
            st.session_state[selected_contact_ids_key] = selected_ids

            toolbar_col1, toolbar_col2, toolbar_col3, toolbar_col4 = st.columns([1, 1, 1, 2])
            with toolbar_col1:
                if rows_to_show and st.button(
                    f"Select all ({len(rows_to_show)})", key=f"select-all-shown-{program_id}"
                ):
                    st.session_state[selected_contact_ids_key] = shown_ids
                    st.rerun()
            with toolbar_col2:
                if selected_ids and st.button("Clear selection", key=f"clear-selection-{program_id}"):
                    st.session_state[selected_contact_ids_key] = []
                    st.rerun()
            with toolbar_col3:
                if st.button("Send", disabled=pending_count == 0, key=f"send-pending-{program_id}"):
                    status_conn.execute("UPDATE programs SET paused = 0 WHERE id = ?", (program_id,))
                    status_conn.commit()
                    st.success("Campaign is active. The worker will send pending contacts.")
                    st.rerun()
            with toolbar_col4:
                if selected_ids:
                    st.caption(f"{len(selected_ids)} selected")

            selected_id_set = set(st.session_state.get(selected_contact_ids_key, [])) & shown_id_set
            rows_to_edit = [{"Select": r["id"] in selected_id_set, **r} for r in rows_to_show]
            status_signature = "|".join(
                f"{r['id']}:{r['status']}:{r['sent_at']}:{r['replied_at']}:{r['error_message']}:{r['id'] in selected_id_set}"
                for r in rows_to_edit
            )
            table_version = hashlib.sha1(status_signature.encode("utf-8")).hexdigest()[:12]
            edited_rows = st.data_editor(
                rows_to_edit,
                hide_index=True,
                column_order=["Select", "phone", "name", "status", "sent_at", "replied_at", "error_message"],
                disabled=["phone", "name", "status", "sent_at", "replied_at", "error_message"],
                key=f"contact-table-{program_id}-{table_version}",
            )
            selected_ids = [r["id"] for r in edited_rows if r["Select"]]
            st.session_state[selected_contact_ids_key] = selected_ids

            bcol1, bcol2 = st.columns(2)
            with bcol1:
                if selected_ids and st.button("Retry selected failed", key=f"retry-selected-{program_id}"):
                    for contact_id in selected_ids:
                        status_conn.execute(
                            "UPDATE contacts SET status = 'pending', error_message = NULL "
                            "WHERE id = ? AND status = 'failed'",
                            (contact_id,),
                        )
                    status_conn.commit()
                    st.rerun()
            with bcol2:
                if selected_ids and st.button("Delete selected", key=f"delete-selected-{program_id}"):
                    deleted_count = 0
                    for contact_id in selected_ids:
                        if delete_contact_from_table(status_conn, contact_id):
                            deleted_count += 1
                    status_conn.commit()
                    st.session_state[selected_contact_ids_key] = []
                    if deleted_count < len(selected_ids):
                        st.warning("Skipped contact(s) currently being sent.")
                    st.rerun()
        finally:
            status_conn.close()

    render_status_and_contacts()
