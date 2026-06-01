"""
Pydantic request / response models for the API.

Separated from ORM models and the detection event schema to keep
the API contract clear and versioned independently.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from shared.schemas.events import StoreEvent


# ── Ingest ───────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    """Batch event ingestion request."""

    events: list[StoreEvent] = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="List of store events to ingest (1–5000).",
    )


class IngestResponse(BaseModel):
    """Response from event ingestion."""

    accepted: int = Field(description="Number of events successfully stored.")
    duplicates: int = Field(description="Number of duplicate events skipped.")
    total: int = Field(description="Total events in the request.")


# ── Metrics ──────────────────────────────────────────────────────────

class ZoneDwellMetric(BaseModel):
    """Average dwell time for a single zone."""

    zone_id: str
    avg_dwell_ms: float


class StoreMetrics(BaseModel):
    """Aggregated store-level metrics."""

    store_id: str
    total_events: int
    unique_visitors: int
    unique_customers: int         # visitors excluding staff
    staff_count: int
    entries: int
    exits: int
    current_inside: int           # entries - exits (approximate)
    conversion_pct: float | None  # % of entries that triggered a queue join
    avg_dwell_by_zone: list[ZoneDwellMetric]
    queue_depth: int
    event_type_breakdown: dict[str, int]
    last_event_at: str | None


# ── Active Visitors ──────────────────────────────────────────────────

class ActiveVisitor(BaseModel):
    """A visitor currently active in a zone."""

    visitor_id: str
    zone_id: str
    is_staff: bool
    since: str  # ISO timestamp


class ActiveVisitorsResponse(BaseModel):
    """Response for active visitors query."""

    store_id: str
    zone_id: str | None = None
    active_count: int
    visitors: list[ActiveVisitor]


# ── Health ───────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    """Service health response."""

    status: str
    service: str = "api"
    environment: str
    database: str      # "connected" or "unavailable"
    total_events: int | None = None
    last_event_at: str | None = None


# ── Errors ───────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str | None = None
