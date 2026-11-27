"""The publishing gate passes on the made-up archive and catches each kind of
damage done to a copy of it (``pipeline/invariants.py``)."""

import csv
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from pipeline import invariants as inv
from tests.helpers.synthetic import build_world

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-12-08T09:00:00+00:00"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """The whole nightly chain over the synthetic days, run once."""
    import pipeline.archive.build as archive_build
    import pipeline.release as rel

    root = tmp_path_factory.mktemp("invariants") / "world"
    with pytest.MonkeyPatch.context() as mp:
        for name in ("ensure_release", "upload", "fetch", "assets", "set_notes"):
            mp.setattr(rel, name, getattr(rel, name))
        mp.setattr(archive_build, "build_day", archive_build.build_day)
        mp.setattr(sys, "argv", sys.argv)
        mp.setenv("OITD_NOW", NOW)
        mp.delenv("GITHUB_SHA", raising=False)
        mp.delenv("GITHUB_REPOSITORY", raising=False)
        mp.syspath_prepend(str(REPO_ROOT / "scripts"))
        import rehearse_nightly

        reference = build_world(root)["reference"]
        code = rehearse_nightly.main(
            ["--replay", str(root), "--max-days", "100", "--now", NOW,
             "--reference-dir", str(reference),
             "--observations", str(reference / "field-observations.csv")]
        )  # fmt: skip
    assert code == 0
    return root


@pytest.fixture
def copy(world, tmp_path):
    dst = tmp_path / "world"
    shutil.copytree(world, dst, ignore=shutil.ignore_patterns("work"))
    return dst


def run(root: Path, **kw):
    return inv.run(root / "data-branch", releases=root / "releases", days=0, **kw)


def rules(res) -> set[str]:
    return {p.split()[0] for p in res.problems}


def edit_json(path: Path, change) -> None:
    obj = json.loads(path.read_text())
    change(obj)
    path.write_text(json.dumps(obj, indent=1) + "\n")


def test_every_rule_passes_on_the_made_up_archive(world):
    res = run(world)
    assert res.problems == []
    assert res.ran == [f"I{i}" for i in range(1, 13)]


def test_light_set_needs_no_release_files(world):
    res = inv.run(world / "data-branch", light_only=True)
    assert res.problems == [] and "I9" not in res.ran


def test_i1_recent_counts_not_the_sum_of_their_days(copy):
    def change(r):
        r["overall"]["late"] += 1
        r["overall"]["on_time"] -= 1

    edit_json(copy / "data-branch" / "ontime" / "recent.json", change)
    assert "I1" in rules(run(copy))


def test_i1_a_pooled_median_that_does_not_follow_the_deviations(copy):
    def change(r):
        route = next(iter(r["routes"].values()))
        route["deviation_s_median"] = (route["deviation_s_median"] or 0) + 5

    edit_json(copy / "data-branch" / "ontime" / "recent.json", change)
    assert rules(run(copy)) == {"I1"}


def test_i2_routes_that_do_not_sum_to_the_day(copy):
    def change(d):
        b = next(iter(d["by_hour"].values()))
        b["scheduled"] += 1

    edit_json(copy / "data-branch" / "ontime" / "days" / "2026-11-24.json", change)
    res = run(copy)
    assert "I2" in rules(res)
    assert any("by_hour" in p for p in res.problems)


def test_i3_a_share_that_does_not_follow_its_counts(copy):
    def change(d):
        d["observed_share"] = 0.5

    path = copy / "data-branch" / "ontime" / "days" / "2026-11-25.json"
    edit_json(path, change)
    assert "I3" in rules(run(copy))


def test_i3_quality_coverage_that_does_not_follow_its_counts(copy):
    def change(q):
        q["coverage"]["observed_scheduled_trips"] += 1

    edit_json(copy / "data-branch" / "quality" / "days" / "2026-11-24.json", change)
    assert "I3" in rules(run(copy))


@pytest.mark.parametrize("table", ["ontime/daily.csv", "quality/daily.csv"])
def test_i4_a_daily_table_that_differs_from_its_day_files(copy, table):
    path = copy / "data-branch" / table
    rows = list(csv.reader(path.open(newline="")))
    rows[1][3] = "0.123"
    with path.open("w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    assert rules(run(copy)) == {"I4"}


def test_i5_a_window_that_breaks_its_rules(copy):
    def change(r):
        r["days_without_service"] = []

    edit_json(copy / "data-branch" / "ontime" / "recent.json", change)
    assert rules(run(copy)) == {"I5"}


def test_i6_a_newest_day_on_an_old_version_is_a_problem(copy):
    edit_json(
        copy / "data-branch" / "quality" / "days" / "2026-11-26.json",
        lambda q: q.update(quality_version="quality/0.0.1"),
    )
    res = run(copy)
    assert "I6" in rules(res)


def test_i6_an_older_day_on_an_old_version_is_a_backlog(copy):
    edit_json(
        copy / "data-branch" / "quality" / "days" / "2026-11-24.json",
        lambda q: q.update(quality_version="quality/0.0.1"),
    )
    res = run(copy)
    assert "I6" not in rules(res)
    assert any(n.startswith("I6 quality") for n in res.notes)


def test_i7_a_day_without_its_quality_file(copy):
    (copy / "data-branch" / "quality" / "days" / "2026-11-25.json").unlink()
    res = run(copy)
    assert "I7" in rules(res)


def test_i7_a_service_day_without_stop_event_metrics(copy):
    edit_json(
        copy / "data-branch" / "quality" / "days" / "2026-11-25.json",
        lambda q: q.update(events=None),
    )
    assert "I7" in rules(run(copy))


def _events(world: Path, day: str):
    path = next((world / "releases").glob(f"*/stop-events-{day}.parquet"))
    tracks = next((world / "releases").glob(f"*/trip-tracks-{day}.parquet"))
    return pq.read_table(path).to_pylist(), pq.read_table(tracks).to_pylist()


def _stop_times(events):
    out: dict[str, list[int]] = {}
    for e in events:
        out.setdefault(e["trip_id"], []).append(e["stop_sequence"])
    return out


def test_i9_a_missing_stop_event(world):
    events, tracks = _events(world, "2026-11-24")
    stop_times = _stop_times(events)
    res = inv.Result()
    inv.check_events(res, "2026-11-24", events[1:], tracks, stop_times)
    assert rules(res) == {"I9"}


def test_i9_an_unknown_status(world):
    events, tracks = _events(world, "2026-11-24")
    events[1] = {**events[1], "departure_status": "guessed"}
    res = inv.Result()
    inv.check_events(res, "2026-11-24", events, tracks, _stop_times(events))
    assert "I9" in rules(res)


def test_i9_a_changed_stop_event_file_is_refused(copy):
    path = next((copy / "releases").glob("*/stop-events-2026-11-24.parquet"))
    table = pq.read_table(path)
    pq.write_table(table.slice(1), path)
    assert "I9" in rules(run(copy))


def test_i10_one_bus_on_two_trips_at_once(world):
    events, tracks = _events(world, "2026-11-24")
    res = inv.Result()
    inv.check_events(res, "2026-11-24", events, tracks, _stop_times(events))
    assert res.problems == []
    by_vehicle: dict[str, list[str]] = {}
    for e in events:
        if e["vehicle_key"] and e["trip_id"] not in by_vehicle.setdefault(e["vehicle_key"], []):
            by_vehicle[e["vehicle_key"]].append(e["trip_id"])
    _, trips = next((v, t) for v, t in by_vehicle.items() if len(t) > 1)
    first = [e for e in events if e["trip_id"] == trips[0]]
    second_start = min(
        e[k] for e in events if e["trip_id"] == trips[1]
        for k in ("arrival_time", "departure_time") if e[k] is not None
    )  # fmt: skip
    last = max(range(len(events)), key=lambda i: (events[i]["trip_id"] == trips[0],
               events[i]["stop_sequence"]))  # fmt: skip
    assert first
    events[last] = {**events[last], "arrival_time": second_start + timedelta(minutes=5),
                    "arrival_status": "observed"}  # fmt: skip
    res = inv.Result()
    inv.check_events(res, "2026-11-24", events, tracks, _stop_times(events))
    assert "I10" in rules(res)


def test_i11_a_day_file_that_its_stop_events_do_not_give(world):
    events, _ = _events(world, "2026-11-24")
    day = json.loads((world / "data-branch" / "ontime" / "days" / "2026-11-24.json").read_text())
    res = inv.Result()
    inv.recount(res, day, events)
    assert res.problems == []
    i = next(
        i for i, e in enumerate(events)
        if e["is_timepoint"] and not e["is_last_stop"] and e["departure_time"] is not None
    )  # fmt: skip
    events[i] = {**events[i], "departure_time": events[i]["departure_time"] + timedelta(hours=1)}
    inv.recount(res, day, events)
    assert rules(res) == {"I11"}


def test_i12_a_release_file_gone(copy):
    path = next((copy / "releases").glob("*/trip-tracks-2026-11-25.parquet"))
    path.unlink()
    assert "I12" in rules(run(copy))


def test_command_exits_1_on_a_problem(copy, capsys):
    data = copy / "data-branch"
    assert inv.main(["--data", str(data), "--light"]) == 0
    edit_json(data / "ontime" / "recent.json", lambda r: r["overall"].update(scheduled=1))
    assert inv.main(["--data", str(data), "--light"]) == 1
    assert "PROBLEM I1" in capsys.readouterr().out


def test_i6_old_versions_may_be_allowed_for_the_site_job(copy):
    edit_json(
        copy / "data-branch" / "quality" / "days" / "2026-11-26.json",
        lambda q: q.update(quality_version="quality/0.0.1"),
    )
    res = inv.run(copy / "data-branch", light_only=True, allow_old=True)
    assert "I6" not in rules(res) and any(n.startswith("I6") for n in res.notes)
