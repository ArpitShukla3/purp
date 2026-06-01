"""
POS-to-visitor correlation engine.

Matches POS transactions to visitor sessions heuristically.

Algorithm:
  1. For each unmatched POS transaction, find customer ENTRY events
     within a time window around the transaction timestamp.
  2. Among candidates, prefer visitors who also had checkout-zone
     activity (ZONE_ENTER on checkout_approx).
  3. Tie-breaking: pick the visitor whose ENTRY timestamp is closest
     to (but before) the POS transaction timestamp.
  4. Each visitor can only be matched to one transaction (greedy,
     earliest-first).

Assumptions (documented):
  - A purchase happens AFTER the customer enters the store.
  - The matching window defaults to 10 minutes (configurable).
  - Without face recognition, temporal proximity is the best signal.
  - Staff visitors are excluded from matching.
  - If multiple transactions compete for the same visitor, the
    earliest transaction wins (first-come-first-served).
"""

from __future__ import annotations
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.event import Event
from shared.models.pos import POSTransaction

logger = structlog.get_logger("analytics.pos_correlation")


async def correlate_pos(
    session: AsyncSession,
    store_id: str,
    window_minutes: int = 10,
) -> dict[str, Any]:
    """
    Match POS transactions to visitor sessions.

    Updates the pos_transactions table with matched_visitor_id,
    match_confidence, and match_method.

    Returns summary of matching results.
    """
    # Get all POS transactions for this store (ordered by time)
    q_pos = (
        select(POSTransaction)
        .where(POSTransaction.store_id == store_id)
        .order_by(POSTransaction.timestamp.asc())
    )
    pos_rows = (await session.execute(q_pos)).scalars().all()

    if not pos_rows:
        return {"store_id": store_id, "total_transactions": 0,
                "matched": 0, "unmatched": 0, "matches": []}

    # Get all customer ENTRY events
    q_entries = (
        select(Event.visitor_id, Event.timestamp)
        .where(Event.store_id == store_id, Event.event_type == "ENTRY",
               Event.is_staff == False)
        .order_by(Event.timestamp.asc())
    )
    entries = (await session.execute(q_entries)).all()

    # Get visitors who visited checkout zone
    q_checkout = (
        select(Event.visitor_id).distinct()
        .where(Event.store_id == store_id,
               Event.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
               Event.zone_id == "checkout_approx",
               Event.is_staff == False)
    )
    checkout_visitors = {r[0] for r in (await session.execute(q_checkout)).all()}

    # Greedy matching
    used_visitors: set[str] = set()
    matches: list[dict[str, Any]] = []
    window = timedelta(minutes=window_minutes)

    for pos in pos_rows:
        best_visitor = None
        best_score = -1.0
        best_method = "none"

        for entry in entries:
            vid = entry.visitor_id
            if vid in used_visitors:
                continue

            entry_ts = entry.timestamp
            # Visitor must have entered before (or near) the transaction
            time_diff = (pos.timestamp - entry_ts).total_seconds()

            # Allow entries from window_minutes before to 2 min after
            if -120 <= time_diff <= window * 1.0 / timedelta(seconds=1):
                # Score: closer in time = higher score
                proximity_score = max(0, 1.0 - abs(time_diff) / (window_minutes * 60))

                # Bonus for checkout zone activity
                checkout_bonus = 0.3 if vid in checkout_visitors else 0.0

                # Bonus for entering before the transaction (expected flow)
                temporal_bonus = 0.1 if time_diff >= 0 else 0.0

                score = proximity_score + checkout_bonus + temporal_bonus

                if score > best_score:
                    best_score = score
                    best_visitor = vid
                    best_method = "temporal_proximity"
                    if vid in checkout_visitors:
                        best_method = "temporal_proximity+checkout_zone"

        if best_visitor:
            used_visitors.add(best_visitor)
            confidence = min(round(best_score, 3), 1.0)

            # Update DB
            await session.execute(
                update(POSTransaction)
                .where(POSTransaction.transaction_id == pos.transaction_id)
                .values(matched_visitor_id=best_visitor,
                        match_confidence=confidence,
                        match_method=best_method)
            )

            matches.append({
                "transaction_id": pos.transaction_id,
                "visitor_id": best_visitor,
                "confidence": confidence,
                "method": best_method,
                "pos_time": pos.timestamp.isoformat(),
                "amount": pos.amount,
            })

            logger.info("pos_matched", txn=pos.transaction_id,
                        visitor=best_visitor, confidence=confidence)

    matched_count = len(matches)
    unmatched = len(pos_rows) - matched_count

    return {
        "store_id": store_id,
        "total_transactions": len(pos_rows),
        "matched": matched_count,
        "unmatched": unmatched,
        "match_rate_pct": round(matched_count / len(pos_rows) * 100, 1) if pos_rows else 0,
        "window_minutes": window_minutes,
        "matches": matches,
        "assumptions": [
            "Visitors must ENTER before or within 2min after the transaction",
            f"Matching window is {window_minutes} minutes",
            "Checkout-zone visitors get a 0.3 scoring bonus",
            "Tie-breaking: closest temporal proximity wins",
            "Each visitor matched to at most one transaction (greedy)",
            "Staff visitors excluded from matching",
        ],
    }
