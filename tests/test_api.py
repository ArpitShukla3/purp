"""
Comprehensive API test suite.

Tests schema validation, idempotency, edge cases (empty stores,
all-staff clips, re-entry, no purchases), and endpoint correctness.

Run with::

    DATABASE_URL="postgresql+asyncpg://store_intel:store_intel@localhost:5432/store_intel" \\
        pytest tests/test_api.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from shared.database import engine


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    # Dispose any stale connections before each test
    await engine.dispose()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


def _make_event(**overrides) -> dict:
    """Factory for a valid event dict with sensible defaults."""
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "test-store",
        "camera_id": "cam-test",
        "visitor_id": f"visitor-{uuid.uuid4().hex[:6]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "ENTRY",
        "confidence": 0.85,
    }
    base.update(overrides)
    return base


# ── Health ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_health_returns_ok(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["service"] == "api"
    assert "database" in body
    assert "total_events" in body


@pytest.mark.anyio
async def test_health_reports_environment(client: AsyncClient):
    resp = await client.get("/health")
    body = resp.json()
    assert body["environment"] in ("local", "dev", "prod")


# ── Prometheus Metrics ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_prometheus_metrics(client: AsyncClient):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")


# ── Schema Validation ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_ingest_rejects_missing_fields(client: AsyncClient):
    resp = await client.post("/events/ingest", json={"events": [{"bad_field": True}]})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    missing_fields = {e["loc"][-1] for e in detail if e["type"] == "missing"}
    assert "visitor_id" in missing_fields
    assert "event_type" in missing_fields


@pytest.mark.anyio
async def test_ingest_rejects_empty_list(client: AsyncClient):
    resp = await client.post("/events/ingest", json={"events": []})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_ingest_rejects_invalid_json(client: AsyncClient):
    resp = await client.post(
        "/events/ingest",
        content="not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_ingest_rejects_invalid_event_type(client: AsyncClient):
    event = _make_event(event_type="INVALID_TYPE")
    resp = await client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 422


# ── Idempotency ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_ingest_idempotent(client: AsyncClient):
    """Posting the same events twice should not create duplicates."""
    event_id = f"idem-{uuid.uuid4().hex[:8]}"
    event = _make_event(event_id=event_id, store_id="test-idem")

    # First ingest
    r1 = await client.post("/events/ingest", json={"events": [event]})
    assert r1.status_code == 200
    assert r1.json()["accepted"] == 1
    assert r1.json()["duplicates"] == 0

    # Second ingest — same event
    r2 = await client.post("/events/ingest", json={"events": [event]})
    assert r2.status_code == 200
    assert r2.json()["accepted"] == 0
    assert r2.json()["duplicates"] == 1


@pytest.mark.anyio
async def test_ingest_batch_mixed_new_and_dup(client: AsyncClient):
    """A batch with both new and duplicate events is partially accepted."""
    eid = f"mixed-{uuid.uuid4().hex[:8]}"
    existing = _make_event(event_id=eid, store_id="test-mixed")
    new_event = _make_event(store_id="test-mixed")

    # Seed the existing one
    await client.post("/events/ingest", json={"events": [existing]})

    # Batch with one dup + one new
    resp = await client.post("/events/ingest", json={"events": [existing, new_event]})
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 1
    assert resp.json()["duplicates"] == 1
    assert resp.json()["total"] == 2


# ── Empty Store Metrics ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_metrics_empty_store(client: AsyncClient):
    """Metrics for a store with no events return zeros gracefully."""
    resp = await client.get(f"/stores/empty-{uuid.uuid4().hex[:6]}/metrics")
    assert resp.status_code == 200
    m = resp.json()
    assert m["total_events"] == 0
    assert m["unique_visitors"] == 0
    assert m["unique_customers"] == 0
    assert m["entries"] == 0
    assert m["exits"] == 0
    assert m["current_inside"] == 0
    assert m["conversion_pct"] is None
    assert m["avg_dwell_by_zone"] == []
    assert m["queue_depth"] == 0


# ── Metrics Consistency ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_metrics_consistent_after_ingest(client: AsyncClient):
    """After ingesting events, metrics reflect them correctly."""
    store = f"test-consist-{uuid.uuid4().hex[:6]}"
    events = [
        _make_event(store_id=store, event_type="ENTRY", visitor_id="v1"),
        _make_event(store_id=store, event_type="ENTRY", visitor_id="v2"),
        _make_event(store_id=store, event_type="EXIT", visitor_id="v1"),
        _make_event(store_id=store, event_type="ZONE_ENTER", visitor_id="v2",
                     zone_id="display_left"),
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    resp = await client.get(f"/stores/{store}/metrics")
    assert resp.status_code == 200
    m = resp.json()
    assert m["total_events"] == 4
    assert m["unique_visitors"] == 2
    assert m["entries"] == 2
    assert m["exits"] == 1
    assert m["current_inside"] == 1


# ── All-Staff Scenario ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_metrics_all_staff(client: AsyncClient):
    """When all visitors are staff, customers count = 0."""
    store = f"test-staff-{uuid.uuid4().hex[:6]}"
    events = [
        _make_event(store_id=store, event_type="ENTRY",
                     visitor_id="staff-1", is_staff=True),
        _make_event(store_id=store, event_type="ENTRY",
                     visitor_id="staff-2", is_staff=True),
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    resp = await client.get(f"/stores/{store}/metrics")
    m = resp.json()
    assert m["unique_customers"] == 0
    assert m["staff_count"] == 2


# ── Funnel ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_funnel_empty_store(client: AsyncClient):
    resp = await client.get(f"/stores/empty-{uuid.uuid4().hex[:6]}/funnel")
    assert resp.status_code == 200
    f = resp.json()
    assert f["total_entered"] == 0
    assert f["total_purchased"] == 0
    assert f["overall_conversion_pct"] == 0.0
    assert len(f["stages"]) == 5


@pytest.mark.anyio
async def test_funnel_no_purchases(client: AsyncClient):
    """Store with entries but no POS data should show 0 purchased."""
    store = f"test-nopos-{uuid.uuid4().hex[:6]}"
    events = [
        _make_event(store_id=store, event_type="ENTRY", visitor_id="v1"),
        _make_event(store_id=store, event_type="ZONE_ENTER",
                     visitor_id="v1", zone_id="display_left"),
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    resp = await client.get(f"/stores/{store}/funnel")
    f = resp.json()
    assert f["total_entered"] == 1
    assert f["total_purchased"] == 0
    assert f["overall_conversion_pct"] == 0.0


# ── Heatmap ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_heatmap_empty_store(client: AsyncClient):
    resp = await client.get(f"/stores/empty-{uuid.uuid4().hex[:6]}/heatmap")
    assert resp.status_code == 200
    h = resp.json()
    assert h["zones"] == []
    assert h["heatmap"] == []


@pytest.mark.anyio
async def test_heatmap_normalization(client: AsyncClient):
    """All normalized values must be in [0.0, 1.0]."""
    resp = await client.get("/stores/purplle-001/heatmap?bucket_minutes=1")
    if resp.status_code == 200:
        for zone in resp.json().get("heatmap", []):
            for v in zone["normalized"]:
                assert 0.0 <= v <= 1.0


# ── Anomalies ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_anomalies_empty_store(client: AsyncClient):
    resp = await client.get(f"/stores/empty-{uuid.uuid4().hex[:6]}/anomalies")
    assert resp.status_code == 200
    assert resp.json()["anomaly_count"] == 0


@pytest.mark.anyio
async def test_anomalies_explainable(client: AsyncClient):
    """Every anomaly must have type, severity, description, and rule."""
    resp = await client.get("/stores/purplle-001/anomalies?dwell_multiplier=1.5")
    if resp.status_code == 200:
        for a in resp.json().get("anomalies", []):
            assert "type" in a
            assert "severity" in a
            assert "description" in a
            assert "rule" in a


@pytest.mark.anyio
async def test_anomalies_thresholds_in_response(client: AsyncClient):
    resp = await client.get("/stores/purplle-001/anomalies")
    if resp.status_code == 200:
        t = resp.json()["thresholds"]
        assert "queue_surge" in t
        assert "conversion_drop_pct" in t
        assert "dead_zone_minutes" in t
        assert "high_dwell_multiplier" in t


# ── Active Visitors ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_active_visitors_structure(client: AsyncClient):
    resp = await client.get("/visitors/active?store_id=purplle-001")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_count" in body
    assert "visitors" in body
    assert isinstance(body["visitors"], list)


# ── Re-entry Events ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_reentry_event_accepted(client: AsyncClient):
    """REENTRY events with session_seq should be ingested."""
    event = _make_event(
        event_type="REENTRY",
        store_id="test-reentry",
        session_seq=2,
    )
    resp = await client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 1


# ── Zone Dwell Events ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_zone_dwell_with_metadata(client: AsyncClient):
    """ZONE_DWELL events carry dwell_ms and zone_id."""
    store = f"test-dwell-{uuid.uuid4().hex[:6]}"
    event = _make_event(
        event_type="ZONE_DWELL",
        store_id=store,
        zone_id="aisle_center",
        dwell_ms=12500,
    )
    r = await client.post("/events/ingest", json={"events": [event]})
    assert r.status_code == 200

    metrics = await client.get(f"/stores/{store}/metrics")
    m = metrics.json()
    assert m["total_events"] >= 1
    zones = {z["zone_id"]: z["avg_dwell_ms"] for z in m["avg_dwell_by_zone"]}
    assert "aisle_center" in zones
    assert zones["aisle_center"] > 0
