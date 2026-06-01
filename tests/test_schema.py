"""
Tests for the StoreEvent Pydantic schema.

Validates field constraints, factory helpers, metadata extensions,
and serialization round-trips.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.schemas.events import EventType, StoreEvent, EventMetadata


# ── Valid Event Construction ─────────────────────────────────────────

def test_minimal_valid_event():
    """An event with only required fields should be valid."""
    evt = StoreEvent(
        visitor_id="v-001",
        event_type=EventType.ENTRY,
        confidence=0.9,
    )
    assert evt.event_id  # auto-generated
    assert evt.store_id  # default
    assert evt.camera_id  # default
    assert evt.timestamp is not None
    assert evt.is_staff is False


def test_full_event():
    """An event with all fields populated."""
    evt = StoreEvent(
        event_id="custom-id",
        store_id="store-99",
        camera_id="cam-5",
        visitor_id="v-002",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        event_type=EventType.ZONE_DWELL,
        confidence=0.95,
        zone_id="display_left",
        dwell_ms=15000,
        is_staff=False,
        session_seq=1,
        metadata=EventMetadata(queue_depth=3),
    )
    assert evt.event_id == "custom-id"
    assert evt.zone_id == "display_left"
    assert evt.dwell_ms == 15000


# ── Validation Failures ──────────────────────────────────────────────

def test_missing_visitor_id():
    with pytest.raises(ValidationError) as exc_info:
        StoreEvent(event_type=EventType.ENTRY, confidence=0.9)
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("visitor_id",) for e in errors)


def test_invalid_event_type():
    with pytest.raises(ValidationError):
        StoreEvent(
            visitor_id="v-001",
            event_type="NOT_A_REAL_TYPE",
            confidence=0.9,
        )


def test_missing_confidence():
    with pytest.raises(ValidationError):
        StoreEvent(
            visitor_id="v-001",
            event_type=EventType.ENTRY,
        )


# ── Event Types ──────────────────────────────────────────────────────

def test_all_event_types_valid():
    """All EventType enum values should produce valid events."""
    for etype in EventType:
        evt = StoreEvent(
            visitor_id="v-test",
            event_type=etype,
            confidence=0.8,
        )
        assert evt.event_type == etype


def test_event_type_count():
    """There should be exactly 8 event types."""
    assert len(EventType) == 8


# ── Serialization ────────────────────────────────────────────────────

def test_json_roundtrip():
    """Event should serialize to JSON and deserialize back."""
    evt = StoreEvent(
        visitor_id="v-rt",
        event_type=EventType.EXIT,
        confidence=0.88,
        zone_id="entrance",
        dwell_ms=5000,
    )
    json_str = evt.model_dump_json()
    parsed = json.loads(json_str)
    assert parsed["visitor_id"] == "v-rt"
    assert parsed["event_type"] == "EXIT"
    assert parsed["dwell_ms"] == 5000

    # Deserialize back
    restored = StoreEvent.model_validate_json(json_str)
    assert restored.event_id == evt.event_id


def test_jsonl_format():
    """Events should be serializable as JSONL (one JSON object per line)."""
    events = [
        StoreEvent(visitor_id=f"v-{i}", event_type=EventType.ENTRY, confidence=0.9)
        for i in range(3)
    ]
    lines = [e.model_dump_json() for e in events]
    assert len(lines) == 3
    for line in lines:
        parsed = json.loads(line)
        assert "event_id" in parsed


# ── Metadata ─────────────────────────────────────────────────────────

def test_metadata_queue_depth():
    meta = EventMetadata(queue_depth=5)
    assert meta.queue_depth == 5


def test_metadata_defaults():
    meta = EventMetadata()
    assert meta.queue_depth is None


# ── Auto-generated Fields ────────────────────────────────────────────

def test_event_id_auto_generated():
    """Two events without explicit IDs get different event_ids."""
    e1 = StoreEvent(visitor_id="v1", event_type=EventType.ENTRY, confidence=0.9)
    e2 = StoreEvent(visitor_id="v2", event_type=EventType.ENTRY, confidence=0.9)
    assert e1.event_id != e2.event_id


def test_timestamp_auto_generated():
    """Events without explicit timestamps get auto-generated ones."""
    evt = StoreEvent(visitor_id="v1", event_type=EventType.ENTRY, confidence=0.9)
    assert evt.timestamp is not None
    assert isinstance(evt.timestamp, datetime)


# ── Staff Flag ───────────────────────────────────────────────────────

def test_staff_default_false():
    evt = StoreEvent(visitor_id="v1", event_type=EventType.ENTRY, confidence=0.9)
    assert evt.is_staff is False


def test_staff_explicit_true():
    evt = StoreEvent(
        visitor_id="v1", event_type=EventType.ENTRY,
        confidence=0.9, is_staff=True,
    )
    assert evt.is_staff is True
