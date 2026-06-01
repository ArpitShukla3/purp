"""
Centralised application configuration.

All settings are read from environment variables with sensible local defaults.
Import the singleton ``settings`` object anywhere you need config values.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings backed by env vars / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── General ───────────────────────────────────────────────────────
    ENVIRONMENT: Literal["local", "dev", "prod"] = "local"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    # ── Database ──────────────────────────────────────────────────────
    DATABASE_URL: str = (
        "postgresql+asyncpg://store_intel:store_intel@postgres:5432/store_intel"
    )

    # ── Redis (optional, for caching active state) ────────────────────
    REDIS_URL: str | None = None

    # ── API service ───────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # ── Detection service ─────────────────────────────────────────────
    DETECTION_HEALTH_PORT: int = 8001
    DETECTION_LOOP_INTERVAL: float = 5.0  # seconds between placeholder ticks

    # ── Detection pipeline ────────────────────────────────────────────
    DETECTION_MODEL: str = "yolov8n.pt"
    DETECTION_CONFIDENCE: float = 0.3
    DETECTION_SAMPLE_RATE: int = 3
    DETECTION_THRESHOLD_X: float = 900.0
    DETECTION_MIN_OBSERVATIONS: int = 3

    # ── Zone / dwell / re-entry / staff / queue ──────────────────────
    DETECTION_STORE_LAYOUT_PATH: str = "detection/store_layout.json"
    DETECTION_DWELL_THRESHOLD_MS: int = 15000      # 15s minimum dwell
    DETECTION_REENTRY_WINDOW_S: float = 120.0       # 2-minute re-entry window
    DETECTION_STAFF_MIN_DURATION_PCT: float = 0.5   # 50% of video = staff candidate
    DETECTION_STAFF_MAX_MOVEMENT: float = 400.0     # max x-range for staff
    DETECTION_QUEUE_MIN_TIME_S: float = 10.0        # min time before queue abandon counts


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings singleton."""
    return Settings()


settings: Settings = get_settings()
