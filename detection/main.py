"""
Store Intelligence — Detection Service
========================================

Placeholder entry point for the CCTV detection pipeline.

On startup it:
1. Exposes a lightweight HTTP health-check on ``DETECTION_HEALTH_PORT``.
2. Enters an async loop that will later run YOLO + DeepSORT inference.
"""

from __future__ import annotations

import asyncio
import signal

import structlog
from aiohttp import web

from shared.config import settings
from shared.logging import setup_logging

setup_logging()
logger = structlog.get_logger("detection")

# ── Health-check server ───────────────────────────────────────────────

async def health_handler(_request: web.Request) -> web.Response:
    """Return a simple JSON health response."""
    return web.json_response(
        {"status": "ok", "service": "detection", "environment": settings.ENVIRONMENT}
    )


async def start_health_server() -> web.AppRunner:
    """Start the aiohttp health-check server."""
    app = web.Application()
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", settings.DETECTION_HEALTH_PORT)
    await site.start()
    logger.info("health_server_started", port=settings.DETECTION_HEALTH_PORT)
    return runner


# ── Detection loop (placeholder) ─────────────────────────────────────

async def detection_loop() -> None:
    """
    Placeholder processing loop.

    In the next phase this will:
    - Read frames from an RTSP source or file
    - Run YOLOv8 object detection
    - Run DeepSORT multi-object tracking
    - Emit structured events to Postgres / event bus
    """
    logger.info("detection_loop_started", interval=settings.DETECTION_LOOP_INTERVAL)
    while True:
        logger.debug("detection_tick", msg="waiting for frames")
        await asyncio.sleep(settings.DETECTION_LOOP_INTERVAL)


# ── Main ──────────────────────────────────────────────────────────────

async def main() -> None:
    """Run health server and detection loop concurrently."""
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    runner = await start_health_server()

    detection_task = asyncio.create_task(detection_loop())

    logger.info("detection_service_ready")
    await stop_event.wait()

    logger.info("detection_service_shutting_down")
    detection_task.cancel()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
