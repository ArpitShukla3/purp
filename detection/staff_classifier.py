"""
Heuristic staff classifier.

Identifies likely staff members based on track behavior patterns.
This is a post-processing pass that runs after the full video has been
processed, using complete track histories.

Heuristic rules (configurable):
  1. **Duration**: Staff are present for a large fraction of the video
  2. **Stationarity**: Staff tend to stay in one area (low movement range)
  3. **Zone affinity**: Staff tend to stay behind counters / in one zone

Limitations:
  - Cannot distinguish staff who actively walk the floor from customers
  - Relies on sustained presence as the primary signal
  - Short clips may not have enough data for accurate classification
  - No appearance-based features (no uniform detection, no face recognition)
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from detection.tracker import TrackState

logger = structlog.get_logger("staff_classifier")


@dataclass
class StaffClassification:
    """Result of staff classification for one track."""

    track_id: int
    is_staff: bool
    confidence: float
    reason: str
    duration_pct: float      # fraction of video this track was present
    movement_range: float    # max x-range of movement in pixels


class StaffClassifier:
    """
    Heuristic-based staff detector.

    Call ``classify()`` after video processing is complete, passing all
    track states.  Returns a classification for each track.
    """

    def __init__(
        self,
        min_duration_pct: float = 0.5,
        max_movement_range: float = 400.0,
        min_observations: int = 30,
    ) -> None:
        """
        Args:
            min_duration_pct: Minimum fraction of total processed frames
                the track must span to be considered staff (0.0–1.0).
                Default 0.5 = present for at least 50% of the video.
            max_movement_range: Maximum x-range (in pixels) for a track
                to be considered "stationary enough" for staff.
                Staff behind counters typically move < 400px.
            min_observations: Minimum number of detections needed to
                classify (avoids false positives on very short tracks).
        """
        self.min_duration_pct = min_duration_pct
        self.max_movement_range = max_movement_range
        self.min_observations = min_observations

    def classify(
        self,
        tracks: dict[int, TrackState],
        total_processed_frames: int,
    ) -> dict[int, StaffClassification]:
        """
        Classify all tracks as staff or customer.

        Args:
            tracks: All track states from the video
            total_processed_frames: Total number of frames that were processed

        Returns:
            Dict mapping track_id → StaffClassification
        """
        results: dict[int, StaffClassification] = {}

        for tid, track in tracks.items():
            n = len(track.positions)

            # Not enough data to classify
            if n < self.min_observations:
                results[tid] = StaffClassification(
                    track_id=tid,
                    is_staff=False,
                    confidence=0.0,
                    reason="insufficient_observations",
                    duration_pct=0.0,
                    movement_range=0.0,
                )
                continue

            # Calculate duration as fraction of video
            duration_frames = track.last_seen - track.first_seen
            duration_pct = (
                duration_frames / total_processed_frames
                if total_processed_frames > 0
                else 0.0
            )

            # Calculate x-range of movement
            xs = [p[0] for p in track.positions]
            x_range = max(xs) - min(xs)

            # Apply heuristic rules
            is_long = duration_pct >= self.min_duration_pct
            is_stationary = x_range <= self.max_movement_range

            is_staff = is_long and is_stationary
            confidence = 0.0
            reason = "customer"

            if is_staff:
                # Confidence based on how clearly staff-like the behavior is
                dur_score = min(duration_pct / self.min_duration_pct, 2.0) / 2.0
                stat_score = max(0, 1.0 - x_range / self.max_movement_range)
                confidence = (dur_score + stat_score) / 2.0
                reason = "long_duration_stationary"
            elif is_long:
                reason = "long_duration_but_mobile"
            elif is_stationary:
                reason = "stationary_but_short"

            results[tid] = StaffClassification(
                track_id=tid,
                is_staff=is_staff,
                confidence=round(confidence, 3),
                reason=reason,
                duration_pct=round(duration_pct, 3),
                movement_range=round(x_range, 1),
            )

            if is_staff:
                logger.info(
                    "staff_detected",
                    track_id=tid,
                    duration_pct=round(duration_pct, 3),
                    movement_range=round(x_range, 1),
                    confidence=round(confidence, 3),
                )

        return results
