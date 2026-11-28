"""The tiered data checks (``scripts/anomaly.py``): each tier is produced by
the change it is meant for, and nothing is said about an ordinary day."""

import csv
import importlib.util
import shutil
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("anomaly", ROOT / "scripts" / "anomaly.py")
anomaly = importlib.util.module_from_spec(spec)
sys.modules["anomaly"] = anomaly  # dataclasses look their module up there
spec.loader.exec_module(anomaly)

GOLDEN = ROOT / "tests" / "golden" / "data-branch"
NOW = datetime(2026, 12, 8, 9, 0, tzinfo=UTC)


def weekdays(n, end=date(2026, 12, 4)):
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def quality_rows(n=12, **last):
    rows = []
    for k, d in enumerate(weekdays(n)):
        rows.append({
            "service_date": d.isoformat(), "service_expected": "true",
            "passio_version": "passio-a", "scheduled_trips": "341",
            "trip_coverage": f"{0.97 + 0.002 * (k % 3):.3f}", "impossible_jumps": str(k % 3),
            "off_shape_share": "0.04", "timepoint_coverage": f"{0.85 + 0.003 * (k % 2):.3f}",
            "position_age_s_p95": "8.0", "trip_id_match_rate": "1.0",
            "timepoint_names_resolved_share": "0.9932", "canceled_trips": "0",
            "passio_changed_during_day": "false", "complete": "true",
        })  # fmt: skip
    rows[-1].update(last)
    return rows


def ontime_rows(n=12, **last):
    rows = [{"service_date": d.isoformat(), "service_expected": "true",
             "on_time_share_of_observed": f"{0.73 + 0.01 * (k % 3):.3f}",
             "feed_agreement_share": "0.94"}
            for k, d in enumerate(weekdays(n))]  # fmt: skip
    rows[-1].update(last)
    return rows


def findings(quality=None, ontime=None, day=None):
    f = anomaly.Findings()
    anomaly.check_figures(f, quality or quality_rows(), ontime or ontime_rows(), day)
    return f


def test_an_ordinary_day_raises_nothing():
    f = findings()
    assert (f.problems, f.looks, f.notes) == ([], [], [])
    assert f.exit_code() == 0


def test_a_collapse_in_trip_coverage_is_a_problem():
    f = findings(quality_rows(trip_coverage="0.60"))
    assert any("trip_coverage" in p for p in f.problems) and f.exit_code() == 4


def test_a_day_without_stop_events_is_a_problem():
    f = findings(quality_rows(complete="false", timepoint_coverage=""))
    assert any("stop events missing" in p for p in f.problems)


def test_trip_ids_that_stop_matching_are_a_problem():
    f = findings(quality_rows(trip_id_match_rate="0.40"))
    assert any("static-pin-overrides" in p for p in f.problems)


def test_a_burst_of_glitches_is_something_to_look_at():
    """November 24, 2026 had 67 impossible jumps; a normal day has 0 to 2."""
    f = findings(quality_rows(impossible_jumps="67"))
    assert f.problems == [] and any("impossible_jumps" in x for x in f.looks)
    assert f.exit_code() == 1
    assert findings(quality_rows(impossible_jumps="15")).looks == []  # under 20: no


def test_low_feed_agreement_and_shifted_trips_are_something_to_look_at():
    day = {
        "service_date": "2026-12-04",
        "validation": {"trip_offsets": {"flagged_routes": ["7922"]}},
    }
    f = findings(ontime=ontime_rows(feed_agreement_share="0.85"), day=day)
    assert len(f.looks) == 2


def test_a_new_schedule_version_is_a_note():
    f = findings(quality_rows(passio_version="passio-b"))
    assert f.problems == [] and f.looks == [] and len(f.notes) == 1
    assert f.exit_code() == 3


def test_with_little_history_fixed_limits_are_used():
    f = findings(quality_rows(n=3, off_shape_share="0.20"))
    assert any("fixed limit" in x for x in f.looks)


def test_a_day_unprocessed_36_hours_after_it_ended_is_a_problem():
    days = [date(2026, 11, 28) + timedelta(days=k) for k in range(8)]  # to December 5
    rows = [{"service_date": d.isoformat()} for d in days if d != date(2026, 12, 3)]
    f = anomaly.Findings()
    anomaly.check_processed(f, rows, "2026-11-28", datetime(2026, 12, 5, 23, 0, tzinfo=UTC))
    assert len(f.problems) == 1 and "2026-12-03" in f.problems[0]  # ended 39 hours before
    g = anomaly.Findings()
    anomaly.check_processed(g, rows, "2026-11-28", datetime(2026, 12, 5, 12, 0, tzinfo=UTC))
    assert g.problems == []  # ended 28 hours before: the nightly job still has time


def test_token_expiry_and_switched_off_workflows():
    f = anomaly.Findings()
    anomaly.check_token(f, NOW + timedelta(days=5), NOW)
    anomaly.check_workflows(f, {"nightly.yml": "disabled_inactivity", "ci.yml": "active"})
    assert len(f.problems) == 2
    g = anomaly.Findings()
    anomaly.check_token(g, NOW + timedelta(days=15), NOW)
    assert g.problems == [] and len(g.looks) == 1


def test_an_edited_daily_table_fails_the_publishing_rules(tmp_path):
    data = tmp_path / "data-branch"
    shutil.copytree(GOLDEN, data)
    now = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)  # the golden days are processed
    assert anomaly.evaluate(data, now).problems == []
    path = data / "quality" / "daily.csv"
    rows = list(csv.reader(path.open(newline="")))
    rows[1][4] = "0.5"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    f = anomaly.evaluate(data, now)
    assert any(p.startswith("publishing rule I4") for p in f.problems)


def test_notes_file_has_no_time_stamp(tmp_path):
    out = anomaly.notes_file(anomaly.Findings(notes=["x"]), "2026-12-04")
    assert out["status"] == "note" and "checked_at" not in out


def test_a_site_that_stopped_deploying_is_something_to_look_at():
    f = anomaly.Findings()
    anomaly.check_site(f, date(2026, 12, 1), NOW)  # December 8: 7 days behind
    anomaly.check_site(f, date(2026, 12, 6), NOW)  # 2 days: ordinary
    assert len(f.looks) == 1


def _validation(tmp_path, static=None, realtime=None):
    import json

    for name, codes in (static or {}).items():
        p = tmp_path / "validation" / "static" / f"{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"notices": [
            {"code": c, "severity": "ERROR", "count": 3} for c in codes] + [
            {"code": "a_warning", "severity": "WARNING", "count": 9}]}))  # fmt: skip
    for day, (codes, checked) in (realtime or {}).items():
        p = tmp_path / "validation" / "realtime" / f"{day}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"service_date": day, "checked_at": checked.isoformat(), "feeds": {
            "vehiclePositions": {"rules": [{"code": c, "severity": "ERROR", "occurrences": 5}
                                           for c in codes]}}}))  # fmt: skip
    return tmp_path


def test_a_new_validator_error_in_a_new_schedule_is_something_to_look_at(tmp_path):
    data = _validation(tmp_path, {"passio-a": ["e1"], "passio-b": ["e1", "e2"]})
    f = anomaly.Findings()
    anomaly.check_validation(f, data, quality_rows(passio_version="passio-b"), NOW)
    assert len(f.looks) == 1 and "e2 (3)" in f.looks[0] and "e1" not in f.looks[0]
    same = anomaly.Findings()
    anomaly.check_validation(same, data, quality_rows(), NOW)  # no new version: nothing
    assert same.looks == same.notes == []


def test_the_schedule_validators_errors_are_a_note_when_nothing_is_new(tmp_path):
    data = _validation(tmp_path, {"passio-a": ["e1"], "passio-b": ["e1"]})
    f = anomaly.Findings()
    anomaly.check_validation(f, data, quality_rows(passio_version="passio-b"), NOW)
    assert f.looks == [] and len(f.notes) == 1 and "e1 (3)" in f.notes[0]


def test_realtime_validator_findings_while_fresh(tmp_path):
    old = NOW - timedelta(days=7)
    data = _validation(
        tmp_path, realtime={"2026-11-27": (["E001"], old), "2026-12-04": (["E001", "E004"], NOW)}
    )
    f = anomaly.Findings()
    anomaly.check_validation(f, data, quality_rows(), NOW)
    assert len(f.looks) == 1 and "E004 (5)" in f.looks[0]
    stale = anomaly.Findings()
    anomaly.check_validation(stale, data, quality_rows(), NOW + timedelta(days=3))
    assert stale.looks == stale.notes == []


def _gate_file(folder, passed, ran_at, problems=(), name="gate.json"):
    import json

    (folder / "checks").mkdir(parents=True, exist_ok=True)
    (folder / "checks" / name).write_text(
        json.dumps({"passed": passed, "mode": "full", "ran_at": ran_at, "problems": list(problems)})
    )


def test_a_failed_gate_on_staging_is_named_and_a_long_hold_is_a_problem(tmp_path):
    data, staging = tmp_path / "data", tmp_path / "staging"
    _gate_file(data, True, (NOW - timedelta(hours=5)).isoformat())
    _gate_file(
        staging,
        False,
        (NOW - timedelta(hours=1)).isoformat(),
        ["I4 quality/daily.csv row 2026-12-04 differs", "frictionless exited with code 1"],
    )

    # Level branches: nothing to say, whatever the reports hold.
    f = anomaly.Findings()
    anomaly.check_staging(f, data, staging, ("a", "a"), NOW)
    assert (f.problems, f.looks) == ([], [])

    f = anomaly.Findings()
    anomaly.check_staging(f, data, staging, ("a", "b"), NOW)
    assert f.problems == []
    assert len(f.looks) == 1 and "I4 quality/daily.csv" in f.looks[0]
    assert "frictionless" in f.looks[0]

    # Data last moved 30 hours ago and staging is still ahead.
    _gate_file(data, True, (NOW - timedelta(hours=30)).isoformat())
    f = anomaly.Findings()
    anomaly.check_staging(f, data, staging, ("a", "b"), NOW)
    assert len(f.problems) == 1 and "more than 26 hours" in f.problems[0]

    # A light gate's promotion counts as data moving.
    _gate_file(data, True, (NOW - timedelta(hours=2)).isoformat(), name="gate-light.json")
    f = anomaly.Findings()
    anomaly.check_staging(f, data, staging, ("a", "b"), NOW)
    assert f.problems == []
