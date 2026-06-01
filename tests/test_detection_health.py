"""
Smoke tests for the detection service health endpoint.

Run with::

    pytest tests/test_detection_health.py -v

NOTE: These tests require the detection service to be running.
They are integration tests meant to be run against a live container.
"""

from __future__ import annotations

import pytest
import httpx


DETECTION_HEALTH_URL = "http://localhost:8001/health"


@pytest.mark.integration
def test_detection_health():
    """Verify the detection service health endpoint is reachable."""
    resp = httpx.get(DETECTION_HEALTH_URL, timeout=5)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "detection"
