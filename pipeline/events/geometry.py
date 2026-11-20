"""Shapes as polylines in metres, and projection of points onto them.

Coordinates are converted to a local flat projection (metres east and north
of a fixed origin). Over Island Transit's service area (about 60 km across)
the distortion is well under 0.1%, far below GPS error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_M = 6_371_000.0
ORIGIN_LAT = 48.2  # middle of the service area
_KX = EARTH_RADIUS_M * math.cos(math.radians(ORIGIN_LAT)) * math.pi / 180
_KY = EARTH_RADIUS_M * math.pi / 180


def to_xy(lat: np.ndarray | float, lon: np.ndarray | float) -> tuple:
    return np.asarray(lon, dtype=float) * _KX, np.asarray(lat, dtype=float) * _KY


@dataclass
class Shape:
    """A polyline with the distance along it at each vertex."""

    x: np.ndarray
    y: np.ndarray
    cum: np.ndarray  # metres from the start, at each vertex

    @classmethod
    def from_latlon(cls, lats: list[float], lons: list[float]) -> Shape:
        x, y = to_xy(lats, lons)
        steps = np.hypot(np.diff(x), np.diff(y))
        return cls(x, y, np.concatenate([[0.0], np.cumsum(steps)]))

    @property
    def length(self) -> float:
        return float(self.cum[-1])

    def project(self, lat: float, lon: float, lo: float = 0.0, hi: float | None = None):
        """Nearest point on the shape between distances ``lo`` and ``hi``.

        Returns (distance along the shape, perpendicular offset in metres), or
        None if the window holds no segment."""
        hi = self.length if hi is None else hi
        px, py = to_xy(lat, lon)
        # Segments that overlap [lo, hi].
        first = max(int(np.searchsorted(self.cum, lo, side="right")) - 1, 0)
        last = min(int(np.searchsorted(self.cum, hi, side="left")), len(self.cum) - 1)
        if last <= first:
            last = min(first + 1, len(self.cum) - 1)
            if last == first:
                return None
        x0, y0 = self.x[first:last], self.y[first:last]
        dx, dy = self.x[first + 1 : last + 1] - x0, self.y[first + 1 : last + 1] - y0
        seg2 = dx * dx + dy * dy
        seg = np.sqrt(seg2)
        start = self.cum[first:last]
        with np.errstate(invalid="ignore", divide="ignore"):
            t = np.where(seg2 > 0, ((px - x0) * dx + (py - y0) * dy) / seg2, 0.0)
            # The window may cut a segment: keep t inside the part within [lo, hi].
            t_lo = np.where(seg > 0, (lo - start) / seg, 0.0)
            t_hi = np.where(seg > 0, (hi - start) / seg, 1.0)
        t = np.clip(t, np.maximum(t_lo, 0.0), np.minimum(t_hi, 1.0))
        along = start + t * seg
        off = np.hypot(x0 + t * dx - px, y0 + t * dy - py)
        i = int(np.argmin(off))
        return float(along[i]), float(off[i])

    def heading(self, along: float) -> float:
        """Compass heading of the shape (degrees) at a distance along it."""
        i = int(np.searchsorted(self.cum, along, side="right")) - 1
        i = min(max(i, 0), len(self.cum) - 2)
        return math.degrees(math.atan2(self.x[i + 1] - self.x[i], self.y[i + 1] - self.y[i])) % 360
