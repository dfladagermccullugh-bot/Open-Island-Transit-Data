import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "catch_up", Path(__file__).resolve().parents[1] / "scripts" / "catch_up.py"
)
catch_up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catch_up)


def utc(h, m=0, day=20):
    return datetime(2026, 11, day, h, m, tzinfo=UTC)  # Pacific is UTC-8 in November


def test_waits_until_the_day_is_complete():
    go, why = catch_up.decide(utc(8, 15), processed=False, last_run=None)  # 00:15 Pacific
    assert not go and "too early" in why


def test_dispatches_when_unprocessed_and_quiet():
    go, why = catch_up.decide(utc(9, 7), processed=False, last_run=utc(1, 0))
    assert go and "2026-11-19" in why
    assert catch_up.service_day(utc(9, 7)).isoformat() == "2026-11-19"


def test_leaves_processed_days_and_recent_runs_alone():
    assert not catch_up.decide(utc(9, 7), processed=True, last_run=None)[0]
    recent = utc(9, 7) - timedelta(hours=1)
    go, why = catch_up.decide(utc(9, 7), processed=False, last_run=recent)
    assert not go and "started at 08:07" in why


def test_a_day_file_without_its_stop_events_is_not_processed():
    assert catch_up.is_complete('{"complete": true}')
    assert catch_up.is_complete('{"service_date": "2026-11-20"}')  # before quality/0.6.1
    assert not catch_up.is_complete('{"complete": false}')
    assert not catch_up.is_complete("Not Found")
