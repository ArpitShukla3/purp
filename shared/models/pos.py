"""
SQLAlchemy ORM model for POS transactions.

POS rows are correlated to visitor sessions heuristically — by matching
the transaction timestamp to the nearest billing-zone visitor session.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base


class POSTransaction(Base):
    """A point-of-sale transaction record."""

    __tablename__ = "pos_transactions"

    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    items: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Correlated visitor ID (set by the correlation engine, nullable until matched)
    matched_visitor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_method: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Raw metadata
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<POSTransaction(id={self.transaction_id!r}, "
            f"amount={self.amount}, visitor={self.matched_visitor_id!r})>"
        )
