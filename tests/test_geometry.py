"""Ties in ``pipeline/events/geometry.py`` have one fixed answer (events/0.3.1).

A point equally near two parts of a shape must not let the last bits of a
distance choose between them: that choice once differed between GitHub's
runner and a cloud session (trip 1155435, November 20, 2026). These cases
are built in metres, so the tie is exact, and then nudged by far less than
``TIE_M``, as another machine's rounding would."""

import pytest

from pipeline.events import geometry
from pipeline.events.geometry import TIE_M, Shape


def at(x_m: float, y_m: float) -> tuple[float, float]:
    """(lat, lon) of a point x metres east and y metres north of the origin."""
    return y_m / geometry._KY, x_m / geometry._KX


def shape(points: list[tuple[float, float]]) -> Shape:
    lats, lons = zip(*(at(x, y) for x, y in points), strict=True)
    return Shape.from_latlon(list(lats), list(lons))


# Out 1 km north, across 20 m, and back south: a hairpin, like a loop's two legs.
HAIRPIN = shape([(0, 0), (0, 1000), (20, 1000), (20, 0)])


@pytest.mark.parametrize("nudge", [0.0, 1e-9, -1e-9, TIE_M / 10, -TIE_M / 10])
def test_a_point_between_two_legs_takes_the_first(nudge):
    lat, lon = at(10 + nudge, 500)
    along, off = HAIRPIN.project(lat, lon)
    assert along == pytest.approx(500, abs=1e-6)  # the northbound leg, not 1,520 m
    assert off == pytest.approx(10, abs=1e-6)
    near = HAIRPIN.passes(lat, lon, max_off=15)
    assert [round(a) for a, _ in near] == [500, 1520]


def test_a_point_at_a_vertex_gets_the_vertex_and_the_heading_that_leaves_it():
    lat, lon = at(0, 1000)
    along, _ = HAIRPIN.project(lat, lon)
    assert along == HAIRPIN.cum[1]
    assert HAIRPIN.heading(along) == pytest.approx(90)  # east, along the crossing


@pytest.mark.parametrize("nudge", [1e-9, -1e-9])
def test_a_point_a_nanometre_from_a_vertex_stays_at_the_vertex(nudge):
    # Not a tie any more: the nearer leg wins, a nanometre from the vertex.
    # (The heading there is that leg's; a report would have to fall within
    # about a nanometre of a vertex for that to matter. The weekly rebuild,
    # scripts/reproduce.py, would show it.)
    lat, lon = at(nudge, 1000 + nudge)
    along, off = HAIRPIN.project(lat, lon)
    assert along == pytest.approx(HAIRPIN.cum[1], abs=1e-8) and off < 1e-8


def test_a_real_difference_still_decides():
    # 1 mm nearer the southbound leg is a real difference, not a tie.
    lat, lon = at(10.001, 500)
    along, _ = HAIRPIN.project(lat, lon)
    assert along == pytest.approx(1520, abs=1e-3)
