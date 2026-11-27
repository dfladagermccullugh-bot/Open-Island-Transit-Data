"""Stop and trip views for the site (``scripts/site_views.py``)."""

import json
from pathlib import Path

import pytest

from tests.test_golden import replay  # noqa: F401  (fixture)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def site_views(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    import site_views

    return site_views


def test_views_match_the_day_files_and_refuse_a_changed_file(tmp_path, replay, site_views):  # noqa: F811
    world = tmp_path / "world"
    assert replay(world) == 0
    data, releases, out = world / "data-branch", world / "releases", tmp_path / "site"
    assert site_views.main([str(data), str(out), "--releases", str(releases)]) == 0

    index = json.loads((out / "views" / "stops" / "index.json").read_text())
    assert index["stops"], "no stop in the index"
    total = 0
    for s in index["stops"]:
        rows = json.loads((out / "views" / "stops" / f"{s['stop_id']}.json").read_text())["rows"]
        assert len(rows) == s["departures"]
        total += len(rows)
    by_route_stop = 0
    for d in index["days"]:
        day = json.loads((data / "ontime" / "days" / f"{d}.json").read_text())
        by_route_stop += sum(
            b["scheduled"] for ss in day.get("by_route_stop", {}).values() for b in ss.values()
        )
    assert total == by_route_stop
    trips = list((out / "views" / "trips").glob("*.json"))
    assert trips and all(json.loads(p.read_text())["trips"] for p in trips)

    # A departure missing from the views is a problem the build stops on.
    files = site_views.stop_event_files(data, releases)
    _, stops, _ = site_views.build(data, files)
    first = next(iter(stops))
    stops[first] = stops[first][1:]
    assert site_views.check(data, files, stops)

    # A Release file that is not the published one is refused.
    name = next(releases.rglob("stop-events-*.parquet"))
    name.write_bytes(name.read_bytes() + b"x")
    with pytest.raises(SystemExit, match="SHA-256"):
        site_views.stop_event_files(data, releases)


def test_replay_keeps_every_report_but_glitches_and_every_timed_stop(tmp_path, replay, site_views):  # noqa: F811
    import io
    from itertools import accumulate

    import pyarrow.parquet as pq

    from pipeline import glitch
    from pipeline.events.ontime import classify

    world = tmp_path / "world"
    assert replay(world) == 0
    data, releases = world / "data-branch", world / "releases"
    views = site_views.replay_views(data, releases)
    assert views, "no replay"
    vehicles = site_views.release_files(data, releases, "vehicles", 0, set(views))
    events = site_views.stop_event_files(data, releases)
    for d, v in views.items():
        rows = [
            r for r in pq.read_table(io.BytesIO(vehicles[d])).to_pylist() if r["lat"] is not None
        ]
        removed = glitch.flag(rows).removed
        kept = {(r["vehicle_key"], r["vehicle_ts"]) for r in rows} - removed
        assert sum(len(b["t"]) for b in v["buses"]) == len(kept)
        for b in v["buses"]:
            t = list(accumulate(b["t"]))
            assert t == sorted(t) and len(set(t)) == len(t)
            lat = [x / v["scale"] for x in accumulate(b["lat"])]
            assert all(47 < x < 49 for x in lat)
            assert b["trips"][0][0] == 0
        timed = sum(
            1
            for e in pq.read_table(io.BytesIO(events[d])).to_pylist()
            if e["is_timepoint"]
            and classify(e)[1] is not None
            and any(tr["trip_id"] == e["trip_id"] for tr in v["trips"])
        )
        assert sum(len(tr["timed"]) for tr in v["trips"]) == timed
