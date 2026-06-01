"""
GET /stores/{store_id}/heatmap — zone visit heatmap.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.analytics.heatmap import compute_heatmap
from api.schemas import ErrorResponse
from shared.database import get_db

logger = structlog.get_logger("api.heatmap")
router = APIRouter(prefix="/stores", tags=["analytics"])


@router.get(
    "/{store_id}/heatmap",
    response_model=None,
    responses={503: {"model": ErrorResponse}},
)
async def get_heatmap(
    store_id: str,
    bucket_minutes: int = Query(default=5, ge=1, le=60),
    zone: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict | Response:
    """
    Zone visit heatmap.

    Returns a matrix of zone × time_bucket with raw counts and
    per-zone normalized (0.0–1.0) values.
    """
    try:
        return await compute_heatmap(db, store_id, bucket_minutes, zone_filter=zone)
    except Exception as exc:
        logger.error("heatmap_failed", error=str(exc), store_id=store_id)
        return Response(
            content=ErrorResponse(error="database_unavailable",
                                  detail="Failed to compute heatmap.").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
