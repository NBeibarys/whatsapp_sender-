"""Central configuration: absolute paths and shared constants.

All other modules import paths from here instead of hardcoding them.
"""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "silkroad.db")
MEDIA_DIR = os.path.join(PROJECT_ROOT, "media")

# Heartbeat older than this (seconds) means the worker is considered dead/stale.
WORKER_STALE_AFTER_SECONDS = 120
