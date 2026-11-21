import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "watchdog", Path(__file__).resolve().parents[1] / "scripts" / "watchdog.py"
)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

NOON = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)  # Friday, 12:00 Pacific
NIGHT = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)  # 1:00 AM Pacific
THANKSGIVING = datetime(2026, 11, 26, 20, 0, tzinfo=UTC)


def fetch(now, age_s, entities=5, ok=True, newest_age_s=5):
    at = now - timedelta(seconds=age_s)
    return {
        "requested_at": at.isoformat(),
        "http_status": 200 if ok else None,
        "error_class": None if ok else "timeout",
        "entity_count": entities if ok else None,
        "newest_entity_ts": (at - timedelta(seconds=newest_age_s)).isoformat()
        if ok and entities
        else None,
    }


def test_one_empty_reply_is_not_a_problem_but_a_run_of_them_is():
    one = [fetch(NOON, 0, entities=0)] + [fetch(NOON, 20 * k) for k in range(1, 12)]
    assert watchdog.position_problems(one, NOON) == []
    empty = [fetch(NOON, 20 * k, entities=0) for k in range(12)]
    assert "empty in service hours" in watchdog.position_problems(empty, NOON)[0]
    # Failed fetches in between do not hide it; too few fetches do not trigger it.
    mixed = [fetch(NOON, 20 * k, entities=0, ok=k % 3 != 0) for k in range(27)]
    assert watchdog.position_problems(mixed, NOON)
    assert watchdog.position_problems(empty[:4], NOON) == []


def test_empty_feed_is_expected_at_night_and_on_a_holiday():
    night = [fetch(NIGHT, 20 * k, entities=0) for k in range(12)]
    assert watchdog.position_problems(night, NIGHT) == []
    holiday = [fetch(THANKSGIVING, 20 * k, entities=0) for k in range(12)]
    assert watchdog.position_problems(holiday, THANKSGIVING) == []


def test_frozen_vehicle_timestamps_are_reported():
    frozen = [fetch(NOON, 20 * k, newest_age_s=1200) for k in range(12)]
    assert "frozen" in watchdog.position_problems(frozen, NOON)[0]


def test_static_download_checks():
    def d(hours, ok=True, source="passio"):
        return {
            "source": source,
            "logged_at": (NOON - timedelta(hours=hours)).isoformat(),
            "http_status": 200 if ok else 503,
            "error_class": None if ok else "http",
        }

    assert watchdog.static_problems([d(4), d(10)], NOON) == []
    assert watchdog.static_problems([d(4, ok=False), d(10)], NOON) == []  # one failure: wait
    assert "failed" in watchdog.static_problems([d(4, ok=False), d(10, ok=False)], NOON)[0]
    assert "no Passio download" in watchdog.static_problems([d(40)], NOON)[0]
    assert watchdog.static_problems([d(400, source="trillium")], NOON) == []


def test_storage_and_prune_checks():
    health = {"database_bytes": 60e6, "oldest_raw_at": (NOON - timedelta(days=4)).isoformat()}

    def prune(hours, error=None, day="2026-11-17"):
        return {"ran_at": (NOON - timedelta(hours=hours)).isoformat(), "service_date": day,
                "error": error}  # fmt: skip

    assert watchdog.storage_problems(health, [], NOON) == []
    assert watchdog.storage_problems(health, [prune(8)], NOON) == []
    failed = watchdog.storage_problems(health, [prune(8, "archive file not confirmed: x")], NOON)
    assert "2026-11-17 failed" in failed[0]
    # An error in an earlier run that the latest run got past is not reported.
    assert watchdog.storage_problems(health, [prune(8), prune(32, "old")], NOON) == []
    assert "has not run" in watchdog.storage_problems(health, [prune(50)], NOON)[0]
    full = {"database_bytes": 350e6, "oldest_raw_at": (NOON - timedelta(days=12)).isoformat()}
    assert len(watchdog.storage_problems(full, [prune(8)], NOON)) == 2


def test_nightly_check_waits_two_days_and_ignores_unreadable_data():
    assert watchdog.nightly_problems({"2026-11-18"}, "2026-11-10", NOON) == []
    late = watchdog.nightly_problems({"2026-11-17"}, "2026-11-10", NOON)
    assert late == ["nightly job: service day 2026-11-18 is still not processed"]
    assert watchdog.nightly_problems(None, "2026-11-10", NOON) == []
    assert watchdog.nightly_problems(set(), "2026-11-19", NOON) == []  # before collection began


def test_feed_age_check():
    status = {f: {"last_ok_age_s": 30} for f in watchdog.MAX_AGE_S}
    assert watchdog.feed_problems(status) == []
    status["tripUpdates"]["last_ok_age_s"] = 1200
    status["serviceAlerts"]["last_ok_age_s"] = None
    out = watchdog.feed_problems(status)
    assert "20 minutes ago" in out[0] and "no successful fetch" in out[1]
