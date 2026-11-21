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
import csv
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
from pipeline.common import load_json, run_order
from pipeline.common import sha_of as _sha
from pipeline.config import COLLECTION_START, REPO
from pipeline.gtfs.holidays import no_service, service_expected
from pipeline.gtfs.schedule import removed_services, route_names, scheduled_trips
from pipeline.gtfs.tables import rows as _csv
from pipeline.gtfs.zipread import read_zip
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

HEADLINE = [
    ("service_date", ("service_date",)),
    ("service_expected", ("schedule", "service_expected")),
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
    ("timepoint_coverage", ("events", "stop_coverage", "timepoints", "coverage")),
    ("stop_coverage", ("events", "stop_coverage", "all_stops", "coverage")),
    ("off_shape_share", ("events", "shape_fit", "off_shape_share")),
    ("prediction_mae_s_5min", ("events", "prediction_error", "by_horizon_min", "5", "mae_s")),
    (
        "prediction_median_abs_s_5min",
        ("events", "prediction_error", "by_horizon_min", "5", "median_abs_s"),
    ),
    (
        "prediction_over_30min_share_5min",
        ("events", "prediction_error", "by_horizon_min", "5", "over_30min_share"),
    ),
    (
        "prediction_within_60s_5min",
        ("events", "prediction_error", "by_horizon_min", "5", "within_60s_share"),
    ),
    ("jitter_m_p95", ("events", "jitter", "jitter_m_p95")),
    ("unassignable_trip_ids", ("events", "assignment", "unassignable")),
    ("vehicles_missing_vs_30d", ("events", "missing_vehicles", "missing_count")),
    ("trip_id_match_rate", ("processing", "id_match", "trip_id", "match_rate")),
    ("over_coverage_share", ("coverage", "over_coverage", "share")),
    ("vehicle_latency_ms_median", ("paradata", "vehiclePositions", "latency_ms_median")),
    ("vehicles_seen", ("coverage", "vehicles_seen")),
    ("timetable_reference", ("processing", "timepoints", "reference")),
    ("timepoint_names_resolved_share", ("processing", "timepoints", "resolved_share")),
    ("canceled_trips", ("coverage", "canceled_trips")),
    ("trip_coverage_excluding_canceled", ("coverage", "trip_coverage_excluding_canceled")),
    ("trip_update_changed_share", ("paradata", "tripUpdates", "refresh", "changed_share")),
    (
        "trip_update_interval_s_median",
        ("paradata", "tripUpdates", "refresh", "update_interval_s_median"),
    ),
    ("passio_basis", ("schedule", "passio_basis")),
    ("passio_changed_during_day", ("schedule", "passio_changed_during_day")),
    ("quality_version", ("quality_version",)),
]


def _load(path: Path) -> dict[str, Any]:
    return load_json(path, {"days": {}})


def _events_sha(events: dict, day: str) -> str | None:
    entry = events.get("days", {}).get(day)
    if not entry or not entry.get("release_tag"):
        return None
    return _sha(entry, f"stop-events-{day}.parquet")


def pending(parsed: dict, static: dict, out: Path, events: dict | None = None) -> list[date]:
    """Days with no quality file, another QUALITY_VERSION, or parsed tables,
    stop events, a schedule version or a timepoints list that changed since
    the file was computed."""
    events = events or {"days": {}}
    days = []
    for day, entry in parsed["days"].items():
        pins = static.get("days", {}).get(day, {})
        if not entry.get("release_tag") or not (pins.get("passio") or {}).get("version"):
            continue
        f = out / "days" / f"{day}.json"
        if not f.exists():
            days.append(date.fromisoformat(day))
            continue
        old = json.loads(f.read_text())
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


def previous_vehicles(out: Path, day: date, days: int = 30) -> dict[str, list[str]]:
    """Vehicle ids seen on each of the ``days`` days before ``day`` that
    have a quality file."""
    found = {}
    for k in range(1, days + 1):
        d = (day - timedelta(days=k)).isoformat()
        f = out / "days" / f"{d}.json"
        if f.exists():
            ids = (json.loads(f.read_text()).get("coverage") or {}).get("vehicle_ids")
            if ids is not None:
                found[d] = ids
    return found


def terminals(contents) -> list[tuple[float, float]]:
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
    tables_dir: Path,
    repo: str,
    parsed: dict,
    vehicles: list[dict],
    passio,
    static_trip_ids: set[str],
    todays_trip_ids: set[str],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Metrics that need stop events; (None, {}) if the day has none yet."""
    d = day.isoformat()
    entry = _load(data / "manifest" / "events.json")["days"].get(d)
    if not entry or not entry.get("release_tag"):
        return None, {}

    def table(name: str, source: dict, columns: list[str] | None = None) -> list[dict]:
        f = f"{name}-{d}.parquet"
        path = release.fetch(source["release_tag"], repo, f, tables_dir, _sha(source, f))
        return pq.read_table(path, columns=columns).to_pylist()

    events = table("stop-events", entry)
    tracks = table("trip-tracks", entry)
    predictions = table("trip-update-stops", parsed, PREDICTION_COLUMNS)
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
        "missing_vehicles": stop_metrics.missing_vehicles(today_ids, previous_vehicles(out, day)),
    }
    return metrics, {"stop_events_sha256": _sha(entry, f"stop-events-{d}.parquet")}


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

    def parsed_table(name: str, columns: list[str] | None = None) -> list[dict]:
        f = f"{name}-{d}.parquet"
        path = release.fetch(parsed["release_tag"], repo, f, tables_dir, _sha(parsed, f))
        return pq.read_table(path, columns=columns).to_pylist()

    trip_updates = parsed_table("trip-updates", TRIP_UPDATE_COLUMNS)
    alerts = parsed_table("alerts")
    skipped = sum(
        r["stop_schedule_relationship"] == "SKIPPED"
        for r in parsed_table("trip-update-stops", ["stop_schedule_relationship"])
    )

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
        day, data, tables_dir, repo, parsed, vehicles, passio, ids["trips"],
        {t.trip_id for t in trips},
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
        "code_sha": os.environ.get("GITHUB_SHA"),
    }
    return m


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
            _load(args.data / "manifest" / "events.json"),
        )
    processed = {f.stem for f in (out / "days").glob("*.json")}
    days = run_order(days, processed, args.max_days)
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
            traceback.print_exc()
    if (out / "days").exists():
        print(f"daily.csv: {write_daily_csv(out)} days")
        g = gaps.write(out)
        print(f"gaps.json: {g['trips']} trips, {g['unobserved']} unobserved; {g['model_status']}")
    else:
        print("no quality files yet")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
