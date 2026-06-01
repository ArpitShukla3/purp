"""
GET /stores/{store_id}/anomalies — anomaly detection.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.analytics.anomalies import detect_anomalies
from api.schemas import ErrorResponse
from shared.database import get_db

logger = structlog.get_logger("api.anomalies")
router = APIRouter(prefix="/stores", tags=["analytics"])


@router.get(
    "/{store_id}/anomalies",
    response_model=None,
    responses={503: {"model": ErrorResponse}},
)
async def get_anomalies(
    store_id: str,
    queue_threshold: int = Query(default=5, ge=1),
    conversion_drop: float = Query(default=50.0, ge=0),
    dead_zone_minutes: int = Query(default=30, ge=1),
    dwell_multiplier: float = Query(default=2.0, ge=1.0),
    db: AsyncSession = Depends(get_db),
) -> dict | Response:
    """
    Detect operational anomalies.

    Types: queue_surge, conversion_drop, dead_zone, high_dwell.
    All thresholds are configurable via query params.
    """
    try:
        return await detect_anomalies(
            db, store_id,
            queue_surge_threshold=queue_threshold,
            conversion_drop_pct=conversion_drop,
            dead_zone_minutes=dead_zone_minutes,
            high_dwell_multiplier=dwell_multiplier,
        )
    except Exception as exc:
        logger.error("anomalies_failed", error=str(exc), store_id=store_id)
        return Response(
            content=ErrorResponse(error="database_unavailable",
                                  detail="Failed to detect anomalies.").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
