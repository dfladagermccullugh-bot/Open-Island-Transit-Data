from datetime import UTC, datetime, timedelta

from pipeline.events.validate import feed_agreement, travel_times

T0 = datetime(2026, 11, 19, 22, 0, tzinfo=UTC)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def ev(seq, dist, arr=None, dep=None, first=False, last=False, status="observed", trip="A"):
    return {
        "trip_id": trip, "route_id": "R1", "vehicle_key": "V1", "stop_sequence": seq,
        "stop_distance_m": dist, "is_first_stop": first, "is_last_stop": last,
        "arrival_time": at(arr) if arr is not None else None,
        "departure_time": at(dep) if dep is not None else None,
        "arrival_status": status if arr is not None or status == "skipped" else "gap",
        "departure_status": status if dep is not None or status == "skipped" else "gap",
        "arrival_gap_s": 20.0, "departure_gap_s": 20.0,
    }  # fmt: skip


def veh(t, seq, trip="A"):
    return {"trip_id": trip, "vehicle_key": "V1", "vehicle_ts": at(t),
            "current_stop_sequence": seq}  # fmt: skip


def test_feed_agreement_brackets_the_change_of_stop_sequence():
    vehicles = [veh(0, 2), veh(20, 2), veh(40, 3), veh(60, 3), veh(80, 4), veh(100, 3)]
    events = [
        ev(1, 0, dep=5, first=True),  # first stop: left out
        ev(2, 300, arr=25, dep=30),  # change between 20 and 40: inside
        ev(3, 600, arr=60, dep=15),  # change between 60 and 80: 45 s early, outside tolerance
        ev(4, 900, arr=95, last=True),  # last stop: left out
    ]
    out = feed_agreement(events, vehicles)
    assert out["compared"] == 2
    assert out["inside_change_interval_share"] == 0.5
    assert out["within_tolerance_share"] == 0.5
    assert out["feed_change_after_departure_s"]["median"] == 37.5  # (10 + 65) / 2
    assert out["feed_sequence_going_backwards"] == 1


def test_travel_times_flags_fast_segments_and_ignores_skipped_stretches():
    events = [
        ev(1, 0, dep=0, first=True),
        ev(2, 1000, arr=60, dep=70),  # 920 m in 60 s: fine
        ev(3, 3000, status="skipped"),
        ev(4, 9000, arr=120, dep=130),  # across the skipped stop: not measured
        ev(5, 9500, arr=135, last=True),  # 420 m in 5 s: too fast
    ]
    out = travel_times(events)
    assert out["negative"] == 0
    assert out["across_skipped_stretch"] == 1
    assert out["over_max_speed"] == 1
    assert out["fastest"][0]["from"] == "departure 4" and out["fastest"][0]["to"] == "arrival 5"
    # American spelling in what is published, and the distance is still there.
    assert out["fastest"][0]["meters"] == 420 and "metres" not in out["fastest"][0]
