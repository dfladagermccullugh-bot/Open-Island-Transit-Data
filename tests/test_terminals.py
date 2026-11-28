"""First and last stops, and relays (events/0.6.0).

Buses lay over in bays a little away from where the schedule puts the
terminal stop, so the 40 m (about 130 feet) rule along the shape misses
them; the terminal rule (``pipeline/events/infer.py``, ``_terminal``) times
the bus entering or leaving a 100 m (about 330 feet) circle around the stop.
A relay is a second bus taking over a trip on the way
(``pipeline/events/continuity.py``, ``relay``).
"""

import io
import zipfile
from datetime import UTC, datetime

import pytest

from pipeline.events.infer import TERMINAL_RADIUS_M, infer_day
from pipeline.geo import meters_between
from pipeline.gtfs.zipread import read_zip
from tests.test_continuity import STOPS, events_of, two_trips_zip
from tests.test_events import DAY, LAT, T0, lon_at

NORTH = 1 / 111_320  # degrees of latitude per meter, near enough


def at(t, meters, north=0.0, trip="T1", vehicle="V1", bearing=90.0, speed=8.0):
    return {"vehicle_key": vehicle, "trip_id": trip, "lat": LAT + north * NORTH,
            "lon": lon_at(meters), "vehicle_ts": datetime.fromtimestamp(T0 + t, UTC),
            "bearing": bearing, "speed": speed}  # fmt: skip


def drive(t0, m0, m1, step_m=200, step_s=20, **kw):
    """Reports every ``step_s`` from ``m0`` to ``m1`` meters, ``step_m`` apart."""
    out, k = [], 0
    while m0 + step_m * k <= m1:
        out.append(at(t0 + step_s * k, m0 + step_m * k, **kw))
        k += 1
    return out


def distance_to(stop, report):
    meters = dict(STOPS)[stop]
    return meters_between(LAT, lon_at(meters), report["lat"], report["lon"])


def test_a_bus_laying_over_short_of_the_last_stop():
    # The bus runs to 5,800 m, then pulls into a bay 60 m north of the road,
    # 50 m short of S3 (about 78 m from it), and waits there.
    run = drive(0, 0, 5800, speed=10.0)
    bay = [at(600 + 20 * k, 5950, north=60, speed=0.0) for k in range(10)]
    events, tracks = infer_day(DAY, two_trips_zip(), run + bay)
    last = events_of(events, "T1")["S3"]
    assert last["arrival_status"] == "observed"
    assert last["report_source"] == "terminal_own"
    # Inside the circle between 580 s (5,800 m, 200 m away) and 600 s.
    t = last["arrival_time"].timestamp() - T0
    assert 580 < t < 600
    assert last["arrival_gap_s"] == 20.0
    track = next(x for x in tracks if x["trip_id"] == "T1")
    assert track["last_report_used"].timestamp() - T0 == 780  # the bay, along the shape


def test_the_old_rule_left_that_arrival_untimed():
    """The 40 m rule along the shape alone: the bay is not reached."""
    run = drive(0, 0, 5800, speed=10.0)
    bay = [at(600 + 20 * k, 5950, north=60, speed=0.0) for k in range(10)]
    events, _ = infer_day(DAY, two_trips_zip(), run + bay)
    last = events_of(events, "T1")["S3"]
    assert distance_to("S3", bay[0]) <= TERMINAL_RADIUS_M
    assert last["stop_distance_m"] - 40 > 5950  # short of the 40 m point


def test_the_next_trips_reports_time_the_arrival():
    # The bus already shows T2 once it is in the bay.
    run = drive(0, 0, 5800, speed=10.0)
    bay = [at(600 + 20 * k, 5950, north=60, trip="T2", speed=0.0) for k in range(10)]
    events, _ = infer_day(DAY, two_trips_zip(), run + bay)
    last = events_of(events, "T1")["S3"]
    assert last["arrival_status"] == "observed"
    assert last["report_source"] == "terminal_borrowed_after"


def test_a_bus_waiting_beyond_the_first_stop():
    # The bus waits 60 m past S0 (beyond the 40 m point, inside the circle),
    # then leaves at 06:00:40.
    wait = [at(-120 + 20 * k, 60, speed=0.0) for k in range(9)]
    run = drive(60, 260, 6000, speed=10.0)
    events, _ = infer_day(DAY, two_trips_zip(), wait + run)
    first = events_of(events, "T1")["S0"]
    assert first["departure_status"] == "observed"
    assert first["report_source"] == "terminal_own"
    t = first["departure_time"].timestamp() - T0
    assert 40 < t < 60


def test_no_terminal_time_without_a_timed_stop_on_the_trip():
    # Seen only in the bay at the end: nothing else of the trip was timed,
    # so nothing ties the bay visit to this trip.
    bay = [at(600 + 20 * k, 5950, north=60, speed=0.0) for k in range(10)]
    approach = [at(560, 5700, north=0), at(580, 5850, north=0)]
    events, _ = infer_day(DAY, two_trips_zip(), approach + bay)
    assert events_of(events, "T1")["S3"]["arrival_status"] != "observed"


def test_no_terminal_time_across_a_long_silence():
    # The bus goes quiet 400 m out and is next seen in the bay 5 minutes later.
    run = drive(0, 0, 5600, speed=10.0)
    bay = [at(860 + 20 * k, 5950, north=60, speed=0.0) for k in range(10)]
    events, _ = infer_day(DAY, two_trips_zip(), run + bay)
    assert events_of(events, "T1")["S3"]["arrival_status"] != "observed"


@pytest.mark.parametrize("handover_s, apart_m, relayed", [(60, 0, True), (400, 0, False),
                                                          (60, 500, False)])  # fmt: skip
def test_a_relay(handover_s, apart_m, relayed):
    # V1 drives T1 to 2,800 m, V2 takes over from there (or 500 m on).
    first = drive(-60, 0, 2800, speed=10.0)
    second = drive(280 + handover_s, 2800 + apart_m, 6000, vehicle="V2", speed=10.0)
    # V2 sends more reports, so it is the chosen vehicle.
    end = 280 + handover_s + 20 * len(second)
    second += [at(end + 20 * k, 6000, vehicle="V2", speed=0.0) for k in range(8)]
    events, tracks = infer_day(DAY, two_trips_zip(), first + second)
    track = next(x for x in tracks if x["trip_id"] == "T1")
    trip = events_of(events, "T1")
    assert track["vehicle_key"] == "V2"
    assert track["vehicles_with_trip_id"] == 2
    if relayed:
        assert track["relay_vehicle_key"] == "V1"
        assert track["reports_relay"] == len(first)
        assert trip["S0"]["departure_status"] == "observed"
        assert trip["S1"]["report_source"] == "relay"
        assert trip["S3"]["report_source"] == "own"
    else:
        assert track["relay_vehicle_key"] is None
        assert track["reports_relay"] == 0
        assert trip["S0"]["departure_status"] != "observed"


def test_two_buses_at_once_are_not_a_relay():
    # V2 shows T1's id while V1 is still driving it: a wrong id, not a relay.
    first = drive(-60, 0, 6000, speed=10.0)
    other = drive(100, 0, 1000, vehicle="V2", speed=10.0)
    _, tracks = infer_day(DAY, two_trips_zip(), first + other)
    track = next(x for x in tracks if x["trip_id"] == "T1")
    assert track["vehicle_key"] == "V1" and track["relay_vehicle_key"] is None


def loop_zip() -> bytes:
    """Out 6,000 m east, then back west on a road 80 m (about 260 feet) north,
    to S3 at 3,000 m: on the way out the bus passes 80 m from S3."""
    north = 80 * NORTH
    stops = [("S0", LAT, lon_at(0)), ("S1", LAT, lon_at(2000)), ("S2", LAT, lon_at(6000)),
             ("S3", LAT + north, lon_at(3000))]  # fmt: skip
    shape = [(LAT, lon_at(0)), (LAT, lon_at(6000)), (LAT + north, lon_at(6000)),
             (LAT + north, lon_at(3000))]  # fmt: skip
    minutes = (0, 4, 10, 15)
    times = [f"T1,06:{m:02d}:00,06:{m:02d}:00,{s},{i + 1}\n"
             for i, ((s, _, _), m) in enumerate(zip(stops, minutes, strict=True))]  # fmt: skip
    files = {
        "agency.txt": "agency_id,agency_name\n1,IT\n",
        "routes.txt": "route_id,route_short_name\nR1,1\n",
        "calendar.txt": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\nWK,1,1,1,1,1,0,0,20260101,20271231\n"
        ),
        "trips.txt": "route_id,service_id,trip_id,shape_id\nR1,WK,T1,SH\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        + "".join(f"SH,{a},{b},{i}\n" for i, (a, b) in enumerate(shape)),
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(f"{s},{s},{a},{b}\n" for s, a, b in stops),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        + "".join(times),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return read_zip(buf.getvalue())


def test_a_pass_by_the_last_stop_on_the_way_out_is_not_the_arrival():
    # Out at 10 m/s, silent for 3 minutes around S2 (so S2 has no time), back
    # along the north road and into a bay 50 m short of S3, 50 m north of it.
    out = [
        r
        for r in drive(0, 0, 6000, speed=10.0)
        if not 500 <= r["vehicle_ts"].timestamp() - T0 <= 680
    ]
    back = [at(620 + 20 * k, 6000 - 200 * k, north=80, bearing=270.0, speed=10.0)
            for k in range(1, 15)]  # fmt: skip
    bay = [at(920 + 20 * k, 3050, north=130, speed=0.0) for k in range(6)]
    events, _ = infer_day(DAY, loop_zip(), out + back + bay)
    trip = events_of(events, "T1")
    assert trip["S2"]["departure_status"] != "observed"
    last = trip["S3"]
    assert last["arrival_status"] == "observed" and last["report_source"] == "terminal_own"
    # Not about 290 s, when the bus passed 80 m from S3 on the way out.
    assert last["arrival_time"].timestamp() - T0 > 880
