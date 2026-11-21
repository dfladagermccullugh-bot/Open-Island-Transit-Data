"""The order in which a stage takes its days."""

from datetime import date, timedelta

from pipeline.common import run_order


def d(text: str) -> date:
    return date.fromisoformat(text)


def test_a_new_day_goes_ahead_of_a_rebuild_backlog():
    done = [d("2026-11-19") + timedelta(days=k) for k in range(40)]
    yesterday = done[-1] + timedelta(days=1)
    order = run_order([*done, yesterday], {x.isoformat() for x in done}, limit=7)
    assert order[0] == yesterday
    assert order[1:] == sorted(done, reverse=True)[:6]


def test_new_days_run_oldest_first():
    # Quality reads the day files of the days before, so they must exist first.
    days = [d("2026-12-03"), d("2026-12-01"), d("2026-12-02")]
    assert run_order(days, set(), limit=7) == sorted(days)


def test_the_limit_applies_after_ordering():
    days = [d("2026-12-01"), d("2026-12-02"), d("2026-12-03")]
    assert run_order(days, {"2026-12-01", "2026-12-02"}, limit=2) == [
        d("2026-12-03"),
        d("2026-12-02"),
    ]


def test_a_day_listed_twice_runs_once():
    assert run_order([d("2026-12-01"), d("2026-12-01")], {"2026-12-01"}, 7) == [d("2026-12-01")]
