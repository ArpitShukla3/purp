"""
GET /stores/{store_id}/funnel — customer conversion funnel.
POST /stores/{store_id}/pos/correlate — run POS-visitor correlation.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.analytics.funnel import compute_funnel
from api.analytics.pos_correlation import correlate_pos
from api.schemas import ErrorResponse
from shared.database import get_db

logger = structlog.get_logger("api.analytics")
router = APIRouter(prefix="/stores", tags=["analytics"])


@router.get(
    "/{store_id}/funnel",
    response_model=None,
    responses={503: {"model": ErrorResponse}},
)
async def get_funnel(
    store_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict | Response:
    """
    Customer conversion funnel.

    Stages: entered → browsed_zone → engaged_dwell → reached_checkout → purchased.
    All stages exclude staff visitors.
    """
    try:
        return await compute_funnel(db, store_id)
    except Exception as exc:
        logger.error("funnel_failed", error=str(exc), store_id=store_id)
        return Response(
            content=ErrorResponse(error="database_unavailable",
                                  detail="Failed to compute funnel.").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )


@router.post(
    "/{store_id}/pos/correlate",
    response_model=None,
    responses={503: {"model": ErrorResponse}},
)
async def run_pos_correlation(
    store_id: str,
    window_minutes: int = Query(default=10, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
) -> dict | Response:
    """
    Run POS-to-visitor correlation.

    Matches POS transactions to the nearest plausible visitor session.
    Updates the pos_transactions table with matched_visitor_id.
    """
    try:
        return await correlate_pos(db, store_id, window_minutes=window_minutes)
    except Exception as exc:
        logger.error("pos_correlation_failed", error=str(exc), store_id=store_id)
        return Response(
            content=ErrorResponse(error="database_unavailable",
                                  detail="Failed to correlate POS.").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
