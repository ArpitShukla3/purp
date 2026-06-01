"""
Track state manager for the detection pipeline.

Maintains per-track position history so the crossing detector can determine
when a person has moved across the threshold line.  The actual tracking
algorithm (ByteTrack) runs inside ``PersonDetector.track()``; this module
manages the *state* on top of those raw results.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from detection.detector import Detection


@dataclass
class TrackState:
    """Position history and metadata for one tracked person."""

    track_id: int
    positions: list[tuple[float, float]] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    bboxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    frame_numbers: list[int] = field(default_factory=list)
    first_seen: int = 0
    last_seen: int = 0

    @property
    def last_x(self) -> float | None:
        """Most recent center-x position."""
        return self.positions[-1][0] if self.positions else None

    @property
    def prev_x(self) -> float | None:
        """Previous center-x position (for crossing detection)."""
        return self.positions[-2][0] if len(self.positions) >= 2 else None

    @property
    def last_confidence(self) -> float:
        """Most recent detection confidence."""
        return self.confidences[-1] if self.confidences else 0.0

    @property
    def last_bbox(self) -> tuple[float, float, float, float] | None:
        """Most recent bounding box."""
        return self.bboxes[-1] if self.bboxes else None

    @property
    def last_frame(self) -> int:
        """Most recent frame number."""
        return self.frame_numbers[-1] if self.frame_numbers else 0

    @property
    def avg_confidence(self) -> float:
        """Average confidence across all detections of this track."""
        return sum(self.confidences) / len(self.confidences) if self.confidences else 0.0


class TrackManager:
    """
    Manages track state and position history.

    For each frame, call ``update()`` with the detections returned by
    ``PersonDetector.track()``.  The manager records each track's centroid
    history so the ``CrossingDetector`` can determine direction of movement.
    """

    def __init__(self, max_history: int = 60) -> None:
        self.tracks: dict[int, TrackState] = {}
        self.max_history = max_history

    def update(
        self, detections: list[Detection], frame_number: int
    ) -> list[TrackState]:
        """
        Update track states with new detections from the current frame.

        Returns the list of *active* TrackState objects that were updated
        in this frame (i.e., tracks that have a detection in the current frame).
        """
        updated: list[TrackState] = []

        for det in detections:
            if det.track_id is None:
                continue

            tid = det.track_id

            if tid not in self.tracks:
                self.tracks[tid] = TrackState(
                    track_id=tid,
                    first_seen=frame_number,
                )

            state = self.tracks[tid]
            state.positions.append((det.center_x, det.center_y))
            state.confidences.append(det.confidence)
            state.bboxes.append(det.bbox)
            state.frame_numbers.append(frame_number)
            state.last_seen = frame_number

            # Trim history to prevent unbounded memory growth
            if len(state.positions) > self.max_history:
                state.positions = state.positions[-self.max_history :]
                state.confidences = state.confidences[-self.max_history :]
                state.bboxes = state.bboxes[-self.max_history :]
                state.frame_numbers = state.frame_numbers[-self.max_history :]

            updated.append(state)

        return updated

    def get_all_tracks(self) -> list[TrackState]:
        """Return all tracks, including inactive ones."""
        return list(self.tracks.values())

    def get_active_track_count(self) -> int:
        """Return the number of tracks that have ever been seen."""
        return len(self.tracks)
