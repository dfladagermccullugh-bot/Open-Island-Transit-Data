import io
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest

from pipeline.events import geometry
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
    events, trips = infer_day(DAY, read_zip(gtfs_zip()), vehicle_rows(pts), {("R1", "S1")})
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


def event(sched_min, actual_min, last=False, timepoint=True, route="R1"):
    s = ORIGIN + timedelta(hours=6, minutes=sched_min)
    a = ORIGIN + timedelta(hours=6, minutes=actual_min) if actual_min is not None else None
    key = "arrival" if last else "departure"
    return {"is_last_stop": last, "is_timepoint": timepoint, "route_id": route,
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
    assert s["experimental"] is True
    assert s["by_hour"]["6"]["scheduled"] == 3  # 06:10 to 06:30 Pacific
