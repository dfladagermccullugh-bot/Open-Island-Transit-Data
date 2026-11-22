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


def test_csv_cells_are_written_the_way_other_tools_read_them():
    """Python writes True and 6.3e-05; spreadsheets, R, DuckDB and JavaScript
    all read true and 0.000063 without help."""
    from pipeline.common import csv_cell

    assert csv_cell(True) == "true" and csv_cell(False) == "false"
    assert csv_cell(None) == ""
    assert csv_cell(6.3e-05) == "0.000063"
    assert csv_cell(1.5e-07) == "0.00000015"
    assert csv_cell(1e21) == "1000000000000000000000"
    assert csv_cell(0.970674) == 0.970674  # an ordinary number is left as it is
    assert csv_cell(341) == 341 and csv_cell("quality/0.5.0") == "quality/0.5.0"
    assert csv_cell(0.0) == 0.0


def test_write_csv_uses_those_cells(tmp_path):
    from pipeline.common import write_csv

    write_csv(tmp_path / "t.csv", ["day", "service_expected", "share", "note"],
              [["2026-11-26", False, 6.3e-05, None], ["2026-11-27", True, 0.5, "a,b"]])  # fmt: skip
    assert (tmp_path / "t.csv").read_text() == (
        'day,service_expected,share,note\n2026-11-26,false,0.000063,\n2026-11-27,true,0.5,"a,b"\n'
    )
