"""FastAPI application entry point.

Run with: .venv/bin/uvicorn web.main:app --host 127.0.0.1 --port 8000
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.worker_supervisor import ensure_worker_running
from web.routers import campaigns, connection, settings
from web.templating import STATIC_DIR

logger = logging.getLogger("silkroad.web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("SKIP_AUTO_WORKER") != "1":
        worker_ok, worker_message = ensure_worker_running()
        if worker_ok:
            logger.info("Worker supervisor: %s", worker_message)
        else:
            logger.error("Worker supervisor: %s", worker_message)
    else:
        logger.info("SKIP_AUTO_WORKER=1 — not starting the WhatsApp worker.")
    yield


app = FastAPI(title="Silkroad WhatsApp Sender", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(campaigns.router)
app.include_router(connection.router)
app.include_router(settings.router)


@app.get("/")
def root():
    return RedirectResponse(url="/campaigns")
