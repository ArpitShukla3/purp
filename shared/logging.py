"""
Structured JSON logging via *structlog*.

Call ``setup_logging()`` once at service startup.  After that, use::

    import structlog
    logger = structlog.get_logger()
    logger.info("event_name", key="value")

Every log line is machine-parseable JSON — ready for ELK / CloudWatch / etc.
"""

from __future__ import annotations

import logging
import sys

import structlog

from shared.config import settings


def setup_logging() -> None:
    """Configure structlog + stdlib logging for JSON output."""

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL.upper())

    # Quieten noisy third-party loggers
    for noisy in ("uvicorn.access", "uvicorn.error", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
