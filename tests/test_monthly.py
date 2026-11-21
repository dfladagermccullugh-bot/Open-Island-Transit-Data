import hashlib
import json
from datetime import date

from pipeline import monthly
from pipeline.check import check


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def quality_day(day, scheduled, observed, ended_before=0, uptime_ok=1900):
    feed = {"slots_expected": 2000, "slots_ok": uptime_ok, "uptime": uptime_ok / 2000,
            "collector_versions": ["collect/0.2.0"]}  # fmt: skip
    fetch = {"attempted": uptime_ok, "failed": 0}
    return {
        "service_date": day,
        "quality_version": "quality/0.3.0",
        "scheduled_service": {"trips_ended_before_collection": ended_before},
        "paradata": {
            "observed_from": f"{day}T21:24:40+00:00",
            **{f: dict(feed) for f in monthly.FEEDS},
        },  # fmt: skip
        "nonresponse": {"fetch": {f: dict(fetch) for f in monthly.FEEDS}},
        "coverage": {
            "scheduled_trips": scheduled,
            "observed_scheduled_trips": observed,
            "trip_coverage": observed / scheduled,
            "routes_never_reporting": [],
        },
    }


def ontime_day(day, route_counts):
    by_route = {}
    for route, (sch, obs, on, early, late) in route_counts.items():
        by_route[route] = {"scheduled": sch, "observed": obs, "on_time": on, "early": early,
                           "late": late}  # fmt: skip
    totals = {k: sum(r[k] for r in by_route.values()) for k in monthly.ONTIME_COUNTS}
    return {
        "service_date": day,
        "ontime_version": "ontime/0.1.1",
        "definition": {"window_s": [-60, 300]},
        **totals,
        "on_time_share_of_observed": totals["on_time"] / totals["observed"],
        "deviation_s_median": 120.0,
        "by_route": by_route,
        "routes": {
            "r1": {"short_name": "1", "long_name": "One"},
            "r10": {"short_name": "10", "long_name": "Ten"},
            "r2c": {"short_name": "2C", "long_name": "Two C"},
        },  # fmt: skip
    }


def make_branch(root, days):
    raw, parsed = {}, {}
    for day in days:
        raw[day] = {"release_tag": f"raw-{day[:7]}", "files": [
            {"name": f"raw-{day}.parquet", "bytes": 100, "sha256": "a" * 64}]}  # fmt: skip
        parsed[day] = {"release_tag": f"parsed-{day[:7]}", "tables_version": "tables/0.1.0",
                       "files": [{"name": f"vehicles-{day}.parquet", "bytes": 50,
                                  "sha256": "b" * 64}]}  # fmt: skip
        write(root / f"quality/days/{day}.json", quality_day(day, 100, 90))
        write(root / f"ontime/days/{day}.json",
              ontime_day(day, {"r10": (10, 8, 6, 0, 2), "r1": (20, 20, 10, 1, 9),
                               "r2c": (5, 4, 4, 0, 0)}))  # fmt: skip
    write(root / "manifest/raw.json", {"days": raw})
    write(root / "manifest/parsed.json", {"days": parsed})
    write(root / "manifest/events.json", {"days": {}})
    rows = "".join(f"{d},passio-x,100,0.9,,quality/0.3.0\n" for d in days)
    (root / "quality/daily.csv").write_text(
        "service_date,passio_version,scheduled_trips,trip_coverage,vehicles_missing_vs_30d,"
        "quality_version\n" + rows
    )


def test_closed_months_needs_last_day_or_grace(tmp_path):
    make_branch(tmp_path, ["2026-11-19", "2026-11-20"])
    assert monthly.closed_months(tmp_path, date(2026, 11, 25)) == []
    assert monthly.closed_months(tmp_path, date(2026, 12, 6)) == []
    assert monthly.closed_months(tmp_path, date(2026, 12, 7)) == ["2026-11"]

    make_branch(tmp_path, ["2026-11-29", "2026-11-30"])
    assert monthly.closed_months(tmp_path, date(2026, 12, 1)) == ["2026-11"]
    (tmp_path / "quality/days/2026-11-30.json").unlink()
    assert monthly.closed_months(tmp_path, date(2026, 12, 1)) == []


def test_build_pools_counts_and_lists_missing_days(tmp_path):
    make_branch(tmp_path, ["2026-11-28", "2026-11-30"])
    write(tmp_path / "quality/days/2026-11-28.json",
          quality_day("2026-11-28", 100, 90, ended_before=40, uptime_ok=1000))  # fmt: skip
    r = monthly.build(tmp_path, "2026-11", date(2026, 12, 1))

    assert r["period"]["days_missing"] == ["2026-11-29"]
    assert [d["service_date"] for d in r["period"]["partial_days"]] == ["2026-11-28"]
    assert r["collection"]["vehiclePositions"]["uptime"] == round(2900 / 4000, 6)
    assert r["collection"]["vehiclePositions"]["uptime_by_day"]["min_date"] == "2026-11-28"
    assert r["coverage"]["trip_coverage"] == 0.9
    o = r["ontime"]
    assert (o["scheduled"], o["observed"], o["on_time"]) == (70, 64, 40)
    assert o["on_time_share_of_observed"] == round(40 / 64, 6)
    assert list(o["by_route"]) == ["r1", "r2c", "r10"]
    assert o["by_route"]["r10"]["on_time_share_of_observed"] == 0.75
    assert r["daily_headline"]["trip_coverage"]["median"] == 0.9
    assert "vehicles_missing_vs_30d" not in r["daily_headline"]
    assert r["releases"]["raw-2026-11"]["files"] == 2
    assert r["versions"]["tables"] == ["tables/0.1.0"]

    md = monthly.markdown(r)
    assert "Missing days: 2026-11-29." in md
    assert "| 2C | 10 | 8 | 100.0% |" in md


def test_checksums_text_matches_report(tmp_path):
    make_branch(tmp_path, ["2026-11-30"])
    r = monthly.build(tmp_path, "2026-11")
    text = monthly.sums_text(monthly.checksums(tmp_path, "2026-11")["raw-2026-11"])
    assert text == f"{'a' * 64}  raw-2026-11-30.parquet\n"
    rel = r["releases"]["raw-2026-11"]
    assert rel["checksums_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert rel["checksums_bytes"] == len(text)


def test_digest_changes_with_inputs(tmp_path):
    make_branch(tmp_path, ["2026-11-30"])
    before = monthly.inputs_digest(tmp_path, "2026-11")
    assert before == monthly.inputs_digest(tmp_path, "2026-11")
    write(tmp_path / "quality/days/2026-11-30.json", quality_day("2026-11-30", 100, 80))
    assert monthly.inputs_digest(tmp_path, "2026-11") != before


def test_main_writes_once_and_publishes(tmp_path, monkeypatch):
    make_branch(tmp_path, ["2026-11-30"])
    uploads, notes = [], []
    monkeypatch.setattr(monthly.release, "ensure_release", lambda *a, **k: None)

    def upload(tag, repo, out, files):
        uploads.append((tag, files[0]["name"]))

    monkeypatch.setattr(monthly.release, "upload", upload)
    monkeypatch.setattr(monthly.release, "set_notes", lambda tag, repo, n: notes.append(tag))
    argv = ["x", "--data", str(tmp_path), "--release", "--today", "2026-12-01",
            "--work", str(tmp_path / "work")]  # fmt: skip
    monkeypatch.setattr("sys.argv", argv)
    assert monthly.main() == 0
    assert sorted(uploads) == [("parsed-2026-11", "SHA256SUMS-parsed-2026-11.txt"),
                               ("raw-2026-11", "SHA256SUMS-raw-2026-11.txt")]  # fmt: skip
    assert sorted(notes) == ["parsed-2026-11", "raw-2026-11"]
    index = json.loads((tmp_path / "reports/index.json").read_text())
    assert [e["month"] for e in index] == ["2026-11"]
    assert (tmp_path / "reports/2026-11.md").exists()

    uploads.clear()
    assert monthly.main() == 0
    assert uploads == []  # unchanged inputs: nothing rebuilt


def test_check_reports_missing_checksum_file(tmp_path):
    make_branch(tmp_path, ["2026-11-30"])
    r = monthly.build(tmp_path, "2026-11")
    monthly.write(tmp_path / "reports", r)
    releases = {
        "raw-2026-11": {"raw-2026-11-30.parquet": 100},
        "parsed-2026-11": {"vehicles-2026-11-30.parquet": 50},
    }
    problems, _ = check(tmp_path, lambda tag: releases[tag])
    assert len(problems) == 2 and all("SHA256SUMS" in p for p in problems)
    for tag, rel in r["releases"].items():
        releases[tag][rel["checksums_file"]] = rel["checksums_bytes"]
    assert check(tmp_path, lambda tag: releases[tag])[0] == []
