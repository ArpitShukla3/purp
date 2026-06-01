"""
GET /stores/{store_id}/metrics — store-level aggregated metrics.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.repository import EventRepository
from api.schemas import ErrorResponse, StoreMetrics, ZoneDwellMetric
from shared.database import get_db

logger = structlog.get_logger("api.metrics")
router = APIRouter(prefix="/stores", tags=["metrics"])


@router.get(
    "/{store_id}/metrics",
    response_model=StoreMetrics,
    responses={
        503: {"model": ErrorResponse, "description": "Database unavailable"},
    },
)
async def get_store_metrics(
    store_id: str,
    db: AsyncSession = Depends(get_db),
) -> StoreMetrics | Response:
    """
    Return aggregated metrics for a store.

    Metrics include:
    - Total events, unique visitors, staff count
    - Entry/exit counts and approximate current occupancy
    - Conversion percentage (queue joins / entries)
    - Average dwell by zone
    - Latest queue depth
    """
    try:
        total = await EventRepository.get_event_count(db, store_id=store_id)
        unique_visitors = await EventRepository.get_unique_visitor_count(db, store_id)
        unique_customers = await EventRepository.get_unique_visitor_count(
            db, store_id, is_staff=False
        )
        staff_ids = await EventRepository.get_staff_visitor_ids(db, store_id)
        entries, exits = await EventRepository.get_entry_exit_counts(db, store_id)
        type_counts = await EventRepository.get_event_type_counts(db, store_id)
        dwell_by_zone = await EventRepository.get_avg_dwell_by_zone(db, store_id)
        queue_depth = await EventRepository.get_latest_queue_depth(db, store_id)
        last_event = await EventRepository.get_last_event_time(db, store_id)

        # Conversion: percentage of entries that led to a queue join
        queue_joins = type_counts.get("BILLING_QUEUE_JOIN", 0)
        conversion = round(queue_joins / entries * 100, 1) if entries > 0 else None

        return StoreMetrics(
            store_id=store_id,
            total_events=total,
            unique_visitors=unique_visitors,
            unique_customers=unique_customers,
            staff_count=len(staff_ids),
            entries=entries,
            exits=exits,
            current_inside=max(0, entries - exits),
            conversion_pct=conversion,
            avg_dwell_by_zone=[
                ZoneDwellMetric(zone_id=zid, avg_dwell_ms=ms)
                for zid, ms in dwell_by_zone.items()
            ],
            queue_depth=queue_depth,
            event_type_breakdown=type_counts,
            last_event_at=last_event.isoformat() if last_event else None,
        )

    except Exception as exc:
        logger.error("metrics_failed", error=str(exc), store_id=store_id)
        return Response(
            content=ErrorResponse(
                error="database_unavailable",
                detail="Failed to compute metrics.",
            ).model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
