import io
import zipfile
from datetime import UTC, date, datetime, timedelta

from pipeline.canceled import canceled_trips
from pipeline.gtfs.schedule import ServiceWindows, Trip, day_origin, scheduled_trips
from pipeline.gtfs.zipread import read_zip
from pipeline.quality.metrics import (
    VEHICLE_FIELDS,
    coverage,
    day_metrics,
    feed_emptiness,
    measurement,
    paradata,
    refresh,
    trip_gaps,
)
from pipeline.stats import quantile

DAY = date(2026, 11, 20)  # a Friday, standard time
T = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)  # 8:00 AM Pacific


def gtfs(calendar_dates: str = "") -> bytes:
    files = {
        "agency.txt": "agency_id,agency_name\n1,IT\n",
        "stops.txt": "stop_id,stop_name\nS1,a\nS2,b\n",
        "routes.txt": "route_id\nR1\nR2\n",
        "trips.txt": "route_id,service_id,trip_id\nR1,WK,A\nR1,WK,B\nR2,WE,C\n",
        "stop_times.txt": (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "A,08:00:00,08:00:00,S1,1\nA,08:30:00,08:30:00,S2,2\n"
            "B,09:00:00,09:00:00,S1,1\nB,25:10:00,25:10:00,S2,2\n"
            "C,10:00:00,10:00:00,S1,1\nC,10:30:00,10:30:00,S2,2\n"
        ),
        "calendar.txt": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\nWK,1,1,1,1,1,0,0,20260101,20271231\n"
            "WE,0,0,0,0,0,1,1,20260101,20271231\n"
        ),
    }
    if calendar_dates:
        files["calendar_dates.txt"] = "service_id,date,exception_type\n" + calendar_dates
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def test_scheduled_trips_weekday_and_after_midnight():
    trips = scheduled_trips(read_zip(gtfs()), DAY)
    assert [t.trip_id for t in trips] == ["A", "B"]
    assert trips[0].start == T
    assert trips[1].end == datetime(2026, 11, 21, 9, 10, tzinfo=UTC)  # 25:10 = 1:10 AM next day


def test_calendar_dates_add_and_remove():
    z = read_zip(gtfs("WK,20261120,2\nWE,20261120,1\n"))
    assert [t.trip_id for t in scheduled_trips(z, DAY)] == ["C"]


def test_day_origin_across_clock_changes():
    assert day_origin(date(2027, 3, 14)) == datetime(2027, 3, 14, 7, 0, tzinfo=UTC)
    assert day_origin(date(2027, 11, 7)) == datetime(2027, 11, 7, 8, 0, tzinfo=UTC)
    # On the spring-forward day, GTFS 08:00 is 8 hours after 11 PM PST, i.e. 8 AM PDT.
    assert day_origin(date(2027, 3, 14)) + timedelta(hours=8) == datetime(
        2027, 3, 14, 15, 0, tzinfo=UTC
    )


def trip(tid, start_h, end_h, route="R1"):
    return Trip(tid, route, "WK", T + timedelta(hours=start_h), T + timedelta(hours=end_h))


def test_service_windows_merge_overlaps():
    w = ServiceWindows([trip("A", 0, 1), trip("B", 0.5, 2), trip("C", 3, 4)])
    assert len(w.starts) == 2
    assert T + timedelta(hours=1.5) in w
    assert T + timedelta(hours=2.5) not in w
    assert T - timedelta(seconds=1) not in w


def test_quantile_matches_linear_interpolation():
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([10], 0.95) == 10
    assert quantile([], 0.5) is None


def fetch(i, feed="vehiclePositions", at=None, ok=True, entities=5, newest_age=5):
    at = at or T + timedelta(seconds=20 * i)
    return {
        "fetch_id": i,
        "feed": feed,
        "scheduled_at": at,
        "requested_at": at,
        "latency_ms": 300,
        "http_status": 200 if ok else None,
        "error_class": None if ok else "timeout",
        "entity_count": entities if ok else None,
        "newest_entity_ts": at - timedelta(seconds=newest_age) if ok else None,
        "collector_version": "collect/0.2.0",
    }


def test_paradata_counts_slots_from_collection_start():
    start = datetime(2026, 11, 21, 7, 0, tzinfo=UTC)  # 1 hour before the day ends
    fetches = [fetch(i, at=start + timedelta(seconds=20 * i)) for i in range(150)]
    fetches.append(fetch(150, at=start + timedelta(seconds=3000), ok=False))
    p = paradata(fetches, DAY, start)
    vp = p["vehiclePositions"]
    assert vp["slots_expected"] == 180
    assert (vp["slots_logged"], vp["slots_ok"]) == (151, 150)
    assert vp["not_logged"] == 29
    assert vp["uptime"] == round(150 / 180, 6)
    assert p["serviceAlerts"]["slots_expected"] == 12


def test_empty_and_stale_only_count_in_service():
    w = ServiceWindows([trip("A", 0, 1)])
    fetches = [
        fetch(1, entities=0),  # empty, in service
        fetch(2, newest_age=300),  # stale, in service
        fetch(3),  # fine
        fetch(4, ok=False),  # failed: not in the denominator
        fetch(5, at=T + timedelta(hours=2), entities=0),  # empty, out of service
    ]
    e = feed_emptiness(fetches, w)
    assert e["ok_in_service_fetches"] == 3
    assert e["empty_in_service"] == 1
    assert e["stale_in_service"] == 1
    assert e["empty_in_service_rate"] == round(1 / 3, 6)
    assert e["by_hour"]["8"]["attempted"] == 4


def report(vehicle, ts_s, lat=48.2, lon=-122.6, trip="A", seen_after=3, fid=None):
    ts = T + timedelta(seconds=ts_s)
    return {
        **dict.fromkeys(VEHICLE_FIELDS),
        "vehicle_key": vehicle,
        "vehicle_ts": ts,
        "trip_id": trip,
        "lat": lat,
        "lon": lon,
        "first_fetch_id": fid if fid is not None else ts_s,
        "first_requested_at": ts + timedelta(seconds=seen_after),
    }


def test_trip_gaps():
    vehicles = [report("V", 0), report("V", 15), report("V", 30), report("V", 150)]
    g = trip_gaps(vehicles, [trip("A", 0, 1)])
    assert g["longest_gap_s_median"] == 120
    assert g["trips_with_gap_over_90s"] == 1
    assert g["share_of_trip_time_in_gaps_mean"] == round(120 / 150, 4)


def test_measurement():
    vehicles = [
        report("V", 0, lat=48.2000),
        report("V", 15, lat=48.2010),  # 111 m in 15 s: 27 km/h
        report("V", 30, lat=48.2200),  # 2.1 km in 15 s: 507 km/h
        report("V", 20, lat=48.2200, fid=40),  # timestamp goes backwards
        report("W", 0, seen_after=-2),  # timestamp in the future
    ]
    m = measurement(vehicles)
    assert m["impossible_jumps_over_130_kmh"] == 1
    assert m["timestamps_going_backwards"] == 1
    assert m["timestamps_in_future"] == 1
    assert m["position_age_s_median"] == 3


def test_coverage_and_over_coverage():
    trips = [trip("A", 0, 1), trip("B", 1, 2), trip("C", 0, 1, route="R2")]
    vehicles = [report("V", 0, trip="A"), report("V", 15, trip=None), report("W", 0, trip="Z"),
                report("X", 0, trip="Q")]  # fmt: skip
    c = coverage(vehicles, trips, {"A", "B", "C", "Q"}, {"A", "B", "C"})
    assert c["trip_coverage"] == round(1 / 3, 6)
    assert c["routes_never_reporting"] == ["R2"]
    assert c["over_coverage"]["no_trip_id"] == 1
    assert c["over_coverage"]["trip_id_not_in_static"] == 1
    assert c["over_coverage"]["trip_not_scheduled_today"] == 1


def test_day_metrics_ignores_trips_before_collection():
    trips = [trip("A", -2, -1), trip("B", 0, 1)]
    vehicles = [report("V", 0, trip="B")]
    m = day_metrics(
        DAY, [fetch(1)], vehicles, trips, {"trips": {"A", "B"}, "routes": {"R1"}, "stops": set()},
        collection_start=T - timedelta(minutes=30),
    )  # fmt: skip
    assert m["scheduled_service"]["trips_ended_before_collection"] == 1
    assert m["coverage"]["trip_coverage"] == 1.0
    assert m["processing"]["id_match"]["route_id"]["present"] == 0


def test_trip_records_count_trips_in_service():
    trips = [trip("A", 0, 2), trip("B", 1, 3), trip("C", 2, 4, route="R2")]
    vehicles = [report("V", 0, trip="A")]
    c = coverage(vehicles, trips, {"A", "B", "C"})
    rows = [dict(zip(c["trips"]["columns"], r, strict=True)) for r in c["trips"]["rows"]]
    assert [r["scheduled_in_service"] for r in rows] == [1, 2, 2]  # A ends as C starts
    assert [r["observed"] for r in rows] == [True, False, False]


def test_a_trip_id_seen_hours_from_its_schedule_does_not_count_as_observed():
    trips = [trip("A", 0, 1), trip("B", 5, 6)]
    # B's id shows 4 hours before B runs (a bus at the terminal, or yesterday's run).
    vehicles = [report("V", 0, trip="A"), report("V", 3600, trip="B")]
    c = coverage(vehicles, trips, {"A", "B"})
    assert c["observed_scheduled_trips"] == 1 and c["trip_coverage"] == 0.5
    assert c["trips_seen_only_outside_window"] == 1
    # 29 minutes before the trip is inside the window.
    near = [report("V", 5 * 3600 - 29 * 60, trip="B")]
    assert coverage(near, trips, {"A", "B"})["observed_scheduled_trips"] == 1


def test_trip_gaps_use_one_vehicle_inside_the_window():
    # Two buses carry the id; merged, their reports would hide the 120 s gap.
    vehicles = [report("V", 0), report("V", 30), report("V", 150), report("W", 60), report("W", 90)]
    vehicles.append(report("V", 9 * 3600))  # hours later: not part of the trip
    g = trip_gaps(vehicles, [trip("A", 0, 1)])
    assert g["longest_gap_s_median"] == 120


def test_an_empty_fetch_is_not_also_stale():
    w = ServiceWindows([trip("A", 0, 1)])
    empty = fetch(1, entities=0)
    empty["newest_entity_ts"] = None  # an empty feed has no vehicle timestamp
    e = feed_emptiness([empty, fetch(2), fetch(3, newest_age=300)], w)
    assert (e["empty_in_service"], e["stale_in_service"]) == (1, 1)
    assert e["stale_in_service_rate"] == round(1 / 3, 6)


def test_the_same_report_in_two_runs_is_counted_once_at_its_first_fetch():
    from pipeline.quality.metrics import distinct_reports

    first = report("V", 0, seen_after=3, fid=1)
    again = report("V", 0, seen_after=63, fid=4)  # the run was split by a failed fetch
    other = report("V", 20, seen_after=5, fid=5)
    reports = distinct_reports([again, other, first])
    assert [r["first_fetch_id"] for r in reports] == [1, 5]
    assert measurement(reports)["position_age_s_p95"] < 6


def test_uptime_slots_follow_the_scheduler_phase_and_stay_within_one():
    # Ticks fire about 19.9 s into each 20 s step, jittering either side of a
    # slot edge. Slots on a fixed grid would put two ticks in one slot and
    # none in the next; uptime must still be 1 and never more.
    start = datetime(2026, 11, 21, 7, 0, tzinfo=UTC)
    fetches = [
        fetch(i, at=start + timedelta(seconds=20 * i + 19.9 + (0.2 if i % 2 else -0.2)))
        for i in range(180)
    ]
    # A tick logged in this day but belonging to the next day's first slot.
    fetches.append(fetch(999, at=start + timedelta(seconds=3600 + 19.9)))
    vp = paradata(fetches, DAY, start)["vehiclePositions"]
    assert vp["slots_expected"] == 180
    assert (vp["slots_ok"], vp["not_logged"], vp["uptime"]) == (180, 0, 1.0)


# --- canceled trips -----------------------------------------------------------------


def test_a_flagged_trip_is_counted_and_set_aside_in_the_second_figure():
    trips = [trip("A", 0, 1), trip("B", 1, 2), trip("C", 2, 3, route="R2")]
    vehicles = [report("V", 0, trip="A")]
    updates = [
        {"trip_id": "B", "trip_schedule_relationship": "CANCELED"},
        {"trip_id": "A", "trip_schedule_relationship": None},
        {"trip_id": "Z", "trip_schedule_relationship": "CANCELED"},  # not scheduled today
    ]
    canceled = canceled_trips(updates, [], trips)
    assert canceled == {"B": "flag"}
    c = coverage(vehicles, trips, {"A", "B", "C"}, canceled=canceled)
    assert c["trip_coverage"] == round(1 / 3, 6)  # the headline figure does not move
    assert c["canceled_trips"] == 1 and c["canceled_by"] == {"flag": 1, "alert": 0}
    assert c["canceled_trip_ids"] == ["B"]
    assert c["trip_coverage_excluding_canceled"] == 0.5
    assert c["by_route"]["R1"]["canceled"] == 1 and c["by_route"]["R2"]["canceled"] == 0
    rows = [dict(zip(c["trips"]["columns"], r, strict=True)) for r in c["trips"]["rows"]]
    assert [r["canceled"] for r in rows] == [False, True, False]


def test_without_cancellations_the_two_figures_agree():
    trips = [trip("A", 0, 1), trip("B", 1, 2)]
    c = coverage([report("V", 0, trip="A")], trips, {"A", "B"})
    assert c["canceled_trips"] == 0 and c["canceled_but_observed"] == 0
    assert c["trip_coverage_excluding_canceled"] == c["trip_coverage"] == 0.5


def test_a_flagged_trip_that_ran_stays_observed():
    trips = [trip("A", 0, 1), trip("B", 1, 2)]
    canceled = canceled_trips(
        [{"trip_id": "A", "trip_schedule_relationship": "CANCELED"}], [], trips
    )
    c = coverage([report("V", 0, trip="A")], trips, {"A", "B"}, canceled=canceled)
    assert c["canceled_trips"] == 1 and c["canceled_but_observed"] == 1
    assert c["trip_coverage_excluding_canceled"] == 0.5  # nothing unobserved was canceled


def test_an_alert_cancels_a_trip_only_while_it_is_active():
    trips = [trip("A", 0, 1), trip("B", 5, 6)]

    def alert(effect, start_h, end_h, trip_ids=("A", "B")):
        period = {"start": T + timedelta(hours=start_h), "end": T + timedelta(hours=end_h)}
        return {"effect": effect, "trip_ids": list(trip_ids), "active_periods": [period]}

    assert canceled_trips([], [alert("NO_SERVICE", -1, 2)], trips) == {"A": "alert"}
    assert canceled_trips([], [alert("DETOUR", -1, 9)], trips) == {}
    # No period means the alert applies for as long as it is shown.
    always = {"effect": "NO_SERVICE", "trip_ids": ["B"], "active_periods": []}
    assert canceled_trips([], [always], trips) == {"B": "alert"}
    # An alert that names only a route does not cancel a trip.
    route_only = {"effect": "NO_SERVICE", "trip_ids": [], "route_ids": ["R1"], "active_periods": []}
    assert canceled_trips([], [route_only], trips) == {}
    # A flag outranks an alert for the same trip.
    flagged = [{"trip_id": "B", "trip_schedule_relationship": "DELETED"}]
    assert canceled_trips(flagged, [always], trips) == {"B": "flag"}


# --- how often trip updates change --------------------------------------------------


def test_refresh_counts_changes_between_consecutive_fetches():
    def run(key, first_s, seen, stamp_s):
        first = T + timedelta(seconds=first_s)
        return {
            "trip_key": key,
            "trip_update_ts": T + timedelta(seconds=stamp_s),
            "first_requested_at": first,
            "last_requested_at": first + timedelta(seconds=20 * (seen - 1)),
            "times_seen": seen,
        }

    runs = [
        run("A", 0, 3, 0),  # seen at 0, 20, 40 s unchanged ...
        run("A", 60, 3, 58),  # ... changed at the next fetch, then unchanged twice
        run("A", 120, 1, 121),  # changed again
        run("A", 600, 2, 590),  # back after a gap: not a pair of consecutive fetches
        run("B", 0, 2, 1),
    ]
    r = refresh(runs)
    # Pairs of consecutive fetches listing the same trip: A 2+1+2+1+0+1, B 1.
    assert (r["pairs"], r["changed"]) == (8, 2)
    assert r["changed_share"] == 0.25
    assert r["update_interval_s_median"] == 60.5  # 58 s and 63 s between timestamps
    assert refresh([])["changed_share"] is None
