"""``scripts/reproduce.py`` rebuilds a published day from its raw files and
finds every file identical; a published file that does not match what the
code makes is reported. Runs on the made-up archive of the golden test, with
its release folder standing in for GitHub."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

from tests.test_golden import REPO_ROOT, replay  # noqa: F401  (replay is a fixture)

spec = importlib.util.spec_from_file_location("reproduce", REPO_ROOT / "scripts" / "reproduce.py")
reproduce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reproduce)


def run(world: Path, work: Path, *days: str) -> tuple[int, dict]:
    shutil.copytree(world / "releases", work / "releases")  # nothing is downloaded
    report = work / "report.json"
    args = ["--data", str(world / "data-branch"), "--work", str(work), "--report", str(report)]
    for d in days:
        args += ["--date", d]
    code = reproduce.main(args)
    return code, json.loads(report.read_text())


def test_a_published_day_rebuilds_identically(tmp_path, replay, monkeypatch):  # noqa: F811
    world = tmp_path / "world"
    assert replay(world) == 0
    monkeypatch.setattr(sys, "argv", sys.argv)
    code, report = run(world, tmp_path / "work", "2026-11-24")
    assert code == 0 and report["all_identical"] and report["days_compared"] == 1
    [day] = report["days"]
    names = {f["name"] for f in day["files"]}
    assert {"vehicles-2026-11-24.parquet", "stop-events-2026-11-24.parquet"} <= names
    assert all(f["same"] for f in day["files"] + day["day_files"])
    assert report["command"] == "python scripts/reproduce.py --date 2026-11-24"


def test_a_file_that_does_not_match_is_reported(tmp_path, replay, monkeypatch):  # noqa: F811
    world = tmp_path / "world"
    assert replay(world) == 0
    monkeypatch.setattr(sys, "argv", sys.argv)
    # Change one published figure: the rebuilt day file no longer matches.
    path = world / "data-branch" / "ontime" / "days" / "2026-11-24.json"
    day = json.loads(path.read_text())
    day["on_time"] += 1
    path.write_text(json.dumps(day))
    code, report = run(world, tmp_path / "work", "2026-11-24")
    assert code == 1 and not report["all_identical"]
    [ontime] = [f for f in report["days"][0]["day_files"] if f["name"].startswith("ontime/")]
    assert ontime["same"] is False


def test_a_day_made_by_an_older_version_is_not_compared(tmp_path, replay, monkeypatch):  # noqa: F811
    world = tmp_path / "world"
    assert replay(world) == 0
    monkeypatch.setattr(sys, "argv", sys.argv)
    path = world / "data-branch" / "manifest" / "events.json"
    m = json.loads(path.read_text())
    m["days"]["2026-11-24"]["events_version"] = "events/0.0.1"
    path.write_text(json.dumps(m))
    code, report = run(world, tmp_path / "work", "2026-11-24")
    assert code == 2 and report["skipped"][0]["service_date"] == "2026-11-24"
