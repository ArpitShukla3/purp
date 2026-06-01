"""
YOLOv8 person detector wrapper.

Provides a clean interface around the Ultralytics YOLO model for detecting
people in video frames.  Only class 0 (person) detections are returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from ultralytics import YOLO


@dataclass
class Detection:
    """A single person detection in one frame."""

    bbox: tuple[float, float, float, float]  # (x1, y1, x2, y2)
    confidence: float
    track_id: int | None = None  # populated by tracker

    @property
    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def center_y(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


class PersonDetector:
    """
    YOLOv8 person detector.

    Wraps the Ultralytics YOLO model and filters results to only
    class 0 (person) detections above the confidence threshold.
    """

    PERSON_CLASS_ID = 0

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence: float = 0.3,
        device: str | None = None,
    ) -> None:
        self.model = YOLO(model_name)
        self.confidence = confidence
        self.device = device
        # Warm-up the model with a dummy input
        self.model.predict(
            np.zeros((640, 640, 3), dtype=np.uint8),
            verbose=False,
            device=self.device,
        )

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """
        Run person detection on a single frame.

        Returns a list of Detection objects filtered to persons only.
        """
        results = self.model.predict(
            frame,
            conf=self.confidence,
            classes=[self.PERSON_CLASS_ID],
            verbose=False,
            device=self.device,
        )

        detections: list[Detection] = []
        for result in results:
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                continue
            for i in range(len(boxes)):
                xyxy = boxes.xyxy[i].cpu().numpy()
                conf = float(boxes.conf[i].cpu().numpy())
                detections.append(
                    Detection(
                        bbox=(float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])),
                        confidence=conf,
                    )
                )

        return detections

    def track(self, frame: np.ndarray) -> list[Detection]:
        """
        Run person detection + ByteTrack tracking on a single frame.

        This must be called on sequential frames with ``persist=True``
        to maintain track IDs across frames.

        Returns detections with ``track_id`` populated.
        """
        results = self.model.track(
            frame,
            conf=self.confidence,
            classes=[self.PERSON_CLASS_ID],
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
            device=self.device,
        )

        detections: list[Detection] = []
        for result in results:
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                continue

            # track IDs may be None if tracking hasn't assigned yet
            ids = boxes.id
            for i in range(len(boxes)):
                xyxy = boxes.xyxy[i].cpu().numpy()
                conf = float(boxes.conf[i].cpu().numpy())
                track_id = int(ids[i].cpu().numpy()) if ids is not None else None
                detections.append(
                    Detection(
                        bbox=(float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])),
                        confidence=conf,
                        track_id=track_id,
                    )
                )

        return detections
