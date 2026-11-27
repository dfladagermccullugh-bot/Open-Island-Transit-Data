"""The period files (``pipeline/periods.py``) and their publishing rule (I8)."""

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

from pipeline import invariants as inv
from pipeline import periods

GOLDEN = Path(__file__).resolve().parent / "golden" / "data-branch"


def day(d: date, on_time=8, late=2, service=True, timetable="2026-09-27"):
    devs = [30] * on_time + [600] * late
    block = {"scheduled": 12, "observed": on_time + late, "on_time": on_time, "early": 0,
             "late": late}  # fmt: skip
    return {
        "service_date": d.isoformat(), "service_expected": service, **block,
        "on_time_share_of_observed": round(on_time / (on_time + late), 4),
        "observed_share": round((on_time + late) / 12, 4),
        "timepoints": f"passio-x__timetable-{timetable}-abc",
        "by_hour": {"7": {**block, "deviations_s": devs}},
        "by_route_stop": {"R": {"S": {**block, "deviations_s": devs}}},
    }  # fmt: skip


def run_of(n, start=date(2026, 11, 2), **kw):
    return [day(start + timedelta(days=k), **kw) for k in range(n)]


def test_headline_waits_for_14_days_before_comparing():
    h = periods.headline(run_of(30))
    assert h["current"]["days"] == 28 and h["previous"] is None and h["change_points"] is None
    h = periods.headline(run_of(28, on_time=6, late=4) + run_of(28, start=date(2026, 11, 30)))
    assert h["previous"]["days"] == 28
    assert h["change_points"] == 20.0  # 60% then 80%


def test_headline_spans_a_timetable_change_and_skips_holidays():
    days = run_of(10) + run_of(10, start=date(2026, 11, 12), timetable="2026-12-27")
    days[5]["service_expected"] = False
    h = periods.headline(days)
    assert h["current"]["days"] == 19
    assert h["timetables"] == ["2026-09-27", "2026-12-27"]
    assert h["days_without_service"] == ["2026-11-07"]


def test_weeks_start_on_monday_and_are_marked_partial():
    p = periods.ontime_periods(run_of(10, start=date(2026, 11, 4)))  # Wednesday to Friday
    weeks = p["grains"]["week"]
    assert [w["period"] for w in weeks] == ["2026-11-02", "2026-11-09"]
    assert [w["partial"] for w in weeks] == [True, True]
    assert weeks[0]["days"] == 5 and weeks[0]["scheduled"] == 60
    assert p["grains"]["month"][0]["partial"] is True


def test_hour_medians_come_from_the_pooled_deviations():
    p = periods.ontime_periods(run_of(3))
    row = p["grains"]["hour"]["pooled"]["rows"][0]
    assert (
        row["hour"] == 7 and row["deviations_counted"] == 30 and row["deviation_s_median"] == 30.0
    )


def test_the_golden_period_files_pass_and_a_changed_headline_fails(tmp_path):
    data = tmp_path / "data-branch"
    shutil.copytree(GOLDEN, data)
    assert [p for p in inv.run(data, light_only=True).problems if p.startswith("I8")] == []
    path = data / "ontime" / "headline.json"
    h = json.loads(path.read_text())
    h["current"]["on_time"] += 1
    path.write_text(json.dumps(h))
    assert any(p.startswith("I8") for p in inv.run(data, light_only=True).problems)
