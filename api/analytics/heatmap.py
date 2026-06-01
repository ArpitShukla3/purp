"""
Heatmap computation engine.

Aggregates zone visit counts by time bucket, producing a matrix of
(zone × time_bucket) values that can power a heatmap visualization.

Values are normalized per-zone to a 0.0–1.0 scale so that zones with
different total traffic are visually comparable.  The raw counts are
also returned for tooltip-style display.

All calculations exclude staff visitors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.event import Event

logger = structlog.get_logger("analytics.heatmap")

# Default zones to include (exclude "outside")
_EXCLUDE_ZONES = {"outside"}


async def compute_heatmap(
    session: AsyncSession,
    store_id: str,
    bucket_minutes: int = 5,
    zone_filter: str | None = None,
) -> dict[str, Any]:
    """
    Compute zone visit heatmap data.

    Groups ZONE_ENTER events by zone_id and time bucket.
    Each cell = count of zone entries in that time window.
    Normalized values are 0.0–1.0 relative to each zone's maximum.

    Args:
        session: DB session
        store_id: Target store
        bucket_minutes: Width of each time bucket in minutes
        zone_filter: Optional zone_id to filter to a single zone

    Returns:
        Dict with zone_ids, time_buckets, raw counts, and normalized values.
    """
    # Get time range from events
    q_range = (
        select(
            func.min(Event.timestamp).label("min_ts"),
            func.max(Event.timestamp).label("max_ts"),
        )
        .where(Event.store_id == store_id, Event.is_staff == False)  # noqa: E712
    )
    row = (await session.execute(q_range)).one_or_none()

    if row is None or row.min_ts is None:
        return {
            "store_id": store_id,
            "bucket_minutes": bucket_minutes,
            "zones": [],
            "time_buckets": [],
            "heatmap": [],
        }

    min_ts: datetime = row.min_ts
    max_ts: datetime = row.max_ts

    # Build time buckets
    bucket_delta = timedelta(minutes=bucket_minutes)
    buckets: list[tuple[datetime, datetime]] = []
    t = min_ts
    while t <= max_ts:
        buckets.append((t, t + bucket_delta))
        t += bucket_delta

    if not buckets:
        buckets = [(min_ts, max_ts)]

    # Get all zone entries (excluding staff)
    q = (
        select(Event.zone_id, Event.timestamp)
        .where(
            Event.store_id == store_id,
            Event.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
            Event.is_staff == False,  # noqa: E712
            Event.zone_id.isnot(None),
        )
    )
    if zone_filter:
        q = q.where(Event.zone_id == zone_filter)

    result = await session.execute(q)
    events = result.all()

    # Collect all zones
    zone_ids = sorted(set(
        e.zone_id for e in events
        if e.zone_id not in _EXCLUDE_ZONES
    ))

    # Build count matrix: zone_id → [counts per bucket]
    matrix: dict[str, list[int]] = {z: [0] * len(buckets) for z in zone_ids}

    for e in events:
        if e.zone_id in _EXCLUDE_ZONES:
            continue
        if e.zone_id not in matrix:
            continue
        # Find which bucket this event falls into
        for i, (bstart, bend) in enumerate(buckets):
            if bstart <= e.timestamp < bend:
                matrix[e.zone_id][i] += 1
                break

    # Normalize per zone (0.0 to 1.0)
    normalized: dict[str, list[float]] = {}
    for z, counts in matrix.items():
        max_val = max(counts) if counts else 1
        if max_val == 0:
            max_val = 1
        normalized[z] = [round(c / max_val, 3) for c in counts]

    # Build response
    time_labels = [b[0].isoformat() for b in buckets]

    heatmap_data = []
    for z in zone_ids:
        heatmap_data.append({
            "zone_id": z,
            "raw_counts": matrix[z],
            "normalized": normalized[z],
            "total_visits": sum(matrix[z]),
        })

    return {
        "store_id": store_id,
        "bucket_minutes": bucket_minutes,
        "zones": zone_ids,
        "time_buckets": time_labels,
        "heatmap": heatmap_data,
    }
