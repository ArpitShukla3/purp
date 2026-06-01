"""
GET /visitors/active — active visitors by zone.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.repository import EventRepository
from api.schemas import ActiveVisitor, ActiveVisitorsResponse, ErrorResponse
from shared.database import get_db

logger = structlog.get_logger("api.visitors")
router = APIRouter(prefix="/visitors", tags=["visitors"])


@router.get(
    "/active",
    response_model=ActiveVisitorsResponse,
    responses={
        503: {"model": ErrorResponse, "description": "Database unavailable"},
    },
)
async def get_active_visitors(
    store_id: str = Query(default="purplle-001", description="Store to query"),
    zone: str | None = Query(default=None, description="Filter by zone_id"),
    db: AsyncSession = Depends(get_db),
) -> ActiveVisitorsResponse | Response:
    """
    Return visitors currently active in zones.

    A visitor is "active" if their most recent zone event is a ZONE_ENTER
    (they haven't left the zone yet).
    """
    try:
        rows = await EventRepository.get_active_visitors_by_zone(
            db, store_id, zone_id=zone,
        )

        visitors = [
            ActiveVisitor(
                visitor_id=r["visitor_id"],
                zone_id=r["zone_id"],
                is_staff=r["is_staff"],
                since=r["since"],
            )
            for r in rows
        ]

        return ActiveVisitorsResponse(
            store_id=store_id,
            zone_id=zone,
            active_count=len(visitors),
            visitors=visitors,
        )

    except Exception as exc:
        logger.error("active_visitors_failed", error=str(exc))
        return Response(
            content=ErrorResponse(
                error="database_unavailable",
                detail="Failed to query active visitors.",
            ).model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
