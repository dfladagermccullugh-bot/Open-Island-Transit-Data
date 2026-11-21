"""Infer stop events and the daily on-time summary.

    python -m pipeline.events --pending --data data-branch \
        --raw-dir work/files --tables-dir work/tables --out work/events --release

A day is pending when it has parsed tables, a pinned Passio version and a
timepoints folder, and ``manifest/events.json`` lacks it or records an older
``EVENTS_VERSION`` or a different timepoints folder. Inputs come from local
copies when their SHA-256 matches, otherwise from Releases.

Writes ``stop-events-<date>.parquet`` and ``trip-tracks-<date>.parquet``
(uploaded with --release to ``parsed-YYYY-MM``), the day's entry in
``manifest/events.json``, and ``ontime/days/<date>.json`` plus
``ontime/daily.csv``, ``ontime/routes.csv`` and ``ontime/recent.json``
(``pipeline/events/routes.py``) on the data branch. A day is also rebuilt
when its on-time summary was made by an older ``ONTIME_VERSION``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline import release
from pipeline.config import COLLECTION_START, REPO
from pipeline.events.infer import EVENTS_VERSION, infer_day
from pipeline.events.ontime import ONTIME_VERSION, summarize
from pipeline.events.routes import write_routes
from pipeline.events.validate import validate
from pipeline.gtfs.schedule import route_names
from pipeline.gtfs.zipread import read_zip

TS = pa.timestamp("us", tz="UTC")
EVENT_SCHEMA = pa.schema(
    [
        ("trip_id", pa.string()),
        ("route_id", pa.string()),
        ("vehicle_key", pa.string()),
        ("stop_sequence", pa.int32()),
        ("stop_id", pa.string()),
        ("is_first_stop", pa.bool_()),
        ("is_last_stop", pa.bool_()),
        ("is_timepoint", pa.bool_()),
        ("stop_distance_m", pa.float64()),
        ("scheduled_arrival", TS),
        ("scheduled_departure", TS),
        ("arrival_time", TS),
        ("arrival_gap_s", pa.float64()),
        ("arrival_status", pa.string()),
        ("departure_time", TS),
        ("departure_gap_s", pa.float64()),
        ("departure_status", pa.string()),
    ]
)
TRACK_SCHEMA = pa.schema(
    [
        ("trip_id", pa.string()),
        ("route_id", pa.string()),
        ("scheduled_start", TS),
        ("scheduled_end", TS),
        ("vehicles_with_trip_id", pa.int32()),
        ("vehicle_key", pa.string()),
        ("position_reports", pa.int32()),
        ("reports_used", pa.int32()),
        ("reports_before_start", pa.int32()),
        ("reports_after_end", pa.int32()),
        ("off_shape_reports", pa.int32()),
        ("wrong_direction_reports", pa.int32()),
        ("shape_id", pa.string()),
        ("shape_length_m", pa.float64()),
        ("max_distance_m", pa.float64()),
        ("shape_skips", pa.int32()),
        ("skipped_m", pa.float64()),
    ]
)
DAILY = [
    "service_date",
    "scheduled",
    "observed",
    "observed_share",
    "on_time",
    "early",
    "late",
    "on_time_share_of_observed",
    "deviation_s_median",
    "feed_agreement_share",
    "ontime_version",
]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {"version": 1, "days": {}}


def _save(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["days"] = dict(sorted(manifest["days"].items()))
    path.write_text(json.dumps(manifest, indent=1, default=str) + "\n")


def _sha(entry: dict[str, Any], name: str) -> str:
    return next(f["sha256"] for f in entry["files"] if f["name"] == name)


def _file_entry(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"name": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def pending(data: Path) -> list[date]:
    parsed = _load(data / "manifest" / "parsed.json")["days"]
    static = _load(data / "manifest" / "static.json").get("days", {})
    done = _load(data / "manifest" / "events.json")["days"]
    out = []
    for day, entry in parsed.items():
        pins = static.get(day, {})
        if not entry.get("release_tag") or not pins.get("timepoints"):
            continue
        prev = done.get(day)
        if (
            prev is None
            or prev.get("events_version") != EVENTS_VERSION
            or prev.get("ontime_version") != ONTIME_VERSION
            or prev.get("timepoints") != pins["timepoints"]
        ):
            out.append(date.fromisoformat(day))
    return sorted(out)


def build(day: date, data: Path, raw_dir: Path, tables_dir: Path, out: Path, repo: str) -> dict:
    d = day.isoformat()
    parsed = _load(data / "manifest" / "parsed.json")["days"][d]
    static = _load(data / "manifest" / "static.json")
    pins = static["days"][d]

    veh_name = f"vehicles-{d}.parquet"
    veh_path = release.fetch(
        parsed["release_tag"], repo, veh_name, tables_dir, _sha(parsed, veh_name)
    )
    vehicles = pq.read_table(veh_path).to_pylist()

    version = static["versions"][pins["passio"]["version"]]
    ex = version["example"]
    zpath = release.fetch(ex["release_tag"], repo, ex["file"], raw_dir, ex["sha256"])
    passio = read_zip(zpath.read_bytes())

    tp_file = data / "timepoints" / pins["timepoints"] / "timepoints.csv"
    pairs = {
        (r["passio_route_id"], r["passio_stop_id"])
        for r in csv.DictReader(tp_file.open())
        if r["passio_stop_id"]
    }

    events, tracks = infer_day(day, passio, vehicles, pairs)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for name, rows, schema in (
        ("stop-events", events, EVENT_SCHEMA),
        ("trip-tracks", tracks, TRACK_SCHEMA),
    ):
        table = pa.table({c: [r[c] for r in rows] for c in schema.names}, schema=schema)
        table = table.replace_schema_metadata({"service_date": d, "events_version": EVENTS_VERSION})
        path = out / f"{name}-{d}.parquet"
        pq.write_table(table, path, compression="zstd", compression_level=9)
        files.append(_file_entry(path))

    names = {s["stop_id"]: s.get("stop_name", "") for s in _stops(passio)}
    summary = summarize(events, COLLECTION_START, names)
    summary.update(
        {
            "service_date": d,
            "passio_version": pins["passio"]["version"],
            "timepoints": pins["timepoints"],
            "events_version": EVENTS_VERSION,
            "routes": route_names(passio),
            "validation": validate(events, vehicles),
        }
    )
    summary["feed_agreement_share"] = summary["validation"]["feed_agreement"][
        "within_tolerance_share"
    ]
    return {
        "entry": {
            "service_date": d,
            "events_version": EVENTS_VERSION,
            "ontime_version": ONTIME_VERSION,
            "passio_version": pins["passio"]["version"],
            "timepoints": pins["timepoints"],
            "vehicles_sha256": _sha(parsed, veh_name),
            "rows": {"stop-events": len(events), "trip-tracks": len(tracks)},
            "files": files,
        },
        "ontime": summary,
    }


_NUMBER_LIST = re.compile(r"\[\s*(-?\d+(?:\.\d+)?(?:,\s*-?\d+(?:\.\d+)?)*)\s*\]")


def dumps_day(obj: dict[str, Any]) -> str:
    """Indented JSON with lists of numbers kept on one line."""
    text = json.dumps(obj, indent=1)
    return _NUMBER_LIST.sub(lambda m: "[" + re.sub(r"\s+", "", m.group(1)) + "]", text) + "\n"


def _stops(passio) -> list[dict[str, str]]:
    raw = passio.files.get("stops.txt")
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))) if raw else []


def write_daily(ontime_dir: Path) -> int:
    files = sorted((ontime_dir / "days").glob("*.json"))
    with (ontime_dir / "daily.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(DAILY)
        for f in files:
            m = json.loads(f.read_text())
            w.writerow([m.get(k) for k in DAILY])
    return len(files)


def main() -> int:
    ap = argparse.ArgumentParser(description="Infer stop events and on-time summaries.")
    ap.add_argument("--date", action="append", type=date.fromisoformat)
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--data", type=Path, default=Path("work"), help="data branch checkout")
    ap.add_argument("--raw-dir", type=Path, default=Path("work/files"))
    ap.add_argument("--tables-dir", type=Path, default=Path("work/tables"))
    ap.add_argument("--out", type=Path, default=Path("work/events"))
    ap.add_argument("--release", action="store_true", help="upload to GitHub Releases")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    ap.add_argument("--max-days", type=int, default=7)
    args = ap.parse_args()

    days = list(args.date or [])
    if args.pending:
        days += pending(args.data)
    days = sorted(set(days))[: args.max_days]
    if not days:
        print("no events to build")
        return 0

    manifest_path = args.data / "manifest" / "events.json"
    manifest = _load(manifest_path)
    ontime_dir = args.data / "ontime"
    failed = False
    for day in days:
        try:
            built = build(day, args.data, args.raw_dir, args.tables_dir, args.out, args.repo)
            entry = built["entry"]
            if args.release:
                tag = f"parsed-{day:%Y-%m}"
                release.ensure_release(
                    tag,
                    args.repo,
                    title=f"Parsed tables, {day:%B %Y}",
                    notes="Parsed daily tables and inferred stop events. See docs/codebook.md.",
                )
                release.upload(tag, args.repo, args.out, entry["files"])
                entry["release_tag"] = tag
                entry["built_at"] = datetime.now(UTC).isoformat(timespec="seconds")
                entry["code_sha"] = os.environ.get("GITHUB_SHA")
                manifest["days"][day.isoformat()] = entry
                _save(manifest_path, manifest)
                (ontime_dir / "days").mkdir(parents=True, exist_ok=True)
                (ontime_dir / "days" / f"{day.isoformat()}.json").write_text(
                    dumps_day(built["ontime"])
                )
            o = built["ontime"]
            print(
                f"{day}: {entry['rows']}; timepoint events {o['scheduled']}, observed "
                f"{o['observed_share']}, on time {o['on_time_share_of_observed']}"
            )
        except Exception as exc:  # keep going; one bad day must not block the rest
            failed = True
            print(f"{day}: FAILED: {exc!r}", file=sys.stderr)
    if (ontime_dir / "days").exists():
        print(f"ontime/daily.csv: {write_daily(ontime_dir)} days")
        print(f"ontime/routes.csv and recent.json: {write_routes(ontime_dir)} days")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
