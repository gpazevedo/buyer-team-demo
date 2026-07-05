"""Demo Harness — FastAPI entry point (PRD-020).

Standalone FastAPI + React 19 web application demonstrating the Buyer Team
end-to-end procurement lifecycle for the Blue Jets tenant.

Start:
  cd impl && uv run uvicorn demo_harness.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

# Set before importing test_tenant_app modules (they read SKILL_MODE at import time)
os.environ.setdefault("SKILL_MODE", "live")
os.environ.setdefault("ENV", "dev")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from demo_harness.observer import router as demo_router
from demo_harness.offer_projection import poll_loop
from demo_harness.seed import seed, seed_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("demo_harness")

_poll_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _poll_task
    _poll_task = asyncio.create_task(poll_loop())
    logger.info("offer projection poll loop started")
    yield
    if _poll_task:
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
        logger.info("offer projection poll loop stopped")


app = FastAPI(
    title="Buyer Team Lifecycle Demo Harness",
    version="0.1.0",
    description="Demonstrates the full procurement lifecycle — PR → PO — for the Blue Jets tenant.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(demo_router)


# ── Seed admin routes ─────────────────────────────────────────────


@app.post("/demo/seed")
def run_seed():
    """Idempotent Blue Jets seed — safe to re-run."""
    os.environ.setdefault("SKILL_MODE", "live")
    result = seed()
    return {"status": "ok", **result}


@app.get("/demo/seed/status")
def get_seed_status():
    """Check which Blue Jets entities exist."""
    os.environ.setdefault("SKILL_MODE", "live")
    return seed_status()


@app.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}
