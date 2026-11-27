"""Compute daily quality metrics and write them to the data branch.

    python -m pipeline.quality --pending --data data-branch \
        --raw-dir work/files --tables-dir work/tables

A day is pending when it has parsed tables and a pinned Passio version, and
``<data>/quality/days/<date>.json`` is missing or was computed by an older
``QUALITY_VERSION``. Inputs are taken from local copies when their SHA-256
matches the manifests, otherwise downloaded from Releases. After each run
``<data>/quality/daily.csv`` and ``<data>/quality/gaps.json`` (randomness of
gaps, ``pipeline/quality/gaps.py``) are rebuilt from all day files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from pipeline import release
from pipeline.canceled import canceled_trips
from pipeline.columns import QUALITY_HEADLINE as HEADLINE
from pipeline.common import DayFiles, code_sha, load_json, run_order, write_csv
from pipeline.common import sha_of as _sha
from pipeline.config import COLLECTION_START, REPO
from pipeline.gtfs.holidays import no_service, service_expected
from pipeline.gtfs.schedule import removed_services, route_names, scheduled_trips
from pipeline.gtfs.tables import rows as _csv
from pipeline.gtfs.zipread import ZipContents, load_zip
from pipeline.quality import gaps
from pipeline.quality import stops as stop_metrics
from pipeline.quality.metrics import QUALITY_VERSION, day_metrics, refresh

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

# The columns of trip-update-stops that prediction error reads (the table is
# the largest of the day; reading it whole took most of this stage's time).
PREDICTION_COLUMNS = [
    "trip_id",
    "stop_sequence",
    "first_requested_at",
    "last_requested_at",
    "arrival_time",
    "departure_time",
]

# The columns of trip-updates that the cancellation count and the refresh
# rate read.
TRIP_UPDATE_COLUMNS = [
    "trip_key",
    "trip_id",
    "trip_schedule_relationship",
    "trip_update_ts",
    "first_requested_at",
    "last_requested_at",
    "times_seen",
]


def _load(path: Path) -> dict[str, Any]:
    return load_json(path, {"days": {}})


def _events_sha(events: dict, day: str) -> str | None:
    entry = events.get("days", {}).get(day)
    if not entry or not entry.get("release_tag"):
        return None
    return _sha(entry, f"stop-events-{day}.parquet")


def pending(
    parsed: dict,
    static: dict,
    out: Path,
    events: dict | None = None,
    files: DayFiles | None = None,
) -> list[date]:
    """Days with no quality file, another QUALITY_VERSION, or parsed tables,
    stop events, a schedule version or a timepoints list that changed since
    the file was computed."""
    events = events or {"days": {}}
    files = files or DayFiles(out / "days")
    days = []
    for day, entry in parsed["days"].items():
        pins = static.get("days", {}).get(day, {})
        if not entry.get("release_tag") or not (pins.get("passio") or {}).get("version"):
            continue
        old = files.read(day)
        if old is None:
            days.append(date.fromisoformat(day))
            continue
        inputs, schedule = old.get("inputs") or {}, old.get("schedule") or {}
        used_timepoints = (old.get("processing") or {}).get("timepoints") or {}
        if (
            old.get("quality_version") != QUALITY_VERSION
            or inputs.get("stop_events_sha256") != _events_sha(events, day)
            or inputs.get("vehicles_sha256") != _sha(entry, f"vehicles-{day}.parquet")
            or inputs.get("tables_version") != entry.get("tables_version")
            or schedule.get("service_expected", True) != service_expected(day)
            or schedule.get("passio_version") != pins["passio"]["version"]
            or used_timepoints.get("folder") != pins.get("timepoints")
        ):
            days.append(date.fromisoformat(day))
    return sorted(days)


def previous_vehicles(
    out: Path, day: date, days: int = 30, files: DayFiles | None = None
) -> dict[str, list[str]]:
    """Vehicle ids seen on each of the ``days`` days before ``day`` that
    have a quality file."""
    files = files or DayFiles(out / "days")
    found = {}
    for k in range(1, days + 1):
        d = (day - timedelta(days=k)).isoformat()
        m = files.read(d)
        if m is not None:
            ids = (m.get("coverage") or {}).get("vehicle_ids")
            if ids is not None:
                found[d] = ids
    return found


def terminals(contents: ZipContents) -> list[tuple[float, float]]:
    """(lat, lon) of every stop that begins or ends a trip."""
    stops = {s["stop_id"]: s for s in _csv(contents, "stops.txt")}
    ends: dict[str, list[tuple[int, str]]] = {}
    for r in _csv(contents, "stop_times.txt"):
        ends.setdefault(r["trip_id"], []).append((int(r["stop_sequence"]), r["stop_id"]))
    ids = set()
    for seq in ends.values():
        seq.sort()
        ids |= {seq[0][1], seq[-1][1]}
    return sorted(
        (float(stops[i]["stop_lat"]), float(stops[i]["stop_lon"])) for i in ids if i in stops
    )


def event_metrics(
    day: date,
    data: Path,
    events_dir: Path,
    repo: str,
    predictions: list[dict],
    vehicles: list[dict],
    passio: ZipContents,
    static_trip_ids: set[str],
    todays_trip_ids: set[str],
    files: DayFiles | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Metrics that need stop events; (None, {}) if the day has none yet.

    ``events_dir`` is where the events stage left the day's stop events and
    trip tracks; a file that is already there with the recorded hash is used
    as it is and not downloaded again."""
    d = day.isoformat()
    entry = _load(data / "manifest" / "events.json")["days"].get(d)
    if not entry or not entry.get("release_tag"):
        return None, {}

    def table(name: str) -> list[dict]:
        f = f"{name}-{d}.parquet"
        path = release.fetch(entry["release_tag"], repo, f, events_dir, _sha(entry, f))
        return pq.read_table(path).to_pylist()

    events = table("stop-events")
    tracks = table("trip-tracks")
    out = data / "quality"
    today_ids = {v["vehicle_id"] for v in vehicles if v["vehicle_id"] is not None}
    metrics = {
        "events_version": entry["events_version"],
        "stop_coverage": stop_metrics.stop_coverage(events, COLLECTION_START),
        "shape_fit": stop_metrics.shape_fit(tracks),
        "retraced_shapes": stop_metrics.retraced_shapes(
            _csv(passio, "shapes.txt"), {t["shape_id"] for t in tracks if t["shape_id"]}
        ),
        "assignment": stop_metrics.assignment(vehicles, tracks, static_trip_ids, todays_trip_ids),
        "prediction_error": stop_metrics.prediction_error(predictions, events),
        "jitter": stop_metrics.jitter(vehicles, terminals(passio)),
        "missing_vehicles": stop_metrics.missing_vehicles(
            today_ids, previous_vehicles(out, day, files=files)
        ),
    }
    return metrics, {"stop_events_sha256": _sha(entry, f"stop-events-{d}.parquet")}


def compute(
    day: date,
    data: Path,
    raw_dir: Path,
    tables_dir: Path,
    repo: str,
    events_dir: Path | None = None,
    files: DayFiles | None = None,
) -> dict[str, Any]:
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

    def parsed_table(name: str, columns: list[str] | None = None) -> list[dict]:
        f = f"{name}-{d}.parquet"
        path = release.fetch(parsed["release_tag"], repo, f, tables_dir, _sha(parsed, f))
        return pq.read_table(path, columns=columns).to_pylist()

    trip_updates = parsed_table("trip-updates", TRIP_UPDATE_COLUMNS)
    alerts = parsed_table("alerts")
    # The largest table of the day: read once, for the count of stops the
    # feed flagged as skipped here and for the prediction errors further on.
    stop_updates = parsed_table(
        "trip-update-stops", [*PREDICTION_COLUMNS, "stop_schedule_relationship"]
    )
    skipped = sum(r["stop_schedule_relationship"] == "SKIPPED" for r in stop_updates)

    pins = static["days"][d]
    version = static["versions"][pins["passio"]["version"]]
    ex = version["example"]
    zpath = release.fetch(ex["release_tag"], repo, ex["file"], raw_dir, ex["sha256"])
    passio = load_zip(zpath, ex["sha256"])
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
        holidays = removed_services(load_zip(tpath, tex["sha256"]), day)

    downloads = [x for x in static.get("downloads", {}).values() if x["day"] == d]
    extra: dict[str, Any] = {
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
    tp_key = pins.get("timepoints")
    tp_summary = data / "timepoints" / str(tp_key) / "summary.json"
    if tp_key and tp_summary.exists():
        tp = json.loads(tp_summary.read_text())
        extra["timepoints"] = {
            "folder": tp_key,
            "reference": tp["reference"],
            "printed_names": tp["printed_names"],
            "resolved_names": tp["resolved_names"],
            "resolved_share": round(tp["resolved_names"] / tp["printed_names"], 4)
            if tp["printed_names"]
            else None,
            "timepoint_route_stops": tp["timepoint_route_stops"],
        }
    else:
        extra["timepoints"] = None
    canceled = canceled_trips(trip_updates, alerts, trips, vehicles)
    m = day_metrics(day, fetches, vehicles, trips, ids, COLLECTION_START, extra, canceled)
    m["coverage"]["stop_updates_flagged_skipped"] = skipped
    m["coverage"]["alerts_seen"] = len(alerts)
    m["paradata"]["tripUpdates"]["refresh"] = refresh(trip_updates)
    m["coverage"]["vehicle_ids"] = sorted(
        {v["vehicle_id"] for v in vehicles if v["vehicle_id"] is not None}
    )
    m["events"], event_inputs = event_metrics(
        day, data, events_dir or tables_dir, repo, stop_updates, vehicles, passio,
        ids["trips"], {t.trip_id for t in trips}, files,
    )  # fmt: skip
    m["routes"] = route_names(passio)
    m["schedule"] = {
        "passio_version": pins["passio"]["version"],
        "passio_basis": pins["passio"]["basis"],
        "passio_changed_during_day": pins["passio"].get("changed_during_day", False),
        "trillium_version": tv,
        "trillium_removes_service_today": holidays,
        "service_expected": service_expected(day),
        "no_service_day": no_service(day),
        "note": "Scheduled trips come from the Passio calendar, which has no holiday exceptions. "
        "On a day Island Transit lists as having no service, service_expected is false and "
        "the day is left out of figures that pool several days.",
    }
    m["inputs"] = {
        "raw_sha256": _sha(raw, raw_name),
        "vehicles_sha256": _sha(parsed, veh_name),
        "tables_version": parsed["tables_version"],
        "stop_events_sha256": None,
        **event_inputs,
        "code_sha": code_sha(),
    }
    return m


def _get(obj: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def write_daily_csv(out: Path, files: DayFiles | None = None) -> int:
    days = (files or DayFiles(out / "days")).all()
    write_csv(
        out / "daily.csv",
        [name for name, _ in HEADLINE],
        ([_get(m, path) for _, path in HEADLINE] for m in days),
    )
    return len(days)


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
    ap.add_argument(
        "--events-dir",
        type=Path,
        help="where the events stage wrote stop events and trip tracks (default: --tables-dir)",
    )
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    ap.add_argument("--max-days", type=int, default=7)
    args = ap.parse_args()

    out = args.data / "quality"
    files = DayFiles(out / "days")
    days = list(args.date or [])
    if args.pending:
        days += pending(
            _load(args.data / "manifest" / "parsed.json"),
            _load(args.data / "manifest" / "static.json"),
            out,
            _load(args.data / "manifest" / "events.json"),
            files,
        )
    processed = set(files.days())
    days = run_order(days, processed, args.max_days)
    failed = False
    for day in days:
        try:
            m = compute(
                day, args.data, args.raw_dir, args.tables_dir, args.repo, args.events_dir, files
            )
            files.write(day.isoformat(), json.dumps(m, indent=1, default=_json_default) + "\n")
            print(f"{day}: coverage {m['coverage']['trip_coverage']}")
        except Exception as exc:  # keep going; one bad day must not block the rest
            failed = True
            print(f"{day}: FAILED: {exc!r}", file=sys.stderr)
            traceback.print_exc()
    if (out / "days").exists():
        print(f"daily.csv: {write_daily_csv(out, files)} days")
        g = gaps.write(out, files)
        print(f"gaps.json: {g['trips']} trips, {g['unobserved']} unobserved; {g['model_status']}")
    else:
        print("no quality files yet")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
