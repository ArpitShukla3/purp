"""
Zone geometry utilities.

Loads store zone polygons from ``store_layout.json`` and provides
point-in-polygon testing to determine which zone(s) a tracked person
is currently inside.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ZoneConfig:
    """Configuration for a single store zone."""

    zone_id: str
    name: str
    camera_id: str
    zone_type: str                          # entrance, aisle, checkout, display, outside
    polygon: list[tuple[float, float]]      # list of (x, y) vertices
    dwell_threshold_ms: int = 15000         # minimum dwell to trigger ZONE_DWELL

    @property
    def is_checkout(self) -> bool:
        return self.zone_type == "checkout"

    @property
    def is_entrance(self) -> bool:
        return self.zone_type == "entrance"

    @property
    def is_outside(self) -> bool:
        return self.zone_type == "outside"


def load_store_layout(path: str | Path) -> list[ZoneConfig]:
    """
    Load zone definitions from a store_layout.json file.

    Returns a list of ZoneConfig objects.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Store layout not found: {path}")

    with open(path) as f:
        data = json.load(f)

    zones: list[ZoneConfig] = []
    for z in data.get("zones", []):
        polygon = [(float(pt[0]), float(pt[1])) for pt in z["polygon"]]
        zones.append(
            ZoneConfig(
                zone_id=z["zone_id"],
                name=z["name"],
                camera_id=z["camera_id"],
                zone_type=z["type"],
                polygon=polygon,
                dwell_threshold_ms=z.get("dwell_threshold_ms", 15000),
            )
        )

    return zones


def get_zones_for_camera(
    zones: list[ZoneConfig], camera_id: str
) -> list[ZoneConfig]:
    """Filter zones to those belonging to a specific camera."""
    return [z for z in zones if z.camera_id == camera_id]


def point_in_polygon(
    x: float, y: float, polygon: list[tuple[float, float]]
) -> bool:
    """
    Ray-casting algorithm for point-in-polygon test.

    Returns True if (x, y) is inside the polygon defined by the
    list of (px, py) vertices.  Works for convex and concave polygons.
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]

        # Check if the ray from (x, y) going right crosses edge (i, j)
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside

        j = i

    return inside


def get_zone_for_point(
    x: float, y: float, zones: list[ZoneConfig]
) -> ZoneConfig | None:
    """
    Return the first zone containing the point (x, y).

    Returns None if the point is not inside any zone.
    Zones are checked in order — if zones overlap, the first match wins.
    """
    for zone in zones:
        if point_in_polygon(x, y, zone.polygon):
            return zone
    return None


def get_all_zones_for_point(
    x: float, y: float, zones: list[ZoneConfig]
) -> list[ZoneConfig]:
    """
    Return all zones containing the point (x, y).

    Unlike ``get_zone_for_point``, this returns *all* matching zones
    (useful when zones overlap).
    """
    return [z for z in zones if point_in_polygon(x, y, z.polygon)]
