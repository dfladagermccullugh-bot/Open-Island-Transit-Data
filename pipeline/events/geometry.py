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


RETRACE_TOL_M = 3.0
TIE_M = 1e-6  # offsets this close are equal: far above rounding error, far below GPS error


def drop_retraced(lats: list[float], lons: list[float]) -> tuple[list[float], list[float], float]:
    """Collapse segments drawn forth, back and forth again (points A, B, A, B
    become A, B). Some published shapes repeat every segment this way, which
    inflates distances along the shape about threefold. Returns the cleaned
    points and the metres of shape removed."""
    x, y = to_xy(lats, lons)
    keep: list[int] = []
    removed = 0.0

    def near(i: int, j: int) -> bool:
        return math.hypot(x[i] - x[j], y[i] - y[j]) <= RETRACE_TOL_M

    for i in range(len(lats)):
        keep.append(i)
        while len(keep) >= 4 and near(keep[-4], keep[-2]) and near(keep[-3], keep[-1]):
            a, b = keep[-2], keep[-1]
            removed += math.hypot(x[a] - x[keep[-3]], y[a] - y[keep[-3]])
            removed += math.hypot(x[b] - x[a], y[b] - y[a])
            del keep[-2:]
    return [lats[i] for i in keep], [lons[i] for i in keep], removed


def _nearest(off: np.ndarray) -> int:
    """Index of the smallest offset; of those within ``TIE_M`` of it, the first.

    Two segments are often exactly as near as each other: they meet at the
    vertex that is the nearest point, or the shape draws a stretch out and
    back. The last bits of the computed offsets then differ between machines,
    so the smallest one alone is not a reproducible choice."""
    return int(np.flatnonzero(off <= off.min() + TIE_M)[0])


@dataclass
class Shape:
    """A polyline with the distance along it at each vertex."""

    x: np.ndarray
    y: np.ndarray
    cum: np.ndarray  # metres from the start, at each vertex
    retraced_m: float = 0.0  # metres of retraced segments removed (``drop_retraced``)

    @classmethod
    def from_latlon(cls, lats: list[float], lons: list[float]) -> Shape:
        lats, lons, removed = drop_retraced(list(lats), list(lons))
        x, y = to_xy(lats, lons)
        steps = np.hypot(np.diff(x), np.diff(y))
        return cls(x, y, np.concatenate([[0.0], np.cumsum(steps)]), removed)

    @property
    def length(self) -> float:
        return float(self.cum[-1])

    def project(self, lat: float, lon: float, lo: float = 0.0, hi: float | None = None):
        """Nearest point on the shape between distances ``lo`` and ``hi``.

        Returns (distance along the shape, perpendicular offset in metres), or
        None if the window holds no segment. Of segments equally near, the
        earliest (``_nearest``). A point nearest the end of a segment gets
        that vertex's own distance, so ``heading`` there is the heading of the
        segment that leaves the vertex."""
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
        along = np.where(t >= 1.0, self.cum[first + 1 : last + 1], start + t * seg)
        off = np.hypot(x0 + t * dx - px, y0 + t * dy - py)
        i = _nearest(off)
        return float(along[i]), float(off[i])

    def passes(self, lat: float, lon: float, max_off: float) -> list[tuple[float, float]]:
        """Every place the shape passes the point: for each run of consecutive
        segments within ``max_off`` metres, the nearest point of the run, as
        (distance along the shape, offset). The nearest point on the whole
        shape is always included. Sorted by distance along the shape. Ties
        and vertices as in ``project``."""
        px, py = to_xy(lat, lon)
        x0, y0 = self.x[:-1], self.y[:-1]
        dx, dy = np.diff(self.x), np.diff(self.y)
        seg2 = dx * dx + dy * dy
        with np.errstate(invalid="ignore", divide="ignore"):
            t = np.clip(np.where(seg2 > 0, ((px - x0) * dx + (py - y0) * dy) / seg2, 0.0), 0, 1)
        along = np.where(t >= 1.0, self.cum[1:], self.cum[:-1] + t * np.sqrt(seg2))
        off = np.hypot(x0 + t * dx - px, y0 + t * dy - py)
        out = {_nearest(off)}
        near = np.flatnonzero(off <= max_off)
        if near.size:
            runs = np.split(near, np.flatnonzero(np.diff(near) > 1) + 1)
            out |= {int(r[_nearest(off[r])]) for r in runs}
        return sorted((float(along[i]), float(off[i])) for i in out)

    def heading(self, along: float) -> float:
        """Compass heading of the shape (degrees) at a distance along it."""
        i = int(np.searchsorted(self.cum, along, side="right")) - 1
        i = min(max(i, 0), len(self.cum) - 2)
        return math.degrees(math.atan2(self.x[i + 1] - self.x[i], self.y[i + 1] - self.y[i])) % 360
