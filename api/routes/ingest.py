"""
POST /events/ingest — batch event ingestion endpoint.

Accepts a list of StoreEvent objects, validates them, and persists
to PostgreSQL with idempotent deduplication (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.repository import EventRepository
from api.schemas import ErrorResponse, IngestRequest, IngestResponse
from shared.database import get_db

logger = structlog.get_logger("api.ingest")
router = APIRouter(prefix="/events", tags=["events"])


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid event data"},
        503: {"model": ErrorResponse, "description": "Database unavailable"},
    },
)
async def ingest_events(
    body: IngestRequest,
    db: AsyncSession = Depends(get_db),
) -> IngestResponse | Response:
    """
    Ingest a batch of store events.

    - Validates all events against the StoreEvent schema
    - Deduplicates by ``event_id`` (idempotent)
    - Returns counts of accepted and duplicate events
    """
    try:
        inserted, skipped = await EventRepository.upsert_batch(db, body.events)

        logger.info(
            "ingest_complete",
            total=len(body.events),
            inserted=inserted,
            skipped=skipped,
        )

        return IngestResponse(
            accepted=inserted,
            duplicates=skipped,
            total=len(body.events),
        )

    except Exception as exc:
        # Database failures → 503 without stack traces
        logger.error("ingest_failed", error=str(exc))
        return Response(
            content=ErrorResponse(
                error="database_unavailable",
                detail="Failed to persist events. Please retry.",
            ).model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
