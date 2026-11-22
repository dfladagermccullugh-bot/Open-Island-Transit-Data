"""Distances on the ground, for the stages that compare positions."""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two points (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
