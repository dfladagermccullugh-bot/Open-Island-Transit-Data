"""Following the physical bus across trip ids (pipeline/events/continuity.py)."""

import io
import zipfile
from datetime import UTC, datetime

from pipeline.events.infer import infer_day
from pipeline.gtfs.zipread import read_zip
from tests.test_events import DAY, LAT, T0, lon_at

STOPS = [(f"S{i}", d) for i, d in enumerate((0, 2000, 4000, 6000))]


def two_trips_zip(second_start: int = 30) -> bytes:
    """T1 leaves S0 at 06:00 and reaches S3 at 06:12; T2 runs the same way
    from 06:``second_start``. Between them the bus drives back empty."""
    times = []
    for trip, start in (("T1", 0), ("T2", second_start)):
        for i, ((s, _), m) in enumerate(zip(STOPS, (0, 4, 8, 12), strict=True)):
            times.append(f"{trip},06:{start + m:02d}:00,06:{start + m:02d}:00,{s},{i + 1}\n")
    files = {
        "agency.txt": "agency_id,agency_name\n1,IT\n",
        "routes.txt": "route_id,route_short_name\nR1,1\n",
        "calendar.txt": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\nWK,1,1,1,1,1,0,0,20260101,20271231\n"
        ),
        "trips.txt": "route_id,service_id,trip_id,shape_id\nR1,WK,T1,SH\nR1,WK,T2,SH\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        + "".join(f"SH,{LAT},{lon_at(d)},{i}\n" for i, d in enumerate((0, 3000, 6000))),
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(f"{s},{s},{LAT},{lon_at(d)}\n" for s, d in STOPS),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        + "".join(times),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return read_zip(buf.getvalue())


def row(t, meters, trip="T1", vehicle="V1", bearing=90.0, speed=8.0):
    return {"vehicle_key": vehicle, "trip_id": trip, "lat": LAT, "lon": lon_at(meters),
            "vehicle_ts": datetime.fromtimestamp(T0 + t, UTC), "bearing": bearing,
            "speed": speed}  # fmt: skip


def run(start_s: float, trip: str = "T1", vehicle: str = "V1", wait_s: int = 60, **kw):
    """A run along the whole shape at 10 m/s, waiting ``wait_s`` at the
    start, a report every 20 s, the trip id given by ``trip`` (a function of
    meters, or a string)."""
    name = trip if callable(trip) else (lambda _m: trip)
    out = [row(start_s + t, 0, name(0), vehicle, speed=0.0) for t in range(0, wait_s + 1, 20)]
    for k in range(1, 31):
        m = 200 * k
        out.append(row(start_s + wait_s + 20 * k, m, name(m), vehicle, **kw))
    return out


def events_of(events, trip):
    return {e["stop_id"]: e for e in events if e["trip_id"] == trip}


def test_a_bus_that_shows_its_next_trip_before_the_last_stop():
    # From 5,000 m on, the bus already shows T2's id.
    vehicles = run(-60, trip=lambda m: "T2" if m >= 5000 else "T1") + run(1740, trip="T2")
    events, tracks = infer_day(DAY, two_trips_zip(), vehicles)
    last = events_of(events, "T1")["S3"]
    assert last["arrival_status"] == "observed"
    assert last["report_source"] == "borrowed_after"
    t1 = next(t for t in tracks if t["trip_id"] == "T1")
    assert t1["reports_borrowed_after"] > 0
    t2 = events_of(events, "T2")
    assert t2["S0"]["departure_status"] == "observed"
    assert t2["S0"]["report_source"] == "own"


def test_without_continuity_that_last_stop_was_lost():
    """The same bus with T2's reports taken away: the old rule, trip id only."""
    vehicles = [v for v in run(-60, trip=lambda m: "T2" if m >= 5000 else "T1")
                if v["trip_id"] == "T1"]  # fmt: skip
    events, _ = infer_day(DAY, two_trips_zip(), vehicles)
    assert events_of(events, "T1")["S3"]["arrival_status"] == "not_reached"


def test_a_bus_that_keeps_the_previous_trip_id_at_the_first_stop():
    # T1 ends at 06:11; the bus is driven back and waits at S0 still
    # showing T1, and only shows T2 once it has left.
    back = [row(700 + 40 * k, 6000 - 400 * k, "T1", bearing=270.0) for k in range(1, 16)]
    wait = [row(t, 0, "T1", speed=0.0) for t in range(1700, 1801, 20)]
    leave = [row(1800 + 20 * k, 200 * k, "T2") for k in range(1, 31)]
    vehicles = run(-60) + back + wait + leave
    events, tracks = infer_day(DAY, two_trips_zip(), vehicles)
    first = events_of(events, "T2")["S0"]
    assert first["departure_status"] == "observed"
    assert first["report_source"] == "borrowed_before"
    assert next(t for t in tracks if t["trip_id"] == "T2")["reports_borrowed_before"] > 0


def test_a_stretch_with_no_trip_id_is_bridged():
    vehicles = run(-60, trip=lambda m: None if 1600 <= m <= 2400 else "T1")
    events, tracks = infer_day(DAY, two_trips_zip(), vehicles)
    s1 = events_of(events, "T1")["S1"]
    assert s1["arrival_status"] == "observed" and s1["departure_status"] == "observed"
    assert s1["report_source"] == "borrowed_within"
    assert next(t for t in tracks if t["trip_id"] == "T1")["reports_borrowed_within"] == 5


def test_the_drive_from_the_depot_is_not_taken_for_the_trip():
    # Before T1, the bus drives the whole route empty with no trip id,
    # from 05:40 (inside the 30-minute window), then turns back to S0.
    deadhead = [row(-1200 + 20 * k, 200 * k, None) for k in range(1, 31)]
    back = [row(-560 + 20 * k, 6000 - 600 * k, None, bearing=270.0) for k in range(1, 11)]
    vehicles = deadhead + back + run(-180, wait_s=180)
    events, _ = infer_day(DAY, two_trips_zip(), vehicles)
    e = events_of(events, "T1")
    # The departure is the real one, at about 06:00:04, not the empty run.
    assert abs((e["S0"]["departure_time"].timestamp() - T0) - 4) < 5
    assert e["S1"]["arrival_time"].timestamp() > T0


def test_a_duplicate_trip_id_keeps_the_bus_with_more_reports():
    vehicles = run(-60) + run(-60, vehicle="V2")[:5]
    _, tracks = infer_day(DAY, two_trips_zip(), vehicles)
    t1 = next(t for t in tracks if t["trip_id"] == "T1")
    assert t1["vehicles_with_trip_id"] == 2 and t1["vehicle_key"] == "V1"


def test_a_trip_the_bus_did_not_drive_does_not_borrow_its_next_run():
    """November 25, 2026, vehicle 20386, in miniature: the bus shows T1 while
    parked far from the route, then drives T2. T1 must not be timed with
    T2's run."""
    parked = [row(t, 9000, "T1", speed=0.0) for t in range(0, 900, 20)]
    vehicles = parked + run(1740, trip="T2")
    events, _ = infer_day(DAY, two_trips_zip(), vehicles)
    t1 = events_of(events, "T1")
    assert all(e["arrival_time"] is None and e["departure_time"] is None for e in t1.values())
    assert events_of(events, "T2")["S3"]["arrival_status"] == "observed"


def test_a_bus_arriving_along_the_road_it_leaves_by_is_timed_leaving():
    """Its first report near the start lies 250 m along the shape, on the
    way in; tracking must start where it waited, or the departure is lost."""
    arrive = [row(-200 + 20 * k, 300 - 50 * k, "T1", bearing=270.0) for k in range(1, 6)]
    vehicles = arrive + run(-60)
    events, _ = infer_day(DAY, two_trips_zip(), vehicles)
    assert events_of(events, "T1")["S0"]["departure_status"] == "observed"
