"""
Per-visitor state manager.

Maintains the lifecycle of each tracked person across frames, handling:
  - Mapping ByteTrack track IDs to stable visitor IDs
  - Zone enter/exit transitions with point-in-polygon testing
  - Dwell time accumulation and ZONE_DWELL event emission
  - Re-entry detection (same person re-entering within a time window)
  - Session sequence numbering for event ordering
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import structlog

from detection.tracker import TrackState
from detection.zones import ZoneConfig, get_all_zones_for_point
from shared.schemas.events import (
    BoundingBox,
    EventMetadata,
    EventType,
    StoreEvent,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger("visitor_state")


@dataclass
class VisitorState:
    """Lifecycle state for one visitor (may span multiple ByteTrack IDs)."""

    visitor_id: str
    track_ids: list[int] = field(default_factory=list)

    # Zone state
    current_zones: set[str] = field(default_factory=set)
    zone_enter_frames: dict[str, int] = field(default_factory=dict)   # zone_id → frame entered
    zone_dwell_emitted: set[str] = field(default_factory=set)         # zones that already got ZONE_DWELL

    # Staff flag (set by post-processing)
    is_staff: bool = False

    # Session event sequence counter
    session_seq: int = 0

    # Entry/exit timestamps (for re-entry matching)
    entry_frame: int | None = None
    exit_frame: int | None = None

    # Average confidence from detections
    avg_confidence: float = 0.0

    def next_seq(self) -> int:
        """Increment and return the next session sequence number."""
        self.session_seq += 1
        return self.session_seq


class VisitorStateManager:
    """
    Manages all visitor states and emits zone / dwell / re-entry events.

    Called once per processed frame by the pipeline. For each frame it:
      1. Maps active tracks to visitors (handling re-entry)
      2. Checks zone transitions for each visitor
      3. Checks dwell thresholds
      4. Returns any events generated
    """

    def __init__(
        self,
        zones: list[ZoneConfig],
        store_id: str = "purplle-001",
        camera_id: str = "cam3",
        reentry_window_s: float = 120.0,
        dwell_check_interval: int = 10,  # check dwell every N frames
    ) -> None:
        self.zones = zones
        self.store_id = store_id
        self.camera_id = camera_id
        self.reentry_window_s = reentry_window_s
        self.dwell_check_interval = dwell_check_interval

        # Track ID → visitor ID mapping
        self._track_to_visitor: dict[int, str] = {}
        # Visitor ID → VisitorState
        self._visitors: dict[str, VisitorState] = {}
        # Recently exited visitors for re-entry matching: list of (exit_frame, visitor_id, last_position)
        self._recent_exits: list[tuple[int, str, tuple[float, float]]] = []
        # Next visitor counter
        self._next_visitor_num: int = 0
        # Frame counter for dwell checks
        self._frame_counter: int = 0

    def update(
        self,
        active_tracks: list[TrackState],
        frame_num: int,
        fps: float,
        video_start_time: datetime | None = None,
    ) -> list[StoreEvent]:
        """
        Process one frame's tracked detections and return any new events.

        Args:
            active_tracks: TrackStates updated in this frame
            frame_num: Current frame number
            fps: Video frame rate
            video_start_time: Absolute start time of the video

        Returns:
            List of events emitted this frame
        """
        self._frame_counter += 1
        events: list[StoreEvent] = []

        for track in active_tracks:
            if track.track_id is None:
                continue
            if not track.positions:
                continue

            # 1. Resolve track → visitor
            visitor = self._resolve_visitor(track, frame_num, fps, video_start_time)
            if visitor is None:
                continue

            # 2. Check zone transitions
            cx, cy = track.positions[-1]
            zone_events = self._check_zone_transitions(
                visitor, cx, cy, track, frame_num, fps, video_start_time
            )
            events.extend(zone_events)

        # 3. Check dwell thresholds periodically (not every frame)
        if self._frame_counter % self.dwell_check_interval == 0:
            dwell_events = self._check_all_dwells(frame_num, fps, video_start_time)
            events.extend(dwell_events)

        return events

    def register_entry(
        self, track_id: int, frame_num: int
    ) -> str:
        """
        Register a store ENTRY for a track. Returns the visitor ID.

        Called by the crossing detector when an ENTRY event is generated.
        Handles re-entry matching automatically.
        """
        # Check if this track already has a visitor
        if track_id in self._track_to_visitor:
            vid = self._track_to_visitor[track_id]
            visitor = self._visitors[vid]
            visitor.entry_frame = frame_num
            return vid

        # Check for re-entry match
        reentry_vid = self._find_reentry_match(frame_num)
        if reentry_vid is not None:
            self._track_to_visitor[track_id] = reentry_vid
            visitor = self._visitors[reentry_vid]
            visitor.track_ids.append(track_id)
            visitor.entry_frame = frame_num
            return reentry_vid

        # New visitor
        return self._create_visitor(track_id, frame_num)

    def register_exit(
        self, track_id: int, frame_num: int, position: tuple[float, float] | None = None
    ) -> str | None:
        """
        Register a store EXIT for a track. Returns the visitor ID.

        Records the exit for re-entry matching.
        """
        vid = self._track_to_visitor.get(track_id)
        if vid is None:
            return None

        visitor = self._visitors[vid]
        visitor.exit_frame = frame_num

        # Record for re-entry matching
        pos = position or (0.0, 0.0)
        self._recent_exits.append((frame_num, vid, pos))

        return vid

    def get_visitor_id(self, track_id: int) -> str | None:
        """Get the visitor ID for a track, or None if not registered."""
        return self._track_to_visitor.get(track_id)

    def get_visitor(self, visitor_id: str) -> VisitorState | None:
        """Get visitor state by ID."""
        return self._visitors.get(visitor_id)

    def get_all_visitors(self) -> list[VisitorState]:
        """Return all visitors."""
        return list(self._visitors.values())

    # ── Internal Methods ─────────────────────────────────────────────

    def _resolve_visitor(
        self,
        track: TrackState,
        frame_num: int,
        fps: float,
        video_start_time: datetime | None,
    ) -> VisitorState | None:
        """Map a track to its visitor, creating one if needed."""
        tid = track.track_id
        if tid in self._track_to_visitor:
            return self._visitors[self._track_to_visitor[tid]]

        # Track not yet assigned — create a new visitor
        # (ENTRY/EXIT registration is separate via register_entry/register_exit)
        vid = self._create_visitor(tid, frame_num)
        return self._visitors[vid]

    def _create_visitor(self, track_id: int, frame_num: int) -> str:
        """Create a new visitor and assign the track to it."""
        self._next_visitor_num += 1
        vid = f"visitor-{self._next_visitor_num}"
        visitor = VisitorState(
            visitor_id=vid,
            track_ids=[track_id],
            entry_frame=frame_num,
        )
        self._visitors[vid] = visitor
        self._track_to_visitor[track_id] = vid
        return vid

    def _find_reentry_match(self, current_frame: int) -> str | None:
        """
        Check if a new entry matches a recent exit (re-entry).

        Heuristic: if an exit happened within ``reentry_window_s`` seconds,
        reuse that visitor ID. Without face recognition, we use temporal
        proximity as the main signal.

        Limitation: If multiple people exit and one re-enters, we match
        the most recent exit. This can produce false matches in high-traffic
        scenarios.
        """
        if not self._recent_exits:
            return None

        # Clean up old exits (beyond the re-entry window)
        # Assume ~30fps if unknown; the window is generous enough
        max_frame_gap = int(self.reentry_window_s * 30)
        self._recent_exits = [
            (f, vid, pos)
            for f, vid, pos in self._recent_exits
            if current_frame - f <= max_frame_gap
        ]

        if not self._recent_exits:
            return None

        # Match the most recent exit
        exit_frame, vid, pos = self._recent_exits.pop()
        logger.info(
            "reentry_matched",
            visitor_id=vid,
            exit_frame=exit_frame,
            reentry_frame=current_frame,
            gap_frames=current_frame - exit_frame,
        )
        return vid

    def _check_zone_transitions(
        self,
        visitor: VisitorState,
        cx: float,
        cy: float,
        track: TrackState,
        frame_num: int,
        fps: float,
        video_start_time: datetime | None,
    ) -> list[StoreEvent]:
        """Check if a visitor has entered or exited any zones."""
        events: list[StoreEvent] = []

        # Find all zones the visitor is currently in
        current_zone_ids = set()
        for zone in self.zones:
            from detection.zones import point_in_polygon

            if point_in_polygon(cx, cy, zone.polygon):
                current_zone_ids.add(zone.zone_id)

        prev_zones = visitor.current_zones

        # Zone enters
        entered = current_zone_ids - prev_zones
        for zid in entered:
            zone = self._get_zone(zid)
            if zone is None or zone.is_outside:
                continue  # don't emit events for the "outside" zone

            visitor.zone_enter_frames[zid] = frame_num
            ts = self._frame_to_timestamp(frame_num, fps, video_start_time)

            events.append(
                StoreEvent(
                    store_id=self.store_id,
                    camera_id=self.camera_id,
                    visitor_id=visitor.visitor_id,
                    event_type=EventType.ZONE_ENTER,
                    confidence=track.avg_confidence,
                    zone_id=zid,
                    is_staff=visitor.is_staff,
                    session_seq=visitor.next_seq(),
                    timestamp=ts,
                    metadata=EventMetadata(
                        zone_name=zone.name,
                        frame_number=frame_num,
                        bbox=self._track_bbox(track),
                    ),
                )
            )

            logger.debug("zone_enter", visitor=visitor.visitor_id, zone=zid, frame=frame_num)

        # Zone exits
        exited = prev_zones - current_zone_ids
        for zid in exited:
            zone = self._get_zone(zid)
            if zone is None or zone.is_outside:
                continue

            enter_frame = visitor.zone_enter_frames.pop(zid, frame_num)
            dwell_frames = frame_num - enter_frame
            dwell_ms = int(dwell_frames / fps * 1000) if fps > 0 else 0
            ts = self._frame_to_timestamp(frame_num, fps, video_start_time)

            events.append(
                StoreEvent(
                    store_id=self.store_id,
                    camera_id=self.camera_id,
                    visitor_id=visitor.visitor_id,
                    event_type=EventType.ZONE_EXIT,
                    confidence=track.avg_confidence,
                    zone_id=zid,
                    dwell_ms=dwell_ms,
                    is_staff=visitor.is_staff,
                    session_seq=visitor.next_seq(),
                    timestamp=ts,
                    metadata=EventMetadata(
                        zone_name=zone.name,
                        frame_number=frame_num,
                        bbox=self._track_bbox(track),
                    ),
                )
            )

            # Remove dwell-emitted flag since visitor left
            visitor.zone_dwell_emitted.discard(zid)

            logger.debug("zone_exit", visitor=visitor.visitor_id, zone=zid, dwell_ms=dwell_ms)

        # Update visitor's current zones
        visitor.current_zones = current_zone_ids
        visitor.avg_confidence = track.avg_confidence

        return events

    def _check_all_dwells(
        self,
        frame_num: int,
        fps: float,
        video_start_time: datetime | None,
    ) -> list[StoreEvent]:
        """Check all visitors for dwell threshold crossings."""
        events: list[StoreEvent] = []

        for visitor in self._visitors.values():
            for zid in visitor.current_zones:
                # Skip if already emitted dwell for this zone visit
                if zid in visitor.zone_dwell_emitted:
                    continue

                zone = self._get_zone(zid)
                if zone is None or zone.is_outside:
                    continue
                if zone.dwell_threshold_ms <= 0:
                    continue

                enter_frame = visitor.zone_enter_frames.get(zid)
                if enter_frame is None:
                    continue

                dwell_frames = frame_num - enter_frame
                dwell_ms = int(dwell_frames / fps * 1000) if fps > 0 else 0

                if dwell_ms >= zone.dwell_threshold_ms:
                    ts = self._frame_to_timestamp(frame_num, fps, video_start_time)

                    events.append(
                        StoreEvent(
                            store_id=self.store_id,
                            camera_id=self.camera_id,
                            visitor_id=visitor.visitor_id,
                            event_type=EventType.ZONE_DWELL,
                            confidence=visitor.avg_confidence,
                            zone_id=zid,
                            dwell_ms=dwell_ms,
                            is_staff=visitor.is_staff,
                            session_seq=visitor.next_seq(),
                            timestamp=ts,
                            metadata=EventMetadata(
                                zone_name=zone.name,
                                frame_number=frame_num,
                            ),
                        )
                    )

                    visitor.zone_dwell_emitted.add(zid)
                    logger.info(
                        "zone_dwell",
                        visitor=visitor.visitor_id,
                        zone=zid,
                        dwell_ms=dwell_ms,
                    )

        return events

    def _get_zone(self, zone_id: str) -> ZoneConfig | None:
        """Look up a zone by ID."""
        for z in self.zones:
            if z.zone_id == zone_id:
                return z
        return None

    @staticmethod
    def _frame_to_timestamp(
        frame_num: int, fps: float, video_start_time: datetime | None
    ) -> datetime:
        """Convert frame number to absolute timestamp."""
        if video_start_time is not None and fps > 0:
            offset = frame_num / fps
            ts = video_start_time + timedelta(seconds=offset)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        return datetime.now(timezone.utc)

    @staticmethod
    def _track_bbox(track: TrackState) -> BoundingBox | None:
        """Get BoundingBox from the latest track detection."""
        if track.last_bbox is not None:
            return BoundingBox(
                x1=track.last_bbox[0],
                y1=track.last_bbox[1],
                x2=track.last_bbox[2],
                y2=track.last_bbox[3],
            )
        return None
