"""
Anomaly detection engine.

Flags operational anomalies using deterministic, rule-based heuristics.
Every anomaly includes the rule, observed value, and threshold.

Types: queue_surge, conversion_drop, dead_zone, high_dwell.
"""

from __future__ import annotations
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.event import Event
from shared.models.pos import POSTransaction

logger = structlog.get_logger("analytics.anomalies")


async def detect_anomalies(
    session: AsyncSession,
    store_id: str,
    queue_surge_threshold: int = 5,
    conversion_drop_pct: float = 50.0,
    dead_zone_minutes: int = 30,
    high_dwell_multiplier: float = 2.0,
) -> dict[str, Any]:
    """Detect anomalies in store operations."""
    anomalies: list[dict[str, Any]] = []

    # 1. Queue surge
    q_queue = (
        select(Event.metadata_json["queue_depth"], Event.timestamp)
        .where(Event.store_id == store_id,
               Event.event_type.in_(["BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"]))
        .order_by(Event.timestamp.desc()).limit(1)
    )
    qr = (await session.execute(q_queue)).one_or_none()
    if qr and qr[0] is not None:
        try:
            depth = int(qr[0])
            if depth >= queue_surge_threshold:
                anomalies.append({"type": "queue_surge", "severity": "high" if depth >= queue_surge_threshold * 2 else "medium",
                    "description": f"Queue depth ({depth}) exceeds threshold ({queue_surge_threshold}).",
                    "observed": depth, "threshold": queue_surge_threshold,
                    "timestamp": qr[1].isoformat() if qr[1] else None,
                    "rule": "queue_depth >= queue_surge_threshold"})
        except (TypeError, ValueError):
            pass

    # 2. Conversion drop
    total_entries = (await session.execute(
        select(func.count(distinct(Event.visitor_id)))
        .where(Event.store_id == store_id, Event.event_type == "ENTRY", Event.is_staff == False)
    )).scalar_one()

    total_purchases = (await session.execute(
        select(func.count(distinct(POSTransaction.matched_visitor_id)))
        .where(POSTransaction.store_id == store_id, POSTransaction.matched_visitor_id.isnot(None))
    )).scalar_one()

    if total_entries > 0:
        overall_conv = total_purchases / total_entries * 100
        recent_result = await session.execute(
            select(Event.visitor_id)
            .where(Event.store_id == store_id, Event.event_type == "ENTRY", Event.is_staff == False)
            .order_by(Event.timestamp.desc()).limit(max(1, total_entries // 4))
        )
        recent_visitors = {r.visitor_id for r in recent_result.all()}
        if recent_visitors:
            recent_purchases = (await session.execute(
                select(func.count(distinct(POSTransaction.matched_visitor_id)))
                .where(POSTransaction.store_id == store_id, POSTransaction.matched_visitor_id.in_(list(recent_visitors)))
            )).scalar_one()
            recent_conv = recent_purchases / len(recent_visitors) * 100
            if overall_conv > 0 and (overall_conv - recent_conv) / overall_conv * 100 > conversion_drop_pct:
                anomalies.append({"type": "conversion_drop", "severity": "high",
                    "description": f"Recent conversion ({recent_conv:.1f}%) below baseline ({overall_conv:.1f}%).",
                    "observed": round(recent_conv, 1), "baseline": round(overall_conv, 1),
                    "threshold_drop_pct": conversion_drop_pct,
                    "rule": "(baseline - recent) / baseline * 100 > threshold"})

    # 3. Dead zones
    zone_result = await session.execute(
        select(Event.zone_id, func.max(Event.timestamp).label("last_seen"))
        .where(Event.store_id == store_id, Event.zone_id.isnot(None), Event.is_staff == False)
        .group_by(Event.zone_id)
    )
    latest_ts = (await session.execute(
        select(func.max(Event.timestamp)).where(Event.store_id == store_id)
    )).scalar_one_or_none()

    if latest_ts:
        dead_threshold = latest_ts - timedelta(minutes=dead_zone_minutes)
        for row in zone_result.all():
            zone_id, last_seen = row
            if zone_id == "outside":
                continue
            if last_seen < dead_threshold:
                gap = (latest_ts - last_seen).total_seconds() / 60
                anomalies.append({"type": "dead_zone", "severity": "medium",
                    "description": f"Zone '{zone_id}' no activity for {gap:.0f}min (threshold: {dead_zone_minutes}min).",
                    "zone_id": zone_id, "last_activity": last_seen.isoformat(),
                    "gap_minutes": round(gap, 1), "threshold_minutes": dead_zone_minutes,
                    "rule": "gap_since_last_visit > dead_zone_minutes"})

    # 4. High dwell
    dwell_rows = (await session.execute(
        select(Event.zone_id, func.avg(Event.dwell_ms).label("avg_dwell"))
        .where(Event.store_id == store_id, Event.dwell_ms.isnot(None),
               Event.zone_id.isnot(None), Event.is_staff == False)
        .group_by(Event.zone_id)
    )).all()

    if len(dwell_rows) > 1:
        all_dwells = [float(r.avg_dwell) for r in dwell_rows]
        global_avg = sum(all_dwells) / len(all_dwells)
        for row in dwell_rows:
            zone_avg = float(row.avg_dwell)
            if zone_avg > global_avg * high_dwell_multiplier:
                anomalies.append({"type": "high_dwell", "severity": "low",
                    "description": f"Zone '{row.zone_id}' dwell ({zone_avg:.0f}ms) is {zone_avg/global_avg:.1f}x global avg.",
                    "zone_id": row.zone_id, "observed_ms": round(zone_avg),
                    "global_avg_ms": round(global_avg), "multiplier": round(zone_avg / global_avg, 2),
                    "threshold_multiplier": high_dwell_multiplier,
                    "rule": "zone_avg_dwell > global_avg * multiplier"})

    return {"store_id": store_id, "anomaly_count": len(anomalies), "anomalies": anomalies,
            "thresholds": {"queue_surge": queue_surge_threshold, "conversion_drop_pct": conversion_drop_pct,
                           "dead_zone_minutes": dead_zone_minutes, "high_dwell_multiplier": high_dwell_multiplier}}
