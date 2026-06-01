"""
Billing queue tracker.

Monitors the checkout zone and emits BILLING_QUEUE_JOIN and
BILLING_QUEUE_ABANDON events based on visitor movement patterns.

Limitations:
  - CAM 3 does not directly cover the checkout counter; the checkout
    zone polygon is approximate.
  - Full accuracy requires CAM 5 integration (future work).
  - Queue depth is a point-in-time count of visitors in the checkout
    zone, not a true FIFO queue model.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import structlog

from detection.visitor_state import VisitorState
from detection.zones import ZoneConfig
from shared.schemas.events import (
    BoundingBox,
    EventMetadata,
    EventType,
    StoreEvent,
)

logger = structlog.get_logger("queue_tracker")


class QueueTracker:
    """
    Tracks billing queue state and emits queue-related events.

    The queue is defined by a checkout zone. When a visitor enters
    the checkout zone, a ``BILLING_QUEUE_JOIN`` event is emitted.
    If they leave without staying long enough (below ``min_queue_time_s``),
    a ``BILLING_QUEUE_ABANDON`` event is emitted.
    """

    def __init__(
        self,
        checkout_zone: ZoneConfig | None = None,
        min_queue_time_s: float = 10.0,
        store_id: str = "purplle-001",
        camera_id: str = "cam3",
    ) -> None:
        """
        Args:
            checkout_zone: The ZoneConfig for the checkout area.
                           If None, queue tracking is disabled.
            min_queue_time_s: Minimum time (seconds) a visitor must stay
                             in the checkout zone for it to count as a
                             completed transaction. If they leave earlier,
                             it's an ABANDON.
        """
        self.checkout_zone = checkout_zone
        self.min_queue_time_s = min_queue_time_s
        self.store_id = store_id
        self.camera_id = camera_id

        # Visitors currently in the queue
        self._in_queue: dict[str, int] = {}  # visitor_id → entry frame
        # Visitors who have already had a JOIN event emitted
        self._join_emitted: set[str] = set()

    @property
    def current_depth(self) -> int:
        """Current number of visitors in the checkout zone."""
        return len(self._in_queue)

    @property
    def is_enabled(self) -> bool:
        """Whether queue tracking is active."""
        return self.checkout_zone is not None

    def on_zone_enter(
        self,
        visitor: VisitorState,
        zone_id: str,
        frame_num: int,
        fps: float,
        video_start_time: datetime | None = None,
    ) -> StoreEvent | None:
        """
        Called when a visitor enters a zone.

        If the zone is the checkout zone, emit BILLING_QUEUE_JOIN.
        """
        if not self.is_enabled:
            return None
        if zone_id != self.checkout_zone.zone_id:
            return None
        if visitor.visitor_id in self._join_emitted:
            return None  # already emitted join for this visitor

        self._in_queue[visitor.visitor_id] = frame_num
        self._join_emitted.add(visitor.visitor_id)

        ts = self._frame_to_ts(frame_num, fps, video_start_time)

        logger.info(
            "queue_join",
            visitor=visitor.visitor_id,
            queue_depth=self.current_depth,
        )

        return StoreEvent(
            store_id=self.store_id,
            camera_id=self.camera_id,
            visitor_id=visitor.visitor_id,
            event_type=EventType.BILLING_QUEUE_JOIN,
            confidence=visitor.avg_confidence,
            zone_id=zone_id,
            is_staff=visitor.is_staff,
            session_seq=visitor.next_seq(),
            timestamp=ts,
            metadata=EventMetadata(
                zone_name=self.checkout_zone.name,
                queue_depth=self.current_depth,
                frame_number=frame_num,
            ),
        )

    def on_zone_exit(
        self,
        visitor: VisitorState,
        zone_id: str,
        frame_num: int,
        fps: float,
        video_start_time: datetime | None = None,
    ) -> StoreEvent | None:
        """
        Called when a visitor exits a zone.

        If the zone is the checkout zone and the visitor didn't stay
        long enough, emit BILLING_QUEUE_ABANDON.
        """
        if not self.is_enabled:
            return None
        if zone_id != self.checkout_zone.zone_id:
            return None
        if visitor.visitor_id not in self._in_queue:
            return None

        enter_frame = self._in_queue.pop(visitor.visitor_id)
        dwell_frames = frame_num - enter_frame
        dwell_s = dwell_frames / fps if fps > 0 else 0

        # If they didn't stay long enough, it's an abandon
        if dwell_s < self.min_queue_time_s:
            ts = self._frame_to_ts(frame_num, fps, video_start_time)

            logger.info(
                "queue_abandon",
                visitor=visitor.visitor_id,
                dwell_s=round(dwell_s, 1),
                queue_depth=self.current_depth,
            )

            return StoreEvent(
                store_id=self.store_id,
                camera_id=self.camera_id,
                visitor_id=visitor.visitor_id,
                event_type=EventType.BILLING_QUEUE_ABANDON,
                confidence=visitor.avg_confidence,
                zone_id=zone_id,
                dwell_ms=int(dwell_s * 1000),
                is_staff=visitor.is_staff,
                session_seq=visitor.next_seq(),
                timestamp=ts,
                metadata=EventMetadata(
                    zone_name=self.checkout_zone.name,
                    queue_depth=self.current_depth,
                    frame_number=frame_num,
                ),
            )

        return None

    @staticmethod
    def _frame_to_ts(
        frame_num: int, fps: float, video_start_time: datetime | None
    ) -> datetime:
        """Convert frame number to timestamp."""
        if video_start_time is not None and fps > 0:
            ts = video_start_time + timedelta(seconds=frame_num / fps)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        return datetime.now(timezone.utc)
