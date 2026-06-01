"""
SQLAlchemy ORM model for store events.

Maps to the ``events`` table in PostgreSQL.  The ``event_id`` column
uses a client-generated UUID string (from the detection pipeline),
enabling idempotent upserts — re-posting the same event is a no-op.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base


class Event(Base):
    """Persisted store event."""

    __tablename__ = "events"

    # Client-generated UUID — natural primary key for deduplication
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    store_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    camera_id: Mapped[str] = mapped_column(String(64), nullable=False)
    visitor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Extended fields
    zone_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dwell_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    session_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Full metadata as JSONB for flexible querying
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    # Audit columns
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Composite indexes for common query patterns
    __table_args__ = (
        Index("idx_events_store_type", "store_id", "event_type"),
        Index("idx_events_store_ts", "store_id", "timestamp"),
        Index("idx_events_visitor_ts", "visitor_id", "timestamp"),
        Index("idx_events_zone", "zone_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Event(event_id={self.event_id!r}, "
            f"type={self.event_type!r}, "
            f"visitor={self.visitor_id!r})>"
        )
