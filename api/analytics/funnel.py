"""
Funnel computation engine.

Computes the entry → zone-visit → checkout-queue → purchase funnel
for a store, excluding staff visitors.  Each stage counts unique
visitors (no double-counting).

Funnel stages:
  1. **entered**          — unique customers who had an ENTRY event
  2. **browsed_zone**     — subset who visited at least one product zone
  3. **engaged** (dwell)  — subset who had a ZONE_DWELL event (lingered)
  4. **reached_checkout**  — subset who triggered BILLING_QUEUE_JOIN
  5. **purchased**         — subset matched to a POS transaction

Design decisions:
  - Each stage is a strict subset of the previous (no visitor can be
    counted in stage N without being in stage N-1).
  - Staff visitors (is_staff=true) are excluded from all stages.
  - "browsed_zone" counts visitors who entered any non-entrance,
    non-outside zone (i.e. product/display/aisle zones).
  - "purchased" uses POS-correlation results from the pos_transactions
    table (matched_visitor_id column).
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import and_, func, select, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.event import Event
from shared.models.pos import POSTransaction

logger = structlog.get_logger("analytics.funnel")

# Zones that represent actual product browsing, not entrance/outside
_BROWSING_ZONES = {"display_left", "aisle_center", "checkout_approx"}
# Entrance zones are not counted as "browsing"
_NON_BROWSING_ZONES = {"entrance", "outside"}


async def compute_funnel(
    session: AsyncSession,
    store_id: str,
) -> dict[str, Any]:
    """
    Compute the full customer funnel for a store.

    Returns a dict with stage names, counts, and drop-off percentages.
    All calculations exclude staff.
    """
    # Stage 1: Entered — unique non-staff visitors with ENTRY events
    q_entered = (
        select(func.count(distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            Event.event_type == "ENTRY",
            Event.is_staff == False,  # noqa: E712
        )
    )
    entered = (await session.execute(q_entered)).scalar_one()

    # Stage 2: Browsed zone — visitors who entered a product zone
    q_browsed = (
        select(func.count(distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            Event.event_type == "ZONE_ENTER",
            Event.is_staff == False,  # noqa: E712
            Event.zone_id.isnot(None),
            Event.zone_id.notin_(list(_NON_BROWSING_ZONES)),
        )
    )
    browsed = (await session.execute(q_browsed)).scalar_one()

    # Stage 3: Engaged (dwell) — visitors who had ZONE_DWELL in a product zone
    q_engaged = (
        select(func.count(distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            Event.event_type == "ZONE_DWELL",
            Event.is_staff == False,  # noqa: E712
            Event.zone_id.isnot(None),
            Event.zone_id.notin_(list(_NON_BROWSING_ZONES)),
        )
    )
    engaged = (await session.execute(q_engaged)).scalar_one()

    # Stage 4: Reached checkout — visitors who joined the billing queue
    q_checkout = (
        select(func.count(distinct(Event.visitor_id)))
        .where(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_JOIN",
            Event.is_staff == False,  # noqa: E712
        )
    )
    reached_checkout = (await session.execute(q_checkout)).scalar_one()

    # Stage 5: Purchased — visitors matched to a POS transaction
    q_purchased = (
        select(func.count(distinct(POSTransaction.matched_visitor_id)))
        .where(
            POSTransaction.store_id == store_id,
            POSTransaction.matched_visitor_id.isnot(None),
        )
    )
    purchased = (await session.execute(q_purchased)).scalar_one()

    # Build funnel with drop-off
    stages = [
        ("entered", entered),
        ("browsed_zone", browsed),
        ("engaged_dwell", engaged),
        ("reached_checkout", reached_checkout),
        ("purchased", purchased),
    ]

    funnel_stages = []
    for i, (name, count) in enumerate(stages):
        prev_count = stages[i - 1][1] if i > 0 else count
        drop_off_pct = (
            round((1 - count / prev_count) * 100, 1) if prev_count > 0 else 0.0
        )
        conversion_from_entry = (
            round(count / entered * 100, 1) if entered > 0 else 0.0
        )

        funnel_stages.append({
            "stage": name,
            "count": count,
            "drop_off_pct": drop_off_pct,
            "conversion_from_entry_pct": conversion_from_entry,
        })

    return {
        "store_id": store_id,
        "stages": funnel_stages,
        "overall_conversion_pct": (
            round(purchased / entered * 100, 1) if entered > 0 else 0.0
        ),
        "total_entered": entered,
        "total_purchased": purchased,
    }
