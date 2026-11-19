from pipeline.gtfs.crosswalk import route_crosswalk, stop_crosswalk, summarize, timepoints


def stop(stop_id, code, lat, lon, name="x"):
    return {"stop_id": stop_id, "stop_code": code, "stop_name": name, "stop_lat": str(lat),
            "stop_lon": str(lon), "location_type": ""}  # fmt: skip


# 0.0001 degrees of latitude is about 11 m.
TRILLIUM_STOPS = [
    stop("T1", "100", 48.2000, -122.6),
    stop("T2", "200", 48.2100, -122.6),
    stop("T3", "", 48.2200, -122.6),
    stop("T9", "300", 48.5000, -122.6),  # same code as a Passio stop, 30 km away
]


def by_id(rows):
    return {r["passio_stop_id"]: r for r in rows}


def test_stop_matching_rules():
    passio = [
        stop("P1", "100", 48.2001, -122.6),  # code, 11 m
        stop("P2", "", 48.2103, -122.6),  # no code, 33 m -> distance
        stop("P3", "", 48.2210, -122.6),  # no code, 111 m -> unmatched
        stop("P4", "300", 48.2201, -122.6),  # code is 30 km away -> distance to T3
        stop("P5", "999", 48.3000, -122.6),  # unknown code, far from all
    ]
    out = by_id(stop_crosswalk(passio, TRILLIUM_STOPS))
    assert (out["P1"]["trillium_stop_id"], out["P1"]["method"]) == ("T1", "stop_code")
    assert (out["P2"]["trillium_stop_id"], out["P2"]["method"]) == ("T2", "distance")
    assert out["P2"]["distance_m"] == 33.4
    assert (out["P3"]["trillium_stop_id"], out["P3"]["method"]) == ("", "")
    assert out["P3"]["nearest_trillium_stop_id"] == "T3"
    assert (out["P4"]["trillium_stop_id"], out["P4"]["method"]) == ("T3", "distance")
    assert out["P5"]["method"] == ""


def test_stations_are_ignored():
    station = {**stop("S1", "100", 48.2, -122.6), "location_type": "1"}
    out = stop_crosswalk([stop("P1", "100", 48.2, -122.6)], [station, *TRILLIUM_STOPS])
    assert out[0]["trillium_stop_id"] == "T1"


def test_route_matching():
    passio = [
        {"route_id": "p1", "route_short_name": "1"},
        {"route_id": "pa", "route_short_name": ""},
        {"route_id": "pb", "route_short_name": ""},
        {"route_id": "p7", "route_short_name": "7"},
    ]
    trillium = [
        {"route_id": "t1", "route_short_name": "1"},
        {"route_id": "tn", "route_short_name": ""},
    ]
    out = {r["passio_route_id"]: r for r in route_crosswalk(passio, trillium)}
    assert out["p1"]["trillium_route_id"] == "t1"
    assert out["pa"]["trillium_route_id"] == out["pb"]["trillium_route_id"] == "tn"
    assert out["pa"]["method"] == "no short name in either feed"
    assert out["p7"]["trillium_route_id"] == ""


def cal(service_id, pattern):
    days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    return {"service_id": service_id, **dict(zip(days, pattern, strict=True))}


def st(trip, seq, stop_id, time, timepoint=""):
    return {"trip_id": trip, "stop_sequence": str(seq), "stop_id": stop_id,
            "departure_time": time, "arrival_time": time, "timepoint": timepoint}  # fmt: skip


def test_timepoints_and_agreement():
    passio = {
        "trips": [
            {"trip_id": "a", "route_id": "p1", "service_id": "WK"},
            {"trip_id": "b", "route_id": "p1", "service_id": "SA"},
        ],
        "calendar": [cal("WK", "1111100"), cal("SA", "0000010")],
        "stop_times": [
            st("a", 1, "P1", "08:00:00", "1"),
            st("a", 2, "P2", "08:05:00", "1"),
            st("a", 3, "P3", "08:09:00", "1"),
            st("b", 1, "P1", "09:00:00", "1"),
        ],
    }
    trillium = {
        "trips": [{"trip_id": "x", "route_id": "t1", "service_id": "WK"}],
        "calendar": [cal("WK", "1111100")],
        "stop_times": [
            st("x", 1, "T1", "08:00:00", "1"),
            st("x", 2, "T2", "08:06:00", "0"),
            st("x", 3, "T3", "08:10:00", "1"),
        ],
    }
    stops = [
        {"passio_stop_id": "P1", "trillium_stop_id": "T1", "method": "stop_code"},
        {"passio_stop_id": "P2", "trillium_stop_id": "T2", "method": "stop_code"},
        {"passio_stop_id": "P3", "trillium_stop_id": "T3", "method": "stop_code"},
    ]
    routes = [{"passio_route_id": "p1", "trillium_route_id": "t1", "method": "route_short_name"}]
    tp_rows, agreement = timepoints(passio, trillium, stops, routes)
    flags = {r["passio_stop_id"]: r["is_timepoint"] for r in tp_rows}
    assert flags == {"P1": True, "P2": False, "P3": True}
    diffs = {(a["passio_trip_id"], a["passio_stop_id"]): a["nearest_trillium_minus_passio_s"]
             for a in agreement}  # fmt: skip
    # Saturday trip b has no Trillium timepoint on a Saturday.
    assert diffs == {("a", "P1"): 0, ("a", "P3"): 60, ("b", "P1"): None}

    summary = summarize(stops, routes, tp_rows, agreement)
    assert summary["timepoints"]["true_timepoint_route_stops"] == 2
    assert summary["schedule_agreement"] == {
        "passio_stop_times_at_timepoints": 3,
        "same_time": 1,
        "within_60_s": 2,
        "within_300_s": 2,
        "no_trillium_time_same_weekday": 1,
    }
