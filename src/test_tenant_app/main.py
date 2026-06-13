"""Buyer Team Test Tenant App — FastAPI entry point."""
import time

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from test_tenant_app.api import catalog, datasets, orders, requisitions

logger = structlog.get_logger()

app = FastAPI(
    title="Buyer Team Test Tenant App",
    version="0.1.0",
    description="Demo procurement lifecycle: dataset visualization, PR simulation, PO inbox.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        latency_ms=latency_ms,
    )
    return response


app.include_router(datasets.router)
app.include_router(requisitions.router)
app.include_router(orders.router)
app.include_router(catalog.router)


@app.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}
