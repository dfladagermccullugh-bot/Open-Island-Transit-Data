"""Compute daily quality metrics and write them to the data branch.

    python -m pipeline.quality --pending --data data-branch \
        --raw-dir work/files --tables-dir work/tables

A day is pending when it has parsed tables and a pinned Passio version, and
``<data>/quality/days/<date>.json`` is missing or was computed by an older
``QUALITY_VERSION``. Inputs are taken from local copies when their SHA-256
matches the manifests, otherwise downloaded from Releases. After each run
``<data>/quality/daily.csv`` is rebuilt from all day files.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from pipeline import release
from pipeline.config import COLLECTION_START, REPO
from pipeline.gtfs.schedule import removed_services, scheduled_trips
from pipeline.gtfs.zipread import read_zip
from pipeline.quality.metrics import QUALITY_VERSION, day_metrics

FETCH_COLUMNS = [
    "fetch_id",
    "feed",
    "scheduled_at",
    "requested_at",
    "latency_ms",
    "http_status",
    "error_class",
    "entity_count",
    "newest_entity_ts",
    "collector_version",
]

HEADLINE = [
    ("service_date", ("service_date",)),
    ("passio_version", ("schedule", "passio_version")),
    ("scheduled_trips", ("coverage", "scheduled_trips")),
    ("trip_coverage", ("coverage", "trip_coverage")),
    ("vehicle_uptime", ("paradata", "vehiclePositions", "uptime")),
    ("trip_update_uptime", ("paradata", "tripUpdates", "uptime")),
    ("vehicle_fetch_failure_rate", ("nonresponse", "fetch", "vehiclePositions", "rate")),
    ("empty_in_service_rate", ("nonresponse", "vehicle_feed", "empty_in_service_rate")),
    ("stale_in_service_rate", ("nonresponse", "vehicle_feed", "stale_in_service_rate")),
    ("longest_gap_s_median", ("nonresponse", "trip_gaps", "longest_gap_s_median")),
    ("position_age_s_median", ("measurement", "position_age_s_median")),
    ("position_age_s_p95", ("measurement", "position_age_s_p95")),
    ("impossible_jumps", ("measurement", "impossible_jumps_over_130_kmh")),
    ("trip_id_match_rate", ("processing", "id_match", "trip_id", "match_rate")),
    ("over_coverage_share", ("coverage", "over_coverage", "share")),
    ("vehicle_latency_ms_median", ("paradata", "vehiclePositions", "latency_ms_median")),
    ("vehicles_seen", ("coverage", "vehicles_seen")),
    ("quality_version", ("quality_version",)),
]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {"days": {}}


def _sha(entry: dict[str, Any], name: str) -> str:
    return next(f["sha256"] for f in entry["files"] if f["name"] == name)


def pending(parsed: dict, static: dict, out: Path) -> list[date]:
    days = []
    for day, entry in parsed["days"].items():
        pins = static.get("days", {}).get(day, {})
        if not entry.get("release_tag") or not (pins.get("passio") or {}).get("version"):
            continue
        f = out / "days" / f"{day}.json"
        if not f.exists() or json.loads(f.read_text()).get("quality_version") != QUALITY_VERSION:
            days.append(date.fromisoformat(day))
    return sorted(days)


def compute(day: date, data: Path, raw_dir: Path, tables_dir: Path, repo: str) -> dict[str, Any]:
    d = day.isoformat()
    raw = _load(data / "manifest" / "raw.json")["days"][d]
    parsed = _load(data / "manifest" / "parsed.json")["days"][d]
    static = _load(data / "manifest" / "static.json")

    raw_name = f"raw-{d}.parquet"
    raw_path = release.fetch(raw["release_tag"], repo, raw_name, raw_dir, _sha(raw, raw_name))
    fetches = pq.read_table(raw_path, columns=FETCH_COLUMNS).to_pylist()

    veh_name = f"vehicles-{d}.parquet"
    veh_path = release.fetch(
        parsed["release_tag"], repo, veh_name, tables_dir, _sha(parsed, veh_name)
    )
    vehicles = pq.read_table(veh_path).to_pylist()

    pins = static["days"][d]
    version = static["versions"][pins["passio"]["version"]]
    ex = version["example"]
    zpath = release.fetch(ex["release_tag"], repo, ex["file"], raw_dir, ex["sha256"])
    passio = read_zip(zpath.read_bytes())
    trips = scheduled_trips(passio, day)
    ids = {
        name: {r[col] for r in _csv(passio, f"{name}.txt")}
        for name, col in (("trips", "trip_id"), ("routes", "route_id"), ("stops", "stop_id"))
    }

    holidays = []
    tv = (pins.get("trillium") or {}).get("version")
    if tv:
        tex = static["versions"][tv]["example"]
        tpath = release.fetch(tex["release_tag"], repo, tex["file"], raw_dir, tex["sha256"])
        holidays = removed_services(read_zip(tpath.read_bytes()), day)

    downloads = [x for x in static.get("downloads", {}).values() if x["day"] == d]
    extra = {
        "parser": {
            feed: {**c, "decode_error_rate": (c["decode_errors"] / c["attempted"])
                   if c["attempted"] else None}
            for feed, c in parsed["fetches"].items()
        },
        "static_downloads": [
            {k: x[k] for k in ("static_fetch_id", "source", "requested_at", "complete",
                               "problems", "missing", "version")}
            for x in sorted(downloads, key=lambda x: x["requested_at"])
        ],
    }  # fmt: skip
    m = day_metrics(day, fetches, vehicles, trips, ids, COLLECTION_START, extra)
    m["schedule"] = {
        "passio_version": pins["passio"]["version"],
        "passio_basis": pins["passio"]["basis"],
        "trillium_version": tv,
        "trillium_removes_service_today": holidays,
        "note": "Scheduled trips come from the Passio calendar, which has no holiday exceptions.",
    }
    m["inputs"] = {
        "raw_sha256": _sha(raw, raw_name),
        "vehicles_sha256": _sha(parsed, veh_name),
        "tables_version": parsed["tables_version"],
        "code_sha": os.environ.get("GITHUB_SHA"),
    }
    return m


def _csv(contents, name: str) -> list[dict[str, str]]:
    body = contents.files.get(name, b"")
    return list(csv.DictReader(body.decode("utf-8-sig", errors="replace").splitlines()))


def _get(obj: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def write_daily_csv(out: Path) -> int:
    files = sorted((out / "days").glob("*.json"))
    with (out / "daily.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([name for name, _ in HEADLINE])
        for f in files:
            m = json.loads(f.read_text())
            w.writerow([_get(m, path) for _, path in HEADLINE])
    return len(files)


def _json_default(o: Any) -> str:
    if isinstance(o, datetime | date):
        return o.isoformat()
    raise TypeError(type(o))


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute daily quality metrics.")
    ap.add_argument("--date", action="append", type=date.fromisoformat)
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--data", type=Path, default=Path("work"), help="data branch checkout")
    ap.add_argument("--raw-dir", type=Path, default=Path("work/files"))
    ap.add_argument("--tables-dir", type=Path, default=Path("work/tables"))
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    ap.add_argument("--max-days", type=int, default=7)
    args = ap.parse_args()

    out = args.data / "quality"
    days = list(args.date or [])
    if args.pending:
        days += pending(
            _load(args.data / "manifest" / "parsed.json"),
            _load(args.data / "manifest" / "static.json"),
            out,
        )
    days = sorted(set(days))[: args.max_days]
    failed = False
    for day in days:
        try:
            m = compute(day, args.data, args.raw_dir, args.tables_dir, args.repo)
            (out / "days").mkdir(parents=True, exist_ok=True)
            (out / "days" / f"{day.isoformat()}.json").write_text(
                json.dumps(m, indent=1, default=_json_default) + "\n"
            )
            print(f"{day}: coverage {m['coverage']['trip_coverage']}")
        except Exception as exc:  # keep going; one bad day must not block the rest
            failed = True
            print(f"{day}: FAILED: {exc!r}", file=sys.stderr)
    if (out / "days").exists():
        print(f"daily.csv: {write_daily_csv(out)} days")
    else:
        print("no quality files yet")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
