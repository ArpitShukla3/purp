"""
Threshold crossing detector — emits ENTRY/EXIT events based on track
appearance and movement patterns near the store entrance.

Strategy:
  The glass door in CAM 3 creates a tracking dead-zone where ByteTrack
  loses track IDs.  Instead of requiring a single track to cross the
  threshold, we use a zone-based approach:

  - **ENTRY zone**: x in [zone_inner, zone_outer] — the area near the door
    on the inside of the store.
  - **ENTRY**: Track first appears in the entry zone and moves deeper inside
    (leftward, toward lower x values), suggesting the person just walked in.
  - **EXIT**: Track is in the entry zone and was previously deeper inside,
    or a track disappears near the entry zone moving outward.

  Additionally, we look at tracks that appear/disappear right near the
  glass boundary as supporting evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from detection.tracker import TrackState
from shared.schemas.events import (
    BoundingBox,
    EventMetadata,
    EventType,
    StoreEvent,
)


class CrossingDetector:
    """
    Detects ENTRY/EXIT events using zone-based track analysis.

    The entrance zone is defined by ``zone_inner`` (deep-store side) and
    ``zone_outer`` (door side).  Tracks appearing in or passing through
    this zone trigger events based on their movement direction.
    """

    def __init__(
        self,
        threshold_x: float = 900.0,
        zone_inner: float = 350.0,
        zone_outer: float = 850.0,
        store_id: str = "purplle-001",
        camera_id: str = "cam3",
        min_observations: int = 3,
    ) -> None:
        self.threshold_x = threshold_x
        self.zone_inner = zone_inner
        self.zone_outer = zone_outer
        self.store_id = store_id
        self.camera_id = camera_id
        self.min_observations = min_observations

        # Track IDs that have already triggered an event
        self._emitted: set[int] = set()
        # Track IDs classified but waiting for confirmation
        self._pending_entries: dict[int, TrackState] = {}

    def check_crossings(
        self,
        tracks: list[TrackState],
        video_start_time: datetime | None = None,
        fps: float = 30.0,
    ) -> list[StoreEvent]:
        """
        Check all tracks for entry/exit patterns.

        Returns a list of newly generated ENTRY/EXIT events.
        """
        events: list[StoreEvent] = []

        for track in tracks:
            if track.track_id in self._emitted:
                continue
            if len(track.positions) < self.min_observations:
                continue

            event = self._classify_track(track, video_start_time, fps)
            if event is not None:
                events.append(event)
                self._emitted.add(track.track_id)

        return events

    def _classify_track(
        self,
        track: TrackState,
        video_start_time: datetime | None,
        fps: float,
    ) -> StoreEvent | None:
        """
        Classify a track as ENTRY, EXIT, or neither based on movement.

        Logic:
        - Get the track's first and most recent x positions.
        - If the track started near the door (entry zone) and moved deeper
          inside, it's an ENTRY.
        - If the track started deep inside and moved toward the door zone,
          it's an EXIT.
        - Tracks entirely inside or outside with no significant movement
          toward/away from the door are ignored.
        """
        xs = [p[0] for p in track.positions]
        first_x = xs[0]
        last_x = xs[-1]
        min_x = min(xs)
        max_x = max(xs)
        n = len(xs)

        # --- INSIDE tracks (max_x < threshold) ---
        if max_x < self.threshold_x:
            # Track appeared near the door zone → ENTRY
            if first_x > self.zone_inner:
                # Moved inward (toward lower x) → strong ENTRY signal
                if last_x < first_x - 15 or min_x < first_x - 20:
                    return self._make_event(
                        track, EventType.ENTRY, "appeared_near_door_moved_inside",
                        video_start_time, fps,
                    )
                # Lingered near the door (browsing near entrance)
                if n >= 5:
                    return self._make_event(
                        track, EventType.ENTRY, "appeared_in_entry_zone",
                        video_start_time, fps,
                    )

            # Track appeared deep inside and moved toward the door → EXIT
            if first_x < self.zone_inner and last_x > first_x + 40:
                return self._make_event(
                    track, EventType.EXIT, "moved_toward_door_from_inside",
                    video_start_time, fps,
                )

        # --- OUTSIDE tracks (min_x >= threshold) ---
        if min_x >= self.threshold_x:
            # Brief track near the door → person passing through the entrance
            # (entering or exiting, we classify by direction)
            near_door = min_x < self.threshold_x + 600  # within 600px of door

            if near_door:
                # Track moving AWAY from door (increasing x) → EXIT
                if last_x > first_x + 30 and n >= 3:
                    return self._make_event(
                        track, EventType.EXIT, "outside_moving_away",
                        video_start_time, fps,
                    )
                # Track moving TOWARD door (decreasing x) → ENTRY
                if first_x > last_x + 30 and n >= 3:
                    return self._make_event(
                        track, EventType.ENTRY, "outside_moving_toward_door",
                        video_start_time, fps,
                    )
                # Brief appearance near door → likely someone passing through
                # Classify by position: closer to door = more likely ENTRY
                if n >= 3 and min_x < self.threshold_x + 350:
                    return self._make_event(
                        track, EventType.ENTRY, "brief_outside_near_door",
                        video_start_time, fps,
                    )

        return None

    def _make_event(
        self,
        track: TrackState,
        event_type: EventType,
        direction: str,
        video_start_time: datetime | None,
        fps: float,
    ) -> StoreEvent:
        """Build a StoreEvent from a track and classification."""
        # Compute timestamp from the track's first frame
        if video_start_time is not None and fps > 0:
            frame_offset = track.first_seen / fps
            timestamp = video_start_time + timedelta(seconds=frame_offset)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        bbox = None
        if track.last_bbox is not None:
            bbox = BoundingBox(
                x1=track.last_bbox[0],
                y1=track.last_bbox[1],
                x2=track.last_bbox[2],
                y2=track.last_bbox[3],
            )

        metadata = EventMetadata(
            bbox=bbox,
            crossing_x=track.positions[0][0],
            threshold_x=self.threshold_x,
            frame_number=track.first_seen,
            direction=direction,
        )

        return StoreEvent(
            store_id=self.store_id,
            camera_id=self.camera_id,
            visitor_id=f"track-{track.track_id}",
            event_type=event_type,
            confidence=track.avg_confidence,
            metadata=metadata,
            timestamp=timestamp,
        )

    def reset(self) -> None:
        """Reset state for processing a new video."""
        self._emitted.clear()
        self._pending_entries.clear()

    @property
    def total_crossings(self) -> int:
        """Total number of crossings detected so far."""
        return len(self._emitted)
