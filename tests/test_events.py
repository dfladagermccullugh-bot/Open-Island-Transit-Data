import io
import random
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest

from pipeline.events import geometry, infer
from pipeline.events.geometry import Shape
from pipeline.events.infer import MAX_GAP_S, STOP_RADIUS_M, crossing, infer_day, track
from pipeline.events.ontime import classify, summarize
from pipeline.gtfs.zipread import read_zip

LAT = 48.2
LON0 = -122.70
DAY = date(2026, 11, 20)  # standard time: GTFS 00:00 is 08:00 UTC
ORIGIN = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)


def lon_at(metres: float) -> float:
    """Longitude ``metres`` east of LON0 along latitude LAT (same flat projection)."""
    return LON0 + metres / geometry._KX


def straight(length: float = 6000) -> Shape:
    return Shape.from_latlon([LAT, LAT], [LON0, lon_at(length)])


def test_project_and_heading():
    s = straight()
    d, off = s.project(LAT + 0.0001, lon_at(1234))  # about 11 m north of the line
    assert d == pytest.approx(1234, abs=0.5)
    assert off == pytest.approx(11.1, abs=0.3)
    assert s.heading(100) == pytest.approx(90, abs=0.01)  # due east
    d, off = s.project(LAT, lon_at(5000), 0, 3000)  # window ends at 3000
    assert d == pytest.approx(3000, abs=0.5)
    assert off == pytest.approx(2000, abs=0.5)  # measured to the window's end, not the line


T0 = ORIGIN.timestamp() + 6 * 3600  # 06:00 local


def reports(points, bearing=90.0, speed=10.0):
    """(seconds after T0, metres along) -> report tuples."""
    return [(T0 + t, LAT, lon_at(d), bearing, speed) for t, d in points]


def test_crossing_interpolates_and_respects_gaps():
    tr = track(straight(), reports([(0, 0), (20, 200), (40, 400), (200, 2000)]), T0)
    t, gap, status = crossing(tr, 300)
    assert status == "observed" and gap == 20
    assert t == pytest.approx(T0 + 30, abs=0.1)
    t, gap, status = crossing(tr, 1000)  # bracketed by reports 160 s apart
    assert (t, status) == (None, "gap") and gap == 160 > MAX_GAP_S
    assert crossing(tr, 5000)[2] == "not_reached"


def test_tracking_starts_at_the_start_and_ignores_wrong_way():
    pts = [(-60, 3000), (-40, 2800)]  # still on the previous trip, elsewhere on the line
    pts += [(0, 0), (20, 200), (40, 400)]
    rs = reports(pts)
    rs.insert(4, (T0 + 30, LAT, lon_at(250), 270.0, 10.0))  # moving west at speed
    tr = track(straight(), rs, T0)
    assert tr.before_start == 2
    assert tr.wrong_direction == 1
    assert tr.dists == pytest.approx([0, 200, 400], abs=0.5)


def test_off_shape_reports_are_counted_and_skipped():
    rs = reports([(0, 0), (20, 200), (40, 400)])
    rs.insert(2, (T0 + 30, LAT + 0.01, lon_at(300), 90.0, 10.0))  # 1.1 km north
    tr = track(straight(), rs, T0)
    assert tr.off_shape == 1 and len(tr.dists) == 3


def gtfs_zip() -> bytes:
    stops = [(f"S{i}", d) for i, d in enumerate((0, 2000, 4000, 6000))]
    files = {
        "agency.txt": "agency_id,agency_name\n1,IT\n",
        "routes.txt": "route_id,route_short_name\nR1,1\n",
        "calendar.txt": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\nWK,1,1,1,1,1,0,0,20260101,20271231\n"
        ),
        "trips.txt": "route_id,service_id,trip_id,shape_id\nR1,WK,T1,SH\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        + "".join(f"SH,{LAT},{lon_at(d)},{i}\n" for i, d in enumerate((0, 3000, 6000))),
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(f"{s},{s},{LAT},{lon_at(d)}\n" for s, d in stops),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        + "".join(
            f"T1,06:{m:02d}:00,06:{m:02d}:00,{s},{i + 1}\n"
            for i, ((s, _), m) in enumerate(zip(stops, (0, 4, 8, 12), strict=True))
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def vehicle_rows(points):
    return [
        {"vehicle_key": "V1", "trip_id": "T1", "lat": LAT, "lon": lon_at(d),
         "vehicle_ts": datetime.fromtimestamp(T0 + t, UTC), "bearing": 90.0, "speed": 8.0}
        for t, d in points
    ]  # fmt: skip


def test_infer_day_end_to_end():
    # Waits at the start, leaves at 06:01, 8 m/s, a 5-minute gap after 3 km.
    pts = [(-120, 0), (-60, 0), (0, 0), (60, 0)]
    pts += [(60 + k * 20, 160 * k) for k in range(1, 19)]  # up to 2880 m at 06:07
    pts += [(720, 5000), (740, 5160), (760, 5320), (780, 5480), (800, 5640), (820, 5800),
            (840, 5960), (860, 6000)]  # fmt: skip
    events, trips = infer_day(DAY, read_zip(gtfs_zip()), vehicle_rows(pts), {("R1", "", "S1")})
    by = {e["stop_id"]: e for e in events}
    assert len(events) == 4 and trips[0]["reports_used"] > 20

    first = by["S0"]
    assert first["arrival_status"] == "not_applicable"
    # Departure: crosses 40 m at 60 s + 40/160*20 s = 65 s after 06:00.
    assert first["departure_time"] == pytest.approx(
        datetime.fromtimestamp(T0 + 65, UTC), abs=timedelta(seconds=0.1)
    )
    s1 = by["S1"]  # 2000 m: arrival at 1960 m, departure at 2040 m
    assert s1["is_timepoint"]
    assert (s1["arrival_time"] - ORIGIN).total_seconds() == pytest.approx(
        6 * 3600 + 60 + (2000 - STOP_RADIUS_M) / 8, abs=0.1
    )
    assert s1["departure_status"] == "observed"
    assert by["S2"]["arrival_status"] == "gap"  # 4000 m falls in the 5-minute gap
    last = by["S3"]
    assert last["departure_status"] == "not_applicable"
    assert last["arrival_status"] == "observed"
    assert last["scheduled_arrival"] == ORIGIN + timedelta(hours=6, minutes=12)


def test_infer_day_without_reports():
    events, trips = infer_day(DAY, read_zip(gtfs_zip()), [], set())
    assert {e["arrival_status"] for e in events} == {"no_reports", "not_applicable"}
    assert trips[0]["position_reports"] == 0


def event(sched_min, actual_min, last=False, timepoint=True, route="R1", stop="S1", seq=1):
    s = ORIGIN + timedelta(hours=6, minutes=sched_min)
    a = ORIGIN + timedelta(hours=6, minutes=actual_min) if actual_min is not None else None
    key = "arrival" if last else "departure"
    return {"is_last_stop": last, "is_timepoint": timepoint, "route_id": route,
            "stop_id": stop, "stop_sequence": seq,
            "arrival_time": a if last else None, "departure_time": None if last else a,
            f"scheduled_{key}": s,
            "scheduled_arrival" if not last else "scheduled_departure": None}  # fmt: skip


def test_classify():
    assert classify(event(10, 10))[0] == "on_time"
    assert classify(event(10, 9))[0] == "on_time"  # exactly 60 s early
    assert classify(event(10, 8.9))[0] == "early"
    assert classify(event(10, 15))[0] == "on_time"  # exactly 300 s late
    assert classify(event(10, 15.1))[0] == "late"
    assert classify(event(10, 2, last=True))[0] == "on_time"  # early at the last stop
    assert classify(event(10, None))[0] == "not_observed"


def test_summarize_skips_non_timepoints_and_events_before_collection():
    events = [event(10, 10), event(20, 30), event(30, None), event(40, 40, timepoint=False),
              event(-600, -600)]  # fmt: skip
    s = summarize(events, collection_start=ORIGIN)
    assert (s["scheduled"], s["observed"], s["on_time"], s["late"]) == (3, 2, 1, 1)
    assert s["observed_share"] == round(2 / 3, 4)
    assert s["on_time_share_of_observed"] == 0.5
    assert "experimental" not in s
    assert s["by_hour"]["6"]["scheduled"] == 3  # 06:10 to 06:30 Pacific


def test_summarize_by_route_stop_keeps_deviations():
    events = [event(10, 12, stop="A", seq=1), event(20, 30, stop="B", seq=5),
              event(30, None, stop="B", seq=5), event(40, 39.5, stop="A", seq=1)]  # fmt: skip
    s = summarize(events, collection_start=ORIGIN, stop_names={"A": "Stop A"})
    a, b = s["by_route_stop"]["R1"]["A"], s["by_route_stop"]["R1"]["B"]
    assert a["name"] == "Stop A" and b["name"] == ""
    assert a["deviations_s"] == [-30, 120]
    assert (b["scheduled"], b["observed"], b["late"], b["stop_sequence_median"]) == (2, 1, 1, 5)


def test_drop_retraced_collapses_forth_back_forth():
    lons = [lon_at(d) for d in (0, 500, 0, 500, 1000, 500, 1000, 1500)]
    _lats, out, removed = geometry.drop_retraced([LAT] * len(lons), lons)
    assert out == pytest.approx([lon_at(d) for d in (0, 500, 1000, 1500)])
    assert removed == pytest.approx(2000, abs=1)
    s = Shape.from_latlon([LAT] * len(lons), lons)
    assert s.length == pytest.approx(1500, abs=1) and s.retraced_m == pytest.approx(2000, abs=1)
    # A single out-and-back (A, B, A) is a real path and is kept.
    _, kept, removed = geometry.drop_retraced([LAT] * 3, [lon_at(0), lon_at(500), lon_at(0)])
    assert len(kept) == 3 and removed == 0


def out_and_back() -> Shape:
    """East 2 km along LAT, then back west 2 km along a road 50 m north."""
    near = 50 / geometry._KY
    lats = [LAT, LAT, LAT + near, LAT + near]
    lons = [lon_at(0), lon_at(2000), lon_at(2000), lon_at(0)]
    return Shape.from_latlon(lats, lons)


def test_passes_and_stop_order_on_out_and_back():
    from pipeline.events.infer import order_stops

    s = out_and_back()
    mid = 25 / geometry._KY  # halfway between the two roads
    p = s.passes(LAT + mid, lon_at(300), 150)
    assert [round(d) for d, _ in p] == [300, 3750]
    stops = [
        [(0.0, 0.0)],
        s.passes(LAT + mid, lon_at(300), 150),  # equally near both roads
        s.passes(LAT, lon_at(1500), 150),  # outbound
        s.passes(LAT + 2 * mid, lon_at(500), 150),  # return
    ]
    placed = order_stops(stops)
    assert [round(d) for d, _ in placed] == [0, 300, 1500, 3550]


def test_tracking_reanchors_past_a_branch_not_driven():
    # Shape: east 0-1000, a 3 km spur north and back, then east 1000-2000.
    north = 1500 / geometry._KY
    lats = [LAT, LAT, LAT + north, LAT, LAT]
    lons = [lon_at(0), lon_at(1000), lon_at(1000), lon_at(1010), lon_at(2000)]
    s = Shape.from_latlon(lats, lons)
    spur_end = s.cum[3]
    # The bus drives straight east, leaving out the spur.
    rs = reports([(0, 0), (20, 300), (40, 600), (60, 900), (80, 1200), (100, 1500), (120, 1800)])
    tr = track(s, rs, T0)
    assert len(tr.skips) == 1
    a, b = tr.skips[0]
    assert a == pytest.approx(900, abs=1) and b > spur_end
    assert tr.off_shape == 0 and len(tr.dists) == len(rs)
    assert crossing(tr, 1000 + 1500)[2] == "skipped"  # a stop up the spur
    assert crossing(tr, s.length - 300)[2] == "observed"


def test_last_stop_arrival_survives_a_report_just_short_of_it():
    # The last stop is at the end of the 6 km shape; arrival is timed at
    # 5960 m. A report at 5955 m used to end tracking (within 50 m of the
    # end) before the bus crossed that point, losing the arrival.
    pts = [(0, 0)] + [(k * 20, 160 * k) for k in range(1, 37)]  # 5760 m at 720 s
    pts += [(740, 5955), (760, 5990), (780, 6000)]
    events, _ = infer_day(DAY, read_zip(gtfs_zip()), vehicle_rows(pts), set())
    last = next(e for e in events if e["is_last_stop"])
    assert last["arrival_status"] == "observed"
    # Crosses 5960 m between the reports at 5955 m (740 s) and 5990 m (760 s).
    assert (last["arrival_time"] - ORIGIN).total_seconds() == pytest.approx(
        6 * 3600 + 740 + 5 / 35 * 20, abs=0.1
    )


def test_a_stop_out_of_order_or_far_from_the_shape_is_unplaced():
    from pipeline.events.infer import order_stops

    # The second stop's only candidate lies behind the first stop.
    placed = order_stops([[(1000.0, 1.0)], [(200.0, 4.0)], [(1500.0, 6.0)]])
    assert placed[0] == (1000.0, 1.0) and placed[2] == (1500.0, 6.0)
    assert placed[1] == (None, None)
    # No candidate at all (farther than MAX_STOP_OFFSET_M from the shape).
    assert order_stops([[(0.0, 1.0)], [], [(900.0, 2.0)]])[1] == (None, None)


def test_unplaced_stop_gets_no_time_and_is_labelled():
    z = read_zip(gtfs_zip())
    static = infer.StaticData.load(z)
    static.stops["S2"]["stop_lat"] = str(LAT + 0.02)  # about 2 km north of the road
    pts = [(0, 0)] + [(k * 20, 160 * k) for k in range(1, 38)]
    events, _ = infer_day(DAY, z, vehicle_rows(pts), set(), static=static)
    by = {e["stop_id"]: e for e in events}
    assert by["S2"]["arrival_status"] == by["S2"]["departure_status"] == "unplaced"
    assert by["S2"]["arrival_time"] is None and by["S2"]["stop_distance_m"] is None
    assert by["S1"]["departure_status"] == "observed" and by["S1"]["stop_offset_m"] < 1


def test_a_trip_with_a_missing_stop_does_not_stop_the_day():
    z = read_zip(gtfs_zip())
    static = infer.StaticData.load(z)
    del static.stops["S2"]
    events, trips = infer_day(DAY, z, vehicle_rows([(0, 0), (20, 160)]), set(), static=static)
    assert events == [] and "KeyError" in trips[0]["problem"]
    assert trips[0]["position_reports"] == 2 and trips[0]["reports_used"] is None


def test_equally_near_segments_resolve_to_the_earlier_one():
    # A road drawn out (east 2 km) and back, the return line ending 2e-7 m to
    # the north. A point 5 m north at 300 m is nearer the return leg by less
    # than a micrometre. Differences that small used to decide the choice, and
    # rounding makes them differ between machines; now the earlier segment wins.
    hair = 2e-7 / geometry._KY
    s = Shape.from_latlon([LAT, LAT, LAT + hair], [lon_at(0), lon_at(2000), lon_at(0)])
    lat, lon = LAT + 5 / geometry._KY, lon_at(300)
    d, off = s.project(lat, lon)
    assert d == pytest.approx(300, abs=0.01) and off == pytest.approx(5, abs=0.01)
    # Both legs lie within reach in one run, so the stop gets one place: the first pass.
    assert [round(d) for d, _ in s.passes(lat, lon, 150)] == [300]
    # A real difference still decides: 1 mm nearer the return leg.
    near = Shape.from_latlon(
        [LAT, LAT, LAT + 1e-3 / geometry._KY], [lon_at(0), lon_at(2000), lon_at(0)]
    )
    assert near.project(lat, lon)[0] == pytest.approx(3700, abs=0.01)


def test_a_position_nearest_a_corner_is_measured_on_the_segment_leaving_it():
    # Shape 74588 (route 7919) begins with a 2.6 m stub south, then turns west.
    # On 2026-11-20 the first report of trip 1155435 lay 30 m from the shape,
    # nearest to that corner, moving north at 4.6 m/s. Both segments are then
    # equally near. The distance along came out one rounding step short of the
    # corner's own distance on some machines, which read the stub's heading
    # (south) and dropped the report as moving the wrong way, and exactly at
    # it on others, which read the next segment's (west) and kept it.
    s = Shape.from_latlon(
        [48.463003, 48.46298, 48.46298, 48.462946, 48.462849],
        [-122.567859, -122.56786, -122.56799, -122.568177, -122.568236],
    )
    lat, lon = 48.46284866333008, -122.5675048828125
    d, off = s.project(lat, lon)
    assert d == s.cum[1]  # exactly, not approximately
    assert off == pytest.approx(30.1, abs=0.05)
    assert s.project(lat, lon, 0.0, 300.0)[0] == s.cum[1]
    assert s.heading(d) == pytest.approx(270, abs=0.01)
    assert [p for p in s.passes(lat, lon, 150) if p[0] == s.cum[1]]
    tr = track(s, [(T0, lat, lon, 3.0, 4.61)], T0)
    assert (tr.wrong_direction, len(tr.dists)) == (0, 1)


def _crossing_by_scan(tr, boundary):
    """``crossing`` as it was first written: a scan from the first report."""
    if not tr.dists:
        return None, None, "no_reports"
    if any(a < boundary < b for a, b in tr.skips):
        return None, None, "skipped"
    for k, d in enumerate(tr.dists):
        if d >= boundary:
            if k == 0:
                return None, None, "past_when_first_seen"
            d0, t0, t1 = tr.dists[k - 1], tr.times[k - 1], tr.times[k]
            gap = t1 - t0
            if gap > infer.MAX_GAP_S:
                return None, gap, "gap"
            return t0 + (boundary - d0) / (d - d0) * gap, gap, "observed"
    return None, None, "not_reached"


def test_the_binary_search_finds_the_same_crossing_as_a_scan():
    """Thousands of made-up tracks, with repeated distances, long gaps and
    skipped stretches, and boundaries on, between and beyond the reports."""
    rng = random.Random(20261122)
    for _ in range(3000):
        n = rng.randint(0, 12)
        dists, times, d, t = [], [], rng.uniform(0, 50), 1000.0
        for _ in range(n):
            d += rng.choice([0.0, 0.0, rng.uniform(0, 400)])
            t += rng.choice([20.0, 20.0, 45.0, 400.0])
            dists.append(d)
            times.append(t)
        skips = [(dists[i], dists[i] + 80.0) for i in range(n) if rng.random() < 0.1]
        tr = infer.Track(times=times, dists=dists, skips=skips)
        boundaries = [*dists, *(x + 0.5 for x in dists), *(x - 0.5 for x in dists), -1.0, 1e9]
        for boundary in boundaries:
            assert infer.crossing(tr, boundary) == _crossing_by_scan(tr, boundary)
