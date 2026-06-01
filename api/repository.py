"""
Event repository — persistence layer for store events.

Separates database operations from HTTP handling.  All methods accept
an ``AsyncSession`` for testability and transaction control.

Key design: ``upsert_batch`` uses ``ON CONFLICT DO NOTHING`` on the
``event_id`` primary key, making ingestion idempotent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.event import Event
from shared.schemas.events import StoreEvent

logger = structlog.get_logger("repository")


class EventRepository:
    """Database operations for store events."""

    @staticmethod
    async def upsert_batch(
        session: AsyncSession,
        events: list[StoreEvent],
    ) -> tuple[int, int]:
        """
        Insert events, skipping duplicates by event_id.

        Returns:
            (inserted_count, skipped_count)
        """
        if not events:
            return 0, 0

        rows = []
        for evt in events:
            rows.append(
                {
                    "event_id": evt.event_id,
                    "store_id": evt.store_id,
                    "camera_id": evt.camera_id,
                    "visitor_id": evt.visitor_id,
                    "timestamp": evt.timestamp,
                    "event_type": evt.event_type.value,
                    "confidence": evt.confidence,
                    "zone_id": evt.zone_id,
                    "dwell_ms": evt.dwell_ms,
                    "is_staff": evt.is_staff,
                    "session_seq": evt.session_seq,
                    "metadata_json": evt.metadata.model_dump() if evt.metadata else {},
                }
            )

        # PostgreSQL INSERT ... ON CONFLICT DO NOTHING
        stmt = pg_insert(Event).values(rows).on_conflict_do_nothing(
            index_elements=["event_id"]
        )
        result = await session.execute(stmt)
        inserted = result.rowcount  # type: ignore[union-attr]
        skipped = len(rows) - inserted

        logger.info(
            "events_upserted",
            total=len(rows),
            inserted=inserted,
            skipped=skipped,
        )
        return inserted, skipped

    @staticmethod
    async def get_event_count(
        session: AsyncSession,
        store_id: str | None = None,
    ) -> int:
        """Count events, optionally filtered by store."""
        q = select(func.count()).select_from(Event)
        if store_id:
            q = q.where(Event.store_id == store_id)
        result = await session.execute(q)
        return result.scalar_one()

    @staticmethod
    async def get_event_type_counts(
        session: AsyncSession,
        store_id: str,
    ) -> dict[str, int]:
        """Count events grouped by event_type for a store."""
        q = (
            select(Event.event_type, func.count())
            .where(Event.store_id == store_id)
            .group_by(Event.event_type)
        )
        result = await session.execute(q)
        return {row[0]: row[1] for row in result.all()}

    @staticmethod
    async def get_unique_visitor_count(
        session: AsyncSession,
        store_id: str,
        is_staff: bool | None = None,
    ) -> int:
        """Count unique visitors for a store."""
        q = (
            select(func.count(func.distinct(Event.visitor_id)))
            .where(Event.store_id == store_id)
        )
        if is_staff is not None:
            q = q.where(Event.is_staff == is_staff)
        result = await session.execute(q)
        return result.scalar_one()

    @staticmethod
    async def get_avg_dwell_by_zone(
        session: AsyncSession,
        store_id: str,
    ) -> dict[str, float]:
        """Average dwell time (ms) per zone for a store."""
        q = (
            select(Event.zone_id, func.avg(Event.dwell_ms))
            .where(
                Event.store_id == store_id,
                Event.dwell_ms.isnot(None),
                Event.zone_id.isnot(None),
            )
            .group_by(Event.zone_id)
        )
        result = await session.execute(q)
        return {row[0]: round(float(row[1]), 1) for row in result.all()}

    @staticmethod
    async def get_active_visitors_by_zone(
        session: AsyncSession,
        store_id: str,
        zone_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get visitors currently active in zones.

        A visitor is "active" in a zone if they have a ZONE_ENTER event
        without a corresponding ZONE_EXIT for that zone.
        """
        # Subquery: latest zone event per (visitor, zone)
        subq = (
            select(
                Event.visitor_id,
                Event.zone_id,
                func.max(Event.timestamp).label("last_ts"),
            )
            .where(
                Event.store_id == store_id,
                Event.event_type.in_(["ZONE_ENTER", "ZONE_EXIT"]),
                Event.zone_id.isnot(None),
            )
            .group_by(Event.visitor_id, Event.zone_id)
            .subquery()
        )

        # Join back to get the event_type of the latest event
        q = (
            select(
                Event.visitor_id,
                Event.zone_id,
                Event.event_type,
                Event.is_staff,
                Event.timestamp,
            )
            .join(
                subq,
                (Event.visitor_id == subq.c.visitor_id)
                & (Event.zone_id == subq.c.zone_id)
                & (Event.timestamp == subq.c.last_ts),
            )
            .where(Event.event_type == "ZONE_ENTER")  # still "in" the zone
        )

        if zone_id:
            q = q.where(Event.zone_id == zone_id)

        result = await session.execute(q)
        return [
            {
                "visitor_id": row.visitor_id,
                "zone_id": row.zone_id,
                "is_staff": row.is_staff,
                "since": row.timestamp.isoformat(),
            }
            for row in result.all()
        ]

    @staticmethod
    async def get_latest_queue_depth(
        session: AsyncSession,
        store_id: str,
    ) -> int:
        """
        Get the latest known queue depth.

        Returns the queue_depth from the most recent BILLING_QUEUE_JOIN
        or BILLING_QUEUE_ABANDON event.
        """
        q = (
            select(Event.metadata_json["queue_depth"])
            .where(
                Event.store_id == store_id,
                Event.event_type.in_(["BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"]),
            )
            .order_by(Event.timestamp.desc())
            .limit(1)
        )
        result = await session.execute(q)
        row = result.scalar_one_or_none()
        if row is not None:
            try:
                return int(row)
            except (TypeError, ValueError):
                return 0
        return 0

    @staticmethod
    async def get_last_event_time(
        session: AsyncSession,
        store_id: str | None = None,
    ) -> datetime | None:
        """Get the timestamp of the most recent event."""
        q = select(func.max(Event.timestamp)).select_from(Event)
        if store_id:
            q = q.where(Event.store_id == store_id)
        result = await session.execute(q)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_staff_visitor_ids(
        session: AsyncSession,
        store_id: str,
    ) -> set[str]:
        """Get all visitor IDs flagged as staff."""
        q = (
            select(func.distinct(Event.visitor_id))
            .where(Event.store_id == store_id, Event.is_staff == True)  # noqa: E712
        )
        result = await session.execute(q)
        return {row[0] for row in result.all()}

    @staticmethod
    async def get_entry_exit_counts(
        session: AsyncSession,
        store_id: str,
    ) -> tuple[int, int]:
        """Get total ENTRY and EXIT event counts."""
        q = (
            select(Event.event_type, func.count())
            .where(
                Event.store_id == store_id,
                Event.event_type.in_(["ENTRY", "EXIT"]),
            )
            .group_by(Event.event_type)
        )
        result = await session.execute(q)
        counts = {row[0]: row[1] for row in result.all()}
        return counts.get("ENTRY", 0), counts.get("EXIT", 0)
