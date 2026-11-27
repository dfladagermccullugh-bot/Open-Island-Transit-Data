import importlib.util
from datetime import UTC, date, datetime, timedelta
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


# --- checks on the published figures ------------------------------------------------

TODAY = date(2026, 12, 29)


def row(day, **over):
    base = {
        "service_date": day, "service_expected": "True", "passio_version": "passio-aaa",
        "scheduled_trips": "341", "trip_coverage": "0.97", "trip_id_match_rate": "1.0",
        "timepoint_names_resolved_share": "0.9932", "canceled_trips": "0",
        "passio_basis": "complete download that day", "passio_changed_during_day": "False",
    }  # fmt: skip
    return {**base, **{k: str(v) for k, v in over.items()}}


def history(n=7, **last):
    days = [(date(2026, 12, 21) + timedelta(days=k)).isoformat() for k in range(n)]
    return [row(d) for d in days[:-1]] + [row(days[-1], **last)]


def data_problems(rows):
    return watchdog.data_problems(rows, TODAY, last_holiday=date(2027, 12, 25))


def test_ordinary_figures_raise_nothing():
    assert data_problems(history()) == []
    assert data_problems([]) == []


def test_trip_ids_that_stop_matching_the_schedule_are_reported():
    (line,) = data_problems(history(trip_id_match_rate=0.41))
    assert "2026-12-27" in line and "41%" in line
    assert "reference/static-pin-overrides.csv" in line


def test_a_service_day_with_no_scheduled_trips_is_reported():
    out = data_problems(history(scheduled_trips=0, trip_coverage=""))
    assert out == ["2026-12-27: the schedule lists no trips, on a day when service is expected"]


def test_trip_coverage_far_below_recent_days_is_reported():
    (line,) = data_problems(history(trip_coverage=0.70))
    assert "70.0%" in line and "97.0%" in line
    assert data_problems(history(trip_coverage=0.90)) == []
    # With fewer than MIN_HISTORY earlier service days there is nothing to compare with.
    assert data_problems(history(n=4, trip_coverage=0.70)) == []


def test_a_drop_in_resolved_timepoint_names_is_reported():
    (line,) = data_problems(history(timepoint_names_resolved_share=0.95))
    assert "timepoint names" in line and "95.0%" in line


def test_a_holiday_row_is_skipped_and_not_used_as_history():
    rows = history()
    rows.append(row("2026-12-28", service_expected="False", scheduled_trips=0, trip_coverage=0.0))
    assert data_problems(rows) == []


def test_notes_for_a_new_schedule_version_a_change_day_and_cancellations():
    basis = "complete downloads that day disagreed; chosen by match to the live feed"
    out = data_problems(
        history(passio_version="passio-bbb", passio_changed_during_day="True",
                passio_basis=basis, canceled_trips=2)
    )  # fmt: skip
    assert len(out) == 3
    assert any("new Passio schedule version passio-bbb" in x for x in out)
    assert any("downloads disagreed" in x and "chosen by match" in x for x in out)
    assert any("2 trips as canceled" in x for x in out)


def test_rows_from_before_the_new_columns_do_not_break_the_checks():
    old = [{k: v for k, v in r.items() if not k.startswith(("canceled", "passio_b", "passio_c"))}
           for r in history()]  # fmt: skip
    assert data_problems(old) == []


def test_the_holiday_list_running_out_is_reported():
    out = watchdog.data_problems(history(), TODAY, last_holiday=date(2027, 1, 1))
    assert out == [
        "the list of no-service holidays ends on 2027-01-01: "
        "add next year's dates to reference/no-service-days.csv"
    ]
    assert watchdog.data_problems([], TODAY, last_holiday=None) == []


def test_a_day_whose_quality_file_is_not_complete_is_not_processed():
    rows = [
        {"service_date": "2026-11-20"},  # before the column existed
        {"service_date": "2026-11-21", "complete": "true"},
        {"service_date": "2026-11-22", "complete": "false"},
    ]
    assert watchdog.complete_days(rows) == {"2026-11-20", "2026-11-21"}
