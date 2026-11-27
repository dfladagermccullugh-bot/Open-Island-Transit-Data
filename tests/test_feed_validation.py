"""The official validators' summaries (``pipeline/feed_validation.py``).
Java and the validators themselves run only in the Validators workflow; here
their input and output are checked."""

import json
from datetime import UTC, date, datetime

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline import feed_validation as fv

STATIC_REPORT = {
    "summary": {"validatorVersion": "8.0.1"},
    "notices": [
        {"code": "stop_too_far_from_shape", "severity": "WARNING", "totalNotices": 14},
        {
            "code": "stop_time_with_arrival_before_previous_departure_time",
            "severity": "ERROR",
            "totalNotices": 22,
        },
        {"code": "service_window_outside_feed_period", "severity": "INFO", "totalNotices": 3},
    ],
}


def test_a_schedule_report_is_kept_as_rules_and_counts():
    s = fv.summarize_static(STATIC_REPORT, "passio-x", "2026-11-19")
    assert (s["errors"], s["warnings"], s["infos"]) == (22, 14, 3)
    assert [n["severity"] for n in s["notices"]] == ["ERROR", "WARNING", "INFO"]
    assert s["validator_version"] == "8.0.1" and s["date_for_validation"] == "2026-11-19"


def _rt(code, severity, n):
    rule = {
        "errorId": code,
        "severity": severity,
        "title": f"title {code}",
        "occurrenceSuffix": "x",
    }
    return {
        "errorMessage": {"validationRule": rule},
        "occurrenceList": [{"prefix": str(i)} for i in range(n)],
    }


def test_realtime_results_are_counted_per_rule_across_responses():
    rules = fv.summarize_realtime({
        "a.pb": [_rt("W001", "WARNING", 3), _rt("E004", "ERROR", 1)],
        "b.pb": [_rt("W001", "WARNING", 2)],
    })  # fmt: skip
    assert rules[0] == {
        "code": "E004",
        "severity": "ERROR",
        "title": "title E004",
        "occurrences": 1,
        "responses": 1,
    }
    assert rules[1]["occurrences"] == 5 and rules[1]["responses"] == 2


def test_the_hour_is_written_one_file_per_response_named_by_time(tmp_path):
    t = lambda h, m: datetime(2026, 11, 24, h, m, 5, tzinfo=UTC)  # noqa: E731
    table = pa.table({
        "feed": ["vehiclePositions", "tripUpdates", "vehiclePositions", "serviceAlerts",
                 "vehiclePositions"],
        "requested_at": [t(18, 0), t(18, 1), t(17, 59), t(18, 2), t(18, 30)],
        "http_status": [200, 200, 200, 200, 500],
        "body": [b"a", b"b", b"c", b"d", b"e"],
    })  # fmt: skip
    pq.write_table(table, tmp_path / "raw.parquet")
    counts = fv.write_hour(tmp_path / "raw.parquet", "2026-11-24", tmp_path / "hour")
    assert counts == {"vehiclePositions": 1, "tripUpdates": 1}  # 10 AM Pacific is 18:00 UTC
    assert (
        tmp_path / "hour" / "vehiclePositions" / "vehiclePositions-2026-11-24T18-00-05Z.pb"
    ).read_bytes() == b"a"


def test_a_realtime_check_is_due_once_a_week_on_the_latest_weekday(tmp_path):
    (tmp_path / "manifest").mkdir()
    days = {d: {} for d in ["2026-11-20", "2026-11-21", "2026-11-22"]}  # Fri, Sat, Sun
    (tmp_path / "manifest" / "raw.json").write_text(json.dumps({"days": days}))
    assert fv.due_day(tmp_path, date(2026, 11, 23)) == "2026-11-20"
    (tmp_path / "validation" / "realtime").mkdir(parents=True)
    (tmp_path / "validation" / "realtime" / "2026-11-20.json").write_text("{}")
    assert fv.due_day(tmp_path, date(2026, 11, 26)) is None


def test_each_new_schedule_version_is_checked_once(tmp_path, monkeypatch):
    (tmp_path / "manifest").mkdir()
    example = {
        "release_tag": "raw-2026-11",
        "file": "static-2026-11-19-passio-1.zip",
        "sha256": "x",
    }
    versions = {
        "passio-a": {"source": "passio", "first_day": "2026-11-19", "example": example},
        "trillium-b": {"source": "trillium", "first_day": "2026-11-19", "example": example},
    }
    (tmp_path / "manifest" / "static.json").write_text(json.dumps({"versions": versions}))
    monkeypatch.setattr(fv, "_release_file", lambda *a: tmp_path / "zip")
    seen = []
    monkeypatch.setattr(fv, "run_static", lambda z, j, day, out: seen.append(day) or STATIC_REPORT)
    assert fv.validate_static(tmp_path, tmp_path / "jar", None, tmp_path, "r") == ["passio-a"]
    assert fv.validate_static(tmp_path, tmp_path / "jar", None, tmp_path, "r") == []
    saved = json.loads((tmp_path / "validation" / "static" / "passio-a.json").read_text())
    assert seen == ["2026-11-19"] and saved["errors"] == 22 and saved["file"] == example["file"]
