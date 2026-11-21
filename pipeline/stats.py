"""Small statistics helpers shared by every stage, so that one definition of
a median or a share stands behind every published number."""

from __future__ import annotations

import math
from collections.abc import Iterable


def quantile(values: Iterable[float], q: float) -> float | None:
    """Quantile by linear interpolation between order statistics (numpy's
    default). The median of an even count is the mean of the two middle
    values. None for no values."""
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def median(values: Iterable[float]) -> float | None:
    return quantile(values, 0.5)


def share(num: float, den: float, digits: int = 6) -> float | None:
    """num / den rounded, or None when the denominator is zero."""
    return round(num / den, digits) if den else None
