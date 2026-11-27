from datetime import UTC, date, datetime, timedelta

from pipeline.quality.stops import (
    assignment,
    jitter,
    missing_vehicles,
    prediction_error,
    shape_fit,
    stop_coverage,
)

T0 = datetime(2026, 11, 19, 20, 0, tzinfo=UTC)  # 12:00 Pacific


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def event(seq, sched_min, dep_min=None, arr_min=None, first=False, last=False, tp=True, **kw):
    row = {
        "trip_id": "A",
        "route_id": "R1",
        "stop_sequence": seq,
        "is_first_stop": first,
        "is_last_stop": last,
        "is_timepoint": tp,
        "scheduled_arrival": at(sched_min),
        "scheduled_departure": at(sched_min),
        "arrival_time": at(arr_min) if arr_min is not None else None,
        "departure_time": at(dep_min) if dep_min is not None else None,
        "arrival_status": "observed" if arr_min is not None else "gap",
        "departure_status": "observed" if dep_min is not None else "not_reached",
    }
    row.update(kw)
    return row


def test_stop_coverage_counts_measured_time_and_reasons():
    events = [
        event(1, 0, dep_min=1, first=True),
        event(2, 10, arr_min=10, tp=False),  # departure missing: not observed
        event(3, 20, arr_min=21, last=True),  # last stop: arrival counts
        event(4, -600, dep_min=-600),  # before collection: left out
    ]
    out = stop_coverage(events, T0 - timedelta(hours=1))
    assert out["timepoints"]["scheduled"] == 2
    assert out["timepoints"]["coverage"] == 1.0
    assert out["all_stops"]["scheduled"] == 3
    assert out["all_stops"]["observed"] == 2
    assert out["all_stops"]["not_observed_by_reason"] == {"not_reached": 1}


def track(route, reports, off, wrong, used=None, vehicles=1, skips=0, before=0, after=0):
    return {
        "reports_before_start": before,
        "reports_after_end": after,
        "shape_skips": skips,
        "skipped_m": 1500.0 * skips,
        "route_id": route,
        "position_reports": reports,
        "off_shape_reports": off,
        "wrong_direction_reports": wrong,
        "reports_used": reports - off - wrong if used is None else used,
        "vehicles_with_trip_id": vehicles,
    }


def test_shape_fit_and_assignment():
    tracks = [track("R1", 100, 10, 2, skips=2), track("R2", 0, 0, 0)]
    tracks.append(track("R2", 50, 50, 0, vehicles=2))
    fit = shape_fit(tracks)
    assert fit["trips_skipping_part_of_shape"] == 1 and fit["skipped_km"] == 3.0
    assert fit["off_shape"] == 60
    assert fit["off_shape_share"] == 0.4
    assert fit["by_route"]["R2"]["off_shape_share"] == 1.0
    # Reports never compared with the shape are not in the denominator.
    waiting = shape_fit([track("R1", 100, 10, 0, before=40, after=10)])
    assert waiting["tracked_reports"] == 50 and waiting["off_shape_share"] == 0.2
    # A trip that could not be read is counted, not added in.
    broken = {**track("R1", 5, 0, 0), "reports_used": None, "problem": "KeyError: 'S2'"}
    assert shape_fit([broken])["trips_not_readable"] == 1
    vehicles = [{"trip_id": t} for t in ("A", "B", "X", None)]
    a = assignment(vehicles, tracks, static_trip_ids={"A", "B", "C"}, todays_trip_ids={"A", "C"})
    assert a["observed_trip_ids"] == 3
    assert a["trip_id_not_in_static"] == ["X"]
    assert a["trip_not_scheduled_today"] == ["B"]
    assert a["unassignable"] == 2
    assert a["trips_with_no_usable_report"] == 1
    assert a["trips_with_more_than_one_vehicle"] == 1


def prediction(seq, predicted_min, from_min, to_min):
    return {
        "trip_id": "A",
        "stop_sequence": seq,
        "arrival_time": at(predicted_min),
        "departure_time": at(predicted_min) + timedelta(seconds=1),
        "first_requested_at": at(from_min),
        "last_requested_at": at(to_min),
    }


def test_prediction_error_uses_prediction_in_force_at_each_horizon():
    events = [event(2, 30, arr_min=30, dep_min=31)]  # actual arrival at minute 30
    preds = [
        prediction(2, 29, 24, 29.5),  # in force 5, 2 and 1 minute ahead
        prediction(2, 33, 0, 12),  # in force 30 minutes ahead
        prediction(2, 31, 12.2, 16),  # in force 15 minutes ahead
    ]
    out = prediction_error(preds, events)["by_horizon_min"]
    assert out["1"]["n"] == 1 and out["1"]["bias_s"] == -60.0
    assert out["5"]["within_60s_share"] == 1.0
    assert out["15"]["bias_s"] == 60.0
    assert out["30"]["bias_s"] == 180.0
    # 10 minutes ahead (minute 20): the latest run had stopped being fetched
    # at minute 16, more than 30 s earlier, so no prediction was in force.
    assert out["10"]["n"] == 0


def test_prediction_error_sets_aside_other_day_predictions():
    events = [event(2, 30, arr_min=30)]
    preds = [prediction(2, 30 + 15 * 60, 20, 40)]  # 03:30 Pacific the next day
    out = prediction_error(preds, events)["by_horizon_min"]["5"]
    assert out["n"] == 0 and out["other_day"] == 1 and out["other_day_share"] == 1.0


def test_a_few_far_off_predictions_do_not_hide_the_typical_miss():
    # Four stops predicted 1 minute late and one predicted 3 hours late (the
    # same Pacific day): the mean is 37 minutes, the typical miss 1 minute.
    events = [event(k, 30, arr_min=30) for k in range(2, 7)]
    preds = [prediction(k, 31, 20, 40) for k in range(2, 6)] + [prediction(6, 210, 20, 40)]
    out = prediction_error(preds, events)["by_horizon_min"]["5"]
    assert out["n"] == 5 and out["mae_s"] == 2208.0
    assert out["median_abs_s"] == 60.0
    assert out["over_30min"] == 1 and out["over_30min_share"] == 0.2


def report(vehicle, minute, lat, lon, speed=0.0):
    return {"vehicle_key": vehicle, "vehicle_ts": at(minute), "lat": lat, "lon": lon,
            "speed": speed}  # fmt: skip


def test_jitter_needs_stationary_reports_at_a_terminal():
    term = (48.2, -122.6)
    rows = [report("v1", k, 48.2 + 0.00001 * k, -122.6) for k in range(4)]  # ~1 m steps
    rows += [report("v1", 4, 48.2, -122.6, speed=8.0)]  # moving: ends the dwell
    rows += [report("v2", k, 48.3, -122.6) for k in range(5)]  # not at a terminal
    rows += [report("v3", k * 3, 48.2, -122.6) for k in range(4)]  # 3 min apart: no dwell
    out = jitter(rows, [term])
    assert out["dwells"] == 1
    assert out["reports"] == 4
    assert 0.5 < out["jitter_m_p95"] < 2.5


def test_missing_vehicles():
    out = missing_vehicles({"1", "3"}, {"2026-11-18": ["1", "2"], "2026-11-17": ["2"]})
    assert out["missing_today"] == ["2"] and out["missing_count"] == 1
    assert out["new_today"] == ["3"]
    first = missing_vehicles({"1"}, {})
    assert first["missing_count"] is None and first["new_today"] == []


def test_pending_recomputes_when_an_input_changes(tmp_path):
    import json

    from pipeline.quality.__main__ import pending
    from pipeline.quality.metrics import QUALITY_VERSION

    day = "2026-11-19"

    def tables(sha, version="tables/1"):
        files = [{"name": f"vehicles-{day}.parquet", "sha256": sha}]
        entry = {"release_tag": "parsed-2026-11", "files": files, "tables_version": version}
        return {"days": {day: entry}}

    def pins(version="p1", timepoints="p1__timetable-a"):
        return {"days": {day: {"passio": {"version": version}, "timepoints": timepoints}}}

    parsed, static = tables("v1"), pins()

    def events(sha):
        files = [{"name": f"stop-events-{day}.parquet", "sha256": sha}]
        return {"days": {day: {"release_tag": "parsed-2026-11", "files": files}}}

    (tmp_path / "days").mkdir()
    f = tmp_path / "days" / f"{day}.json"
    assert pending(parsed, static, tmp_path, events("a")) == [date(2026, 11, 19)]
    f.write_text(json.dumps({"quality_version": QUALITY_VERSION,
                             "schedule": {"passio_version": "p1"},
                             "processing": {"timepoints": {"folder": "p1__timetable-a"}},
                             "inputs": {"stop_events_sha256": "a", "vehicles_sha256": "v1",
                                        "tables_version": "tables/1"}}))  # fmt: skip
    assert pending(parsed, static, tmp_path, events("a")) == []
    # Rebuilt parsed tables make the day pending again.
    assert pending(tables("v2"), static, tmp_path, events("a")) == [date(2026, 11, 19)]
    assert pending(tables("v1", "tables/2"), static, tmp_path, events("a")) == [date(2026, 11, 19)]
    assert pending(parsed, static, tmp_path, events("b")) == [date(2026, 11, 19)]
    assert pending(parsed, static, tmp_path, {"days": {}}) == [date(2026, 11, 19)]
    # So does a schedule version moved by an override, or a new timetable list.
    assert pending(parsed, pins(version="p2"), tmp_path, events("a")) == [date(2026, 11, 19)]
    assert pending(parsed, pins(timepoints="p1__timetable-b"), tmp_path, events("a")) == [
        date(2026, 11, 19)
    ]


def test_retraced_shapes():
    from pipeline.quality.stops import retraced_shapes

    def pts(sid, lons):
        return [{"shape_id": sid, "shape_pt_sequence": str(i), "shape_pt_lat": "48.2",
                 "shape_pt_lon": str(x)} for i, x in enumerate(lons)]  # fmt: skip

    rows = pts("BAD", [-122.6, -122.59, -122.6, -122.59, -122.58]) + pts("OK", [-122.6, -122.5])
    rows += pts("UNUSED", [-122.6, -122.59, -122.6, -122.59])
    out = retraced_shapes(rows, {"BAD", "OK"})
    assert out["shapes_used"] == 2 and out["shapes_retraced"] == 1
    assert 1400 < out["retraced_m_by_shape"]["BAD"] < 1600


def test_prediction_error_keeps_twenty_quantiles_for_a_dot_chart():
    events = [event(k, 30, arr_min=30) for k in range(2, 42)]
    # Forty stops, predicted 1, 2, ... 40 seconds early (error -1 to -40 s).
    preds = [prediction(k, 30 - (k - 1) / 60, 20, 40) for k in range(2, 42)]
    out = prediction_error(preds, events)["by_horizon_min"]["5"]
    q = out["quantiles_20_s"]
    assert len(q) == 20 and q == sorted(q)
    assert q[0] == -39.0 and q[-1] == -2.0  # the 2.5th and 97.5th percentiles
    assert prediction_error([], events)["by_horizon_min"]["5"]["quantiles_20_s"] is None
