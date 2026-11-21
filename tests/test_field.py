from datetime import UTC, date, datetime, timedelta

import pytest

from pipeline.events import field

DAY = date(2026, 11, 23)
T = datetime(2026, 11, 23, 16, 0, tzinfo=UTC)  # 08:00 Pacific


def test_parse_local_times():
    assert field.parse_local(DAY, "08:00:05") == T + timedelta(seconds=5)
    assert field.parse_local(DAY, " 8:00:05 am ") == T + timedelta(seconds=5)
    assert field.parse_local(DAY, "2:03:27 PM") == datetime(2026, 11, 23, 22, 3, 27, tzinfo=UTC)
    assert field.parse_local(DAY, "12:10:00 am") == datetime(2026, 11, 23, 8, 10, tzinfo=UTC)
    assert field.parse_local(DAY, "") is None
    with pytest.raises(ValueError):
        field.parse_local(DAY, "08:00")  # seconds are required


def test_load_skips_blank_rows_and_reports_bad_ones(tmp_path):
    f = tmp_path / "obs.csv"
    f.write_text(
        ",".join(field.SHEET_COLUMNS) + "\n"
        "2026-11-23,S1,Stop,1,A,08:00,both,08:00:10,08:00:40,\n"
        "2026-11-23,S1,Stop,1,B,08:10,both,,,\n"
        "2026-11-23,S1,Stop,1,C,08:20,both,8:20,,\n"
    )
    rows, problems = field.load(f)
    assert [r["trip_id"] for r in rows] == ["A"]
    assert rows[0]["departure"] == T + timedelta(seconds=40)
    assert len(problems) == 1 and problems[0].startswith("line 4")


def _event(trip, stop, arr, dep):
    return {"trip_id": trip, "stop_id": stop,
            "arrival_time": None if arr is None else T + timedelta(seconds=arr),
            "departure_time": None if dep is None else T + timedelta(seconds=dep)}  # fmt: skip


def _obs(trip, stop, arr, dep, day=DAY, line=2):
    return {"line": line, "service_date": day.isoformat(), "trip_id": trip, "stop_id": stop,
            "arrival": None if arr is None else T + timedelta(seconds=arr),
            "departure": None if dep is None else T + timedelta(seconds=dep)}  # fmt: skip


def test_score():
    events = {DAY.isoformat(): [_event("A", "S1", 10, 50), _event("B", "S1", None, 300),
                                _event("C", "S2", 600, 640)]}  # fmt: skip
    obs = [
        _obs("A", "S1", 0, 40),  # inferred 10 s later on both
        _obs("B", "S1", 290, 340),  # arrival not inferred; departure 40 s early
        _obs("C", "S2", 700, None),  # inferred 100 s early
        _obs("Z", "S1", 0, None),  # no such scheduled event
        _obs("A", "S1", 0, None, day=date(2026, 11, 24)),  # day not processed yet
    ]
    r = field.score(obs, events)
    assert r["arrival"]["n"] == 2 and r["departure"]["n"] == 2
    assert r["overall"]["n"] == 4
    assert r["overall"]["bias_s"] == pytest.approx((10 + 10 - 40 - 100) / 4, abs=0.1)
    assert r["overall"]["mae_s"] == 40.0
    assert r["overall"]["within_30s_share"] == 0.5
    assert r["meets_target"] is False
    assert r["timed_moments_not_inferred"] == 1
    assert r["rows_with_no_scheduled_event"] == 1
    assert r["rows_waiting_for_their_day"] == 1
    assert r["by_stop"]["S2"]["arrival"]["mae_s"] == 100.0


def test_score_with_nothing_scored():
    r = field.score([], {})
    assert r["overall"]["n"] == 0 and r["meets_target"] is None


def test_pass_is_scored_against_the_midpoint():
    events = {DAY.isoformat(): [_event("A", "S1", 10, 20), _event("B", "S1", None, 30)]}
    a = _obs("A", "S1", None, None)
    a["pass"] = T + timedelta(seconds=12)  # midpoint 15 -> +3 s
    b = _obs("B", "S1", None, None)
    b["pass"] = T + timedelta(seconds=30)  # no inferred arrival -> not scored
    r = field.score([a, b], events)
    assert r["pass"]["n"] == 1 and r["pass"]["bias_s"] == 3.0
    assert r["overall"]["n"] == 1 and r["timed_moments_not_inferred"] == 1


def test_load_reads_pass_stop(tmp_path):
    f = tmp_path / "obs.csv"
    f.write_text(
        ",".join(field.SHEET_COLUMNS) + "\n2026-11-23,S1,Stop,1,A,08:00,both,,,8:00:12 am,\n"
    )
    rows, problems = field.load(f)
    assert not problems and rows[0]["pass"] == T + timedelta(seconds=12)
