#!/usr/bin/env python3
"""
Store Intelligence — Detection CLI
====================================

Command-line entry point for running the full detection pipeline on a
video clip.  Emits ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL,
BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, and REENTRY events.

Usage::

    python -m detection.cli \\
        --input "CCTV Footage.../CAM 3.mp4" \\
        --output detection/output/cam3_events.jsonl \\
        --layout detection/store_layout.json \\
        --camera-id cam3

    # Quick test with higher frame skip
    python -m detection.cli \\
        --input "path/to/video.mp4" \\
        --sample-rate 6 \\
        --confidence 0.35
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# Ensure the project root is on sys.path so `shared` is importable
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from shared.logging import setup_logging  # noqa: E402

setup_logging()

from detection.pipeline import DetectionPipeline  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Store Intelligence detection pipeline on a video clip.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the input video file.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path for JSONL output file. Auto-generated if not specified.",
    )
    parser.add_argument(
        "--model",
        default="yolov8n.pt",
        help="YOLOv8 model name or path.",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.3,
        help="Minimum detection confidence threshold.",
    )
    parser.add_argument(
        "--threshold-x",
        type=float,
        default=900.0,
        help="X-coordinate of the vertical entry/exit threshold line (pixels).",
    )
    parser.add_argument(
        "--zone-inner",
        type=float,
        default=350.0,
        help="Inner boundary of the entry zone (deep-store side, pixels).",
    )
    parser.add_argument(
        "--zone-outer",
        type=float,
        default=850.0,
        help="Outer boundary of the entry zone (door side, pixels).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=3,
        help="Process every Nth frame (higher = faster, less accurate).",
    )
    parser.add_argument(
        "--store-id",
        default="purplle-001",
        help="Store identifier for emitted events.",
    )
    parser.add_argument(
        "--camera-id",
        default="cam3",
        help="Camera identifier for emitted events.",
    )
    parser.add_argument(
        "--min-observations",
        type=int,
        default=3,
        help="Minimum track observations before a crossing is considered valid.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Device for inference (e.g., 'cpu', 'cuda:0'). Auto-detected if not set.",
    )

    # ── New: layout & advanced features ──────────────────────────────
    parser.add_argument(
        "--layout",
        default="detection/store_layout.json",
        help="Path to store_layout.json with zone polygon definitions.",
    )
    parser.add_argument(
        "--reentry-window",
        type=float,
        default=120.0,
        help="Seconds within which a re-entering person reuses the same visitor ID.",
    )
    parser.add_argument(
        "--staff-min-duration",
        type=float,
        default=0.5,
        help="Minimum fraction of video a track must span to be classified as staff.",
    )
    parser.add_argument(
        "--staff-max-movement",
        type=float,
        default=400.0,
        help="Maximum x-range (pixels) for a track to be considered staff.",
    )
    parser.add_argument(
        "--queue-min-time",
        type=float,
        default=10.0,
        help="Minimum seconds in checkout zone before it counts as a transaction.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Determine output path
    output_path = args.output
    if output_path is None:
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(exist_ok=True)
        video_stem = Path(args.input).stem.replace(" ", "_")
        output_path = str(output_dir / f"{video_stem}_events.jsonl")

    # Build and run pipeline
    pipeline = DetectionPipeline(
        model_name=args.model,
        confidence=args.confidence,
        threshold_x=args.threshold_x,
        zone_inner=args.zone_inner,
        zone_outer=args.zone_outer,
        sample_rate=args.sample_rate,
        store_id=args.store_id,
        camera_id=args.camera_id,
        min_observations=args.min_observations,
        device=args.device,
        layout_path=args.layout,
        reentry_window_s=args.reentry_window,
        staff_min_duration_pct=args.staff_min_duration,
        staff_max_movement=args.staff_max_movement,
        queue_min_time_s=args.queue_min_time,
    )

    events = pipeline.process_video(
        video_path=args.input,
        output_path=output_path,
    )

    # ── Summary ──────────────────────────────────────────────────────
    type_counts = Counter(e.event_type.value for e in events)
    staff_events = sum(1 for e in events if e.is_staff)
    unique_visitors = len(set(e.visitor_id for e in events))

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Detection Pipeline Summary", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  Input:        {args.input}", file=sys.stderr)
    print(f"  Output:       {output_path}", file=sys.stderr)
    print(f"  Layout:       {args.layout}", file=sys.stderr)
    print(f"  Threshold-X:  {args.threshold_x}", file=sys.stderr)
    print(f"{'─'*60}", file=sys.stderr)

    for etype in [
        "ENTRY", "EXIT", "REENTRY",
        "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
        "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON",
    ]:
        count = type_counts.get(etype, 0)
        if count > 0:
            print(f"  {etype:25s} {count:4d}", file=sys.stderr)

    print(f"{'─'*60}", file=sys.stderr)
    print(f"  Total events:             {len(events):4d}", file=sys.stderr)
    print(f"  Unique visitors:          {unique_visitors:4d}", file=sys.stderr)
    print(f"  Staff-flagged events:     {staff_events:4d}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
