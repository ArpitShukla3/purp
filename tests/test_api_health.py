"""
Smoke tests for the API service health and metrics endpoints.

Run with::

    pytest tests/test_api_health.py -v
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_health_returns_ok(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "api"


@pytest.mark.anyio
async def test_metrics_returns_prometheus_format(client: AsyncClient):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"] or "text/plain" in resp.headers.get("content-type", "")
