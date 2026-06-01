"""
Detection pipeline orchestrator.

Connects video input → YOLOv8 detector → ByteTrack tracker → crossing
detector → visitor state manager → queue tracker → JSONL event output.

This is the main processing engine.  The flow per frame is:

  1. Read & sample frame
  2. Run YOLOv8 person detection + ByteTrack tracking
  3. Update track state manager (position history)
  4. Run crossing detector (ENTRY/EXIT from store entrance)
  5. Run visitor state manager (zone transitions, dwell, re-entry)
  6. Run queue tracker (checkout zone join/abandon)
  7. Emit all events to JSONL
  8. Post-process: staff classification (after video ends)
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import structlog

from detection.crossing import CrossingDetector
from detection.detector import PersonDetector
from detection.queue_tracker import QueueTracker
from detection.staff_classifier import StaffClassifier
from detection.tracker import TrackManager
from detection.visitor_state import VisitorStateManager
from detection.zones import ZoneConfig, get_zones_for_camera, load_store_layout
from shared.schemas.events import EventType, StoreEvent

logger = structlog.get_logger("pipeline")


class DetectionPipeline:
    """
    End-to-end detection pipeline for one camera clip.

    Processes a video file frame-by-frame, running person detection with
    ByteTrack tracking, and emits the full range of store events:
    ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN,
    BILLING_QUEUE_ABANDON, and REENTRY.
    """

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence: float = 0.3,
        threshold_x: float = 900.0,
        zone_inner: float = 350.0,
        zone_outer: float = 850.0,
        sample_rate: int = 3,
        store_id: str = "purplle-001",
        camera_id: str = "cam3",
        min_observations: int = 3,
        device: str | None = None,
        layout_path: str | None = None,
        reentry_window_s: float = 120.0,
        staff_min_duration_pct: float = 0.5,
        staff_max_movement: float = 400.0,
        queue_min_time_s: float = 10.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.store_id = store_id
        self.camera_id = camera_id

        # ── Load zones ───────────────────────────────────────────────
        self.zones: list[ZoneConfig] = []
        checkout_zone: ZoneConfig | None = None

        if layout_path is not None:
            layout_file = Path(layout_path)
            if layout_file.exists():
                all_zones = load_store_layout(layout_file)
                self.zones = get_zones_for_camera(all_zones, camera_id)
                # Find checkout zone
                for z in self.zones:
                    if z.is_checkout:
                        checkout_zone = z
                        break
                logger.info(
                    "zones_loaded",
                    count=len(self.zones),
                    zone_ids=[z.zone_id for z in self.zones],
                    checkout=checkout_zone.zone_id if checkout_zone else None,
                )
            else:
                logger.warning("layout_not_found", path=str(layout_file))

        logger.info(
            "pipeline_init",
            model=model_name,
            confidence=confidence,
            threshold_x=threshold_x,
            zone_inner=zone_inner,
            zone_outer=zone_outer,
            sample_rate=sample_rate,
            zones=len(self.zones),
            reentry_window_s=reentry_window_s,
        )

        # ── Detector ─────────────────────────────────────────────────
        self.detector = PersonDetector(
            model_name=model_name,
            confidence=confidence,
            device=device,
        )

        # ── Track manager (full history for zone-based classification)
        self.track_manager = TrackManager(max_history=9999)

        # ── Crossing detector (ENTRY/EXIT) ───────────────────────────
        self.crossing_detector = CrossingDetector(
            threshold_x=threshold_x,
            zone_inner=zone_inner,
            zone_outer=zone_outer,
            store_id=store_id,
            camera_id=camera_id,
            min_observations=min_observations,
        )

        # ── Visitor state manager (zones, dwell, re-entry) ───────────
        self.visitor_manager = VisitorStateManager(
            zones=self.zones,
            store_id=store_id,
            camera_id=camera_id,
            reentry_window_s=reentry_window_s,
        )

        # ── Queue tracker ────────────────────────────────────────────
        self.queue_tracker = QueueTracker(
            checkout_zone=checkout_zone,
            min_queue_time_s=queue_min_time_s,
            store_id=store_id,
            camera_id=camera_id,
        )

        # ── Staff classifier (post-processing) ──────────────────────
        self.staff_classifier = StaffClassifier(
            min_duration_pct=staff_min_duration_pct,
            max_movement_range=staff_max_movement,
        )

    def process_video(
        self,
        video_path: str | Path,
        output_path: str | Path | None = None,
    ) -> list[StoreEvent]:
        """
        Process an entire video file and return all store events.

        Args:
            video_path: Path to the input video file.
            output_path: Optional path for JSONL output file.

        Returns:
            List of all StoreEvent objects emitted during processing.
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = total_frames / fps if fps > 0 else 0

        logger.info(
            "video_opened",
            path=str(video_path),
            fps=fps,
            total_frames=total_frames,
            resolution=f"{width}x{height}",
            duration_s=round(duration, 1),
        )

        video_start = datetime.fromtimestamp(
            video_path.stat().st_mtime, tz=timezone.utc
        )

        # Prepare output file
        output_file = None
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_file = open(output_path, "w")

        all_events: list[StoreEvent] = []
        frame_num = 0
        processed = 0
        start_time = time.perf_counter()
        last_log_time = start_time

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                frame_num += 1

                if frame_num % self.sample_rate != 0:
                    continue

                processed += 1

                # ── 1. Detection + tracking ──────────────────────────
                detections = self.detector.track(frame)

                # ── 2. Update track states ───────────────────────────
                active_tracks = self.track_manager.update(detections, frame_num)

                # ── 3. Crossing detector (ENTRY/EXIT) ────────────────
                crossing_events = self.crossing_detector.check_crossings(
                    active_tracks,
                    video_start_time=video_start,
                    fps=fps,
                )

                # Register entries/exits with visitor manager for re-entry and ID mapping
                for evt in crossing_events:
                    track_id = self._extract_track_id(evt.visitor_id)
                    if track_id is not None:
                        if evt.event_type == EventType.ENTRY:
                            stable_vid = self.visitor_manager.register_entry(track_id, frame_num)
                            # Check if this is a re-entry
                            visitor = self.visitor_manager.get_visitor(stable_vid)
                            if visitor and len(visitor.track_ids) > 1:
                                # This is a re-entry — emit REENTRY event
                                reentry_evt = StoreEvent(
                                    store_id=self.store_id,
                                    camera_id=self.camera_id,
                                    visitor_id=stable_vid,
                                    event_type=EventType.REENTRY,
                                    confidence=evt.confidence,
                                    session_seq=visitor.next_seq(),
                                    timestamp=evt.timestamp,
                                    metadata=evt.metadata,
                                )
                                self._emit(reentry_evt, all_events, output_file)
                            # Update the ENTRY event with the stable visitor ID
                            evt.visitor_id = stable_vid
                            if visitor:
                                evt.session_seq = visitor.next_seq()
                        elif evt.event_type == EventType.EXIT:
                            # Get position for re-entry matching
                            pos = None
                            if evt.metadata.bbox:
                                pos = (evt.metadata.bbox.center_x, evt.metadata.bbox.center_y)
                            stable_vid = self.visitor_manager.register_exit(track_id, frame_num, pos)
                            if stable_vid:
                                evt.visitor_id = stable_vid
                                visitor = self.visitor_manager.get_visitor(stable_vid)
                                if visitor:
                                    evt.session_seq = visitor.next_seq()

                    self._emit(evt, all_events, output_file)

                # ── 4. Visitor state update (zones, dwell) ───────────
                visitor_events = self.visitor_manager.update(
                    active_tracks, frame_num, fps, video_start,
                )

                # ── 5. Queue tracking ────────────────────────────────
                for vevt in visitor_events:
                    # Hook into zone transitions for queue tracking
                    visitor = self.visitor_manager.get_visitor(vevt.visitor_id)
                    if visitor and vevt.zone_id:
                        if vevt.event_type == EventType.ZONE_ENTER:
                            q_evt = self.queue_tracker.on_zone_enter(
                                visitor, vevt.zone_id, frame_num, fps, video_start
                            )
                            if q_evt:
                                self._emit(q_evt, all_events, output_file)
                        elif vevt.event_type == EventType.ZONE_EXIT:
                            q_evt = self.queue_tracker.on_zone_exit(
                                visitor, vevt.zone_id, frame_num, fps, video_start
                            )
                            if q_evt:
                                self._emit(q_evt, all_events, output_file)

                    self._emit(vevt, all_events, output_file)

                # ── Progress logging ─────────────────────────────────
                now = time.perf_counter()
                if now - last_log_time >= 5.0:
                    elapsed = now - start_time
                    pct = (frame_num / total_frames * 100) if total_frames > 0 else 0
                    effective_fps = processed / elapsed if elapsed > 0 else 0
                    logger.info(
                        "progress",
                        frame=frame_num,
                        total=total_frames,
                        pct=round(pct, 1),
                        events=len(all_events),
                        effective_fps=round(effective_fps, 1),
                        active_tracks=self.track_manager.get_active_track_count(),
                    )
                    last_log_time = now

        finally:
            cap.release()
            if output_file is not None:
                output_file.close()

        # ── 6. Post-processing: staff classification ─────────────────
        staff_results = self.staff_classifier.classify(
            self.track_manager.tracks, processed,
        )

        # Update events with staff flags
        staff_track_ids = {tid for tid, sc in staff_results.items() if sc.is_staff}
        staff_visitor_ids: set[str] = set()
        for tid in staff_track_ids:
            vid = self.visitor_manager.get_visitor_id(tid)
            if vid:
                staff_visitor_ids.add(vid)
                visitor = self.visitor_manager.get_visitor(vid)
                if visitor:
                    visitor.is_staff = True

        if staff_visitor_ids:
            logger.info("staff_flagged", visitor_ids=list(staff_visitor_ids))
            for evt in all_events:
                if evt.visitor_id in staff_visitor_ids:
                    evt.is_staff = True

        # Re-write output if we flagged staff (need to update is_staff in JSONL)
        if output_path is not None and staff_visitor_ids:
            with open(output_path, "w") as f:
                for evt in all_events:
                    f.write(evt.to_jsonl() + "\n")

        # ── Final summary ────────────────────────────────────────────
        elapsed = time.perf_counter() - start_time
        effective_fps = processed / elapsed if elapsed > 0 else 0

        type_counts = Counter(e.event_type.value for e in all_events)

        logger.info(
            "pipeline_complete",
            total_frames=total_frames,
            processed_frames=processed,
            elapsed_s=round(elapsed, 1),
            effective_fps=round(effective_fps, 1),
            total_events=len(all_events),
            event_types=dict(type_counts),
            total_tracks=self.track_manager.get_active_track_count(),
            total_visitors=len(self.visitor_manager.get_all_visitors()),
            staff_count=len(staff_visitor_ids),
        )

        if output_path is not None:
            logger.info("output_written", path=str(output_path))

        return all_events

    # ── Helpers ──────────────────────────────────────────────────────

    def _emit(
        self,
        event: StoreEvent,
        all_events: list[StoreEvent],
        output_file,
    ) -> None:
        """Emit an event: append to list, write to file, and log."""
        all_events.append(event)
        jsonl = event.to_jsonl()

        if output_file is not None:
            output_file.write(jsonl + "\n")
            output_file.flush()

        logger.info(
            "event_emitted",
            event_type=event.event_type.value,
            visitor_id=event.visitor_id,
            zone_id=event.zone_id,
            frame=event.metadata.frame_number,
            confidence=round(event.confidence, 3),
        )

    @staticmethod
    def _extract_track_id(visitor_id_str: str) -> int | None:
        """Extract numeric track ID from 'track-N' format."""
        if visitor_id_str.startswith("track-"):
            try:
                return int(visitor_id_str.split("-", 1)[1])
            except (ValueError, IndexError):
                return None
        return None
