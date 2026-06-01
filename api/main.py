"""
Store Intelligence — API Service
=================================

FastAPI application providing:
- ``POST /events/ingest``       — batch event ingestion with deduplication
- ``GET  /stores/{id}/metrics`` — aggregated store metrics
- ``GET  /visitors/active``     — active visitors by zone
- ``GET  /health``              — liveness/readiness with DB status
- ``GET  /metrics``             — Prometheus-compatible metrics
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from sqlalchemy import text

from api.routes import ingest, metrics, visitors, analytics, heatmap, anomalies_route
from api.schemas import HealthResponse
from shared.config import settings
from shared.database import engine, get_db, async_session_factory
from shared.logging import setup_logging

# ── Logging ───────────────────────────────────────────────────────────
setup_logging()
logger = structlog.get_logger("api")

# ── Prometheus metrics ────────────────────────────────────────────────
registry = CollectorRegistry()

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
    registry=registry,
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    registry=registry,
)
EVENTS_INGESTED = Counter(
    "events_ingested_total",
    "Total events ingested",
    registry=registry,
)


# ── Lifespan ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle hook."""
    logger.info(
        "api_starting",
        environment=settings.ENVIRONMENT,
        debug=settings.DEBUG,
    )
    # Verify DB connectivity and create tables if needed
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("database_connected")
    except Exception:
        logger.exception("database_connection_failed")
    yield
    await engine.dispose()
    logger.info("api_shutdown")


# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Store Intelligence API",
    version="0.3.0",
    description="Event ingestion, metrics, analytics, and active visitor tracking for Purplle stores.",
    docs_url="/docs" if settings.DEBUG or settings.ENVIRONMENT == "local" else None,
    lifespan=lifespan,
)

# ── Include routers ──────────────────────────────────────────────────
app.include_router(ingest.router)
app.include_router(metrics.router)
app.include_router(visitors.router)
app.include_router(analytics.router)
app.include_router(heatmap.router)
app.include_router(anomalies_route.router)

# ── Static files (frontend dashboard) ────────────────────────────────
_frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/static", StaticFiles(directory=_frontend_dir), name="static")


# ── Middleware ────────────────────────────────────────────────────────
@app.middleware("http")
async def metrics_middleware(request: Request, call_next) -> Response:
    """Record request count and latency for Prometheus."""
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed = time.perf_counter() - start

    path = request.url.path
    method = request.method
    status = str(response.status_code)

    REQUEST_COUNT.labels(method=method, path=path, status=status).inc()
    REQUEST_LATENCY.labels(method=method, path=path).observe(elapsed)

    logger.info(
        "http_request",
        method=method,
        path=path,
        status=status,
        duration=round(elapsed, 4),
    )
    return response


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"], response_model=HealthResponse)
async def health() -> HealthResponse:
    """
    Liveness / readiness probe.

    Reports service status, database connectivity, total event count,
    and last event timestamp for freshness monitoring.
    """
    db_status = "unavailable"
    total_events = None
    last_event_at = None

    try:
        async with async_session_factory() as session:
            # Check DB connection
            await session.execute(text("SELECT 1"))
            db_status = "connected"

            # Get freshness info
            from api.repository import EventRepository

            total_events = await EventRepository.get_event_count(session)
            last_ts = await EventRepository.get_last_event_time(session)
            if last_ts:
                last_event_at = last_ts.isoformat()
    except Exception:
        logger.warning("health_db_check_failed")

    return HealthResponse(
        status="ok" if db_status == "connected" else "degraded",
        environment=settings.ENVIRONMENT,
        database=db_status,
        total_events=total_events,
        last_event_at=last_event_at,
    )


@app.get("/metrics", tags=["ops"], include_in_schema=False)
async def prometheus_metrics() -> Response:
    """Prometheus-compatible metrics endpoint."""
    return Response(
        content=generate_latest(registry),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/", tags=["dashboard"], include_in_schema=False)
async def dashboard_root() -> FileResponse:
    """Serve the web dashboard."""
    index = os.path.join(_frontend_dir, "index.html")
    if os.path.isfile(index):
        return FileResponse(index, media_type="text/html")
    return Response(content="Dashboard not found. Place frontend/index.html.", status_code=404)
