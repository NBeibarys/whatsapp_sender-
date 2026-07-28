"""Shared Jinja2Templates instance (kept out of main.py to avoid circular imports)."""

import os

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)
