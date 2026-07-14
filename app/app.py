import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st

from app.worker_supervisor import WORKER_LOG_PATH, ensure_worker_running

st.title("Silkroad WhatsApp Sender")

if os.environ.get("SKIP_AUTO_WORKER") == "1":
    st.caption("WhatsApp worker autostart skipped for this run.")
else:
    worker_ok, worker_message = ensure_worker_running()
    if worker_ok:
        st.caption(worker_message)
    else:
        st.error(worker_message)
        st.caption(f"Worker log: {WORKER_LOG_PATH}")

st.write("Use the pages in the sidebar: Campaign, Connection, Settings.")
