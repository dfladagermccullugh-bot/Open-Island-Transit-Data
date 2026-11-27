"""GPS glitches are removed when a bus jumps somewhere impossible and back,
and only then (``pipeline/glitch.py``)."""

from datetime import UTC, datetime, timedelta

from pipeline import glitch

T0 = datetime(2026, 11, 24, 14, 0, tzinfo=UTC)
LAT, LON = 48.0, -122.5
EAST_M = 1 / 74_400  # degrees of longitude per meter at this latitude, about


def reports(vehicle, track, step_s=20):
    """One report every ``step_s`` seconds; ``track`` gives meters east and
    meters north of a starting point, or None for a report not sent."""
    out = []
    for i, where in enumerate(track):
        if where is None:
            continue
        east, north = where
        when = T0 + timedelta(seconds=i * step_s)
        out.append({"vehicle_key": vehicle, "trip_id": "T", "lat": LAT + north / 111_200,
                    "lon": LON + east * EAST_M, "vehicle_ts": when})  # fmt: skip
    return out


def driving(n, speed_ms=10.0, step_s=20):
    return [(i * speed_ms * step_s, 0.0) for i in range(n)]


def test_a_steady_bus_has_no_glitch():
    g = glitch.flag(reports("A", driving(30)))
    assert g.removed == set() and g.unresolved_jumps == 0


def test_a_spike_is_removed():
    track = driving(10)
    track[5] = (track[5][0], 3000.0)  # 3 km north for one report
    rows = reports("A", track)
    g = glitch.flag(rows)
    assert g.removed == {("A", rows[5]["vehicle_ts"])}
    assert (g.spikes, g.plateaus, g.unresolved_jumps) == (1, 0, 0)
    assert len(glitch.keep(rows, g)) == 9


def test_a_short_plateau_is_removed_and_a_long_one_is_not():
    short = driving(20)
    for i in range(6, 9):  # 40 seconds away
        short[i] = (short[i][0], 5000.0)
    g = glitch.flag(reports("A", short))
    assert (g.plateaus, g.plateau_reports, len(g.removed)) == (1, 3, 3)

    long = driving(20)
    for i in range(6, 14):  # 140 seconds away: longer than PLATEAU_S
        long[i] = (long[i][0], 5000.0)
    g = glitch.flag(reports("A", long))
    assert g.removed == set()
    assert g.unresolved_jumps == 2


def test_a_jump_with_no_jump_back_is_kept_and_counted():
    track = driving(10) + [(10_000.0 + i * 200, 0.0) for i in range(10)]
    g = glitch.flag(reports("A", track))
    assert g.removed == set() and g.unresolved_jumps == 1


def test_a_timestamp_a_few_seconds_off_is_kept():
    """Position on its line, but sent 8 seconds early: the bus seems to go
    too fast into it and slowly out of it, and could have driven the whole
    way. Removing it would remove good data."""
    rows = reports("A", driving(10, speed_ms=25.0))
    rows[5]["vehicle_ts"] -= timedelta(seconds=14)
    g = glitch.flag(rows)
    assert g.removed == set()
    assert g.unresolved_jumps == 1


def test_reports_are_ordered_by_time_whatever_their_trip_id():
    track = driving(10)
    track[5] = (track[5][0], 3000.0)
    rows = reports("A", track)
    for r in rows[5:]:
        r["trip_id"] = "NEXT"
    g = glitch.flag(list(reversed(rows)))
    assert len(g.removed) == 1


def test_a_removed_report_on_top_of_another_bus_is_counted():
    track = driving(10)
    track[5] = (track[5][0], 3000.0)
    a = reports("A", track)
    b = reports("B", [(track[5][0] + 5, 3000.0)] * 10)
    g = glitch.flag(a + b)
    assert len(g.removed) == 1 and g.cross_vehicle == 1


def test_the_report_that_jumps_back_is_the_glitch_when_the_run_carries_on():
    """Vehicle 17471 on November 24, 2026, in miniature: a report early in
    time makes the next stretch look like a jump, and later one report
    jumps back. The stretch carries on smoothly past that report, so the
    report is the glitch, not the stretch."""
    track = driving(14, speed_ms=15.0)
    track[9] = (track[9][0] - 1500.0, 0.0)  # 1.5 km back for one report
    rows = reports("A", track)
    rows[4]["vehicle_ts"] -= timedelta(seconds=12)  # a time stamp off
    g = glitch.flag(rows)
    assert g.removed == {("A", rows[9]["vehicle_ts"])}
    assert g.spikes == 1 and g.plateaus == 0
