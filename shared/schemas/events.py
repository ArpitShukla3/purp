"""
Store event schemas for the detection pipeline.

Every detection event is represented as a ``StoreEvent`` Pydantic model.
These models are the **single source of truth** for event structure
throughout the system — CLI output, API ingestion, and DB storage all
share the same shape.

Event types:
  ENTRY / EXIT            — store entrance crossings
  ZONE_ENTER / ZONE_EXIT  — named zone transitions
  ZONE_DWELL              — sustained presence in a zone
  BILLING_QUEUE_JOIN      — person joins the checkout queue
  BILLING_QUEUE_ABANDON   — person leaves queue without billing
  REENTRY                 — person re-enters within the re-entry window
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ── Event Type Enum ──────────────────────────────────────────────────

class EventType(str, Enum):
    """All event types the detection pipeline can emit."""

    # Store-level entrance events
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    REENTRY = "REENTRY"

    # Zone-level events
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"

    # Billing / queue events
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"


# ── Sub-models ───────────────────────────────────────────────────────

class BoundingBox(BaseModel):
    """Axis-aligned bounding box in pixel coordinates."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


class EventMetadata(BaseModel):
    """Additional context attached to each event."""

    # Spatial / visual
    bbox: BoundingBox | None = None
    crossing_x: float | None = None
    threshold_x: float | None = None
    frame_number: int | None = None
    direction: str | None = None

    # Zone context
    zone_name: str | None = None

    # Queue context
    queue_depth: int | None = None

    # Re-entry context
    reentry_gap_s: float | None = None
    original_visitor_id: str | None = None


# ── Main Event Model ────────────────────────────────────────────────

class StoreEvent(BaseModel):
    """
    A single structured event emitted by the detection pipeline.

    This is the canonical event format used across the whole system.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str = "purplle-001"
    camera_id: str = "cam3"
    visitor_id: str                    # stable visitor ID (survives re-entry)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: EventType
    confidence: float

    # Extended fields (optional, depending on event type)
    zone_id: str | None = None         # which zone this event relates to
    dwell_ms: int | None = None        # milliseconds of dwell time
    is_staff: bool = False             # heuristic staff classification
    session_seq: int | None = None     # event sequence within this visitor's session

    metadata: EventMetadata = Field(default_factory=EventMetadata)

    def to_jsonl(self) -> str:
        """Serialize to a single JSON line (for JSONL output)."""
        return self.model_dump_json()

    @classmethod
    def validate_event(cls, raw: dict | str) -> "StoreEvent":
        """
        Parse and validate a raw event dict or JSON string.

        Raises ``ValidationError`` if the event doesn't match the schema.
        """
        if isinstance(raw, str):
            return cls.model_validate_json(raw)
        return cls.model_validate(raw)

    # ── Factory helpers ──────────────────────────────────────────────

    @classmethod
    def entry(
        cls,
        visitor_id: str,
        confidence: float,
        *,
        store_id: str = "purplle-001",
        camera_id: str = "cam3",
        metadata: EventMetadata | None = None,
        timestamp: datetime | None = None,
        is_staff: bool = False,
        session_seq: int | None = None,
    ) -> StoreEvent:
        """Factory for ENTRY events."""
        return cls(
            store_id=store_id,
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type=EventType.ENTRY,
            confidence=confidence,
            metadata=metadata or EventMetadata(),
            timestamp=timestamp or datetime.now(timezone.utc),
            is_staff=is_staff,
            session_seq=session_seq,
        )

    @classmethod
    def exit(
        cls,
        visitor_id: str,
        confidence: float,
        *,
        store_id: str = "purplle-001",
        camera_id: str = "cam3",
        metadata: EventMetadata | None = None,
        timestamp: datetime | None = None,
        is_staff: bool = False,
        session_seq: int | None = None,
    ) -> StoreEvent:
        """Factory for EXIT events."""
        return cls(
            store_id=store_id,
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type=EventType.EXIT,
            confidence=confidence,
            metadata=metadata or EventMetadata(),
            timestamp=timestamp or datetime.now(timezone.utc),
            is_staff=is_staff,
            session_seq=session_seq,
        )

    @classmethod
    def zone_enter(
        cls,
        visitor_id: str,
        zone_id: str,
        confidence: float,
        *,
        zone_name: str | None = None,
        **kwargs,
    ) -> StoreEvent:
        """Factory for ZONE_ENTER events."""
        meta = kwargs.pop("metadata", None) or EventMetadata(zone_name=zone_name)
        if zone_name and not meta.zone_name:
            meta.zone_name = zone_name
        return cls(
            visitor_id=visitor_id,
            event_type=EventType.ZONE_ENTER,
            confidence=confidence,
            zone_id=zone_id,
            metadata=meta,
            **kwargs,
        )

    @classmethod
    def zone_exit(
        cls,
        visitor_id: str,
        zone_id: str,
        confidence: float,
        *,
        zone_name: str | None = None,
        dwell_ms: int | None = None,
        **kwargs,
    ) -> StoreEvent:
        """Factory for ZONE_EXIT events."""
        meta = kwargs.pop("metadata", None) or EventMetadata(zone_name=zone_name)
        if zone_name and not meta.zone_name:
            meta.zone_name = zone_name
        return cls(
            visitor_id=visitor_id,
            event_type=EventType.ZONE_EXIT,
            confidence=confidence,
            zone_id=zone_id,
            dwell_ms=dwell_ms,
            metadata=meta,
            **kwargs,
        )

    @classmethod
    def zone_dwell(
        cls,
        visitor_id: str,
        zone_id: str,
        dwell_ms: int,
        confidence: float,
        *,
        zone_name: str | None = None,
        **kwargs,
    ) -> StoreEvent:
        """Factory for ZONE_DWELL events."""
        meta = kwargs.pop("metadata", None) or EventMetadata(zone_name=zone_name)
        if zone_name and not meta.zone_name:
            meta.zone_name = zone_name
        return cls(
            visitor_id=visitor_id,
            event_type=EventType.ZONE_DWELL,
            confidence=confidence,
            zone_id=zone_id,
            dwell_ms=dwell_ms,
            metadata=meta,
            **kwargs,
        )
