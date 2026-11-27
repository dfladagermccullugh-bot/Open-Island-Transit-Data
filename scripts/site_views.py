"""Stop and trip views for the site, built when the site is built.

    python scripts/site_views.py <data branch> <site data folder> [--releases <dir>]

Reads the stop-event files of the last 28 service days (Release files,
listed with their SHA-256 in ``files.csv``; each is checked before use) and
writes, under ``<site data folder>/views/``:

- ``stops/index.json``: every timepoint stop with its name, routes and the
  number of departures in the window;
- ``stops/<stop id>.json``: that stop's departures, one row each: service
  date, route, trip, scheduled clock time, minutes from the schedule (empty
  when we could not time it), what happened (``observed`` or the reason
  there is no time) and which reports timed it (``report_source``);
- ``trips/<date>.json``: for the last 7 service days, every trip's stops
  with their scheduled and inferred times, for the trip view.

These files are part of the built site only, not the data branch, so the
branch does not grow with them. The counts are checked against
``by_route_stop`` in the on-time day files; a difference stops the build.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import COLLECTION_START, PACIFIC
from pipeline.events.ontime import classify

DAYS = 28
TRIP_DAYS = 7


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def release_files(
    data: Path, releases: Path | None, table: str, days: int, dates: set[str] | None = None
) -> dict[str, bytes]:
    """Service date -> the bytes of that day's ``table`` file, for the last
    ``days`` days that have one (or the ``dates`` given), each checked by its
    SHA-256 in ``files.csv``."""
    with (data / "files.csv").open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["table"] == table]
    wanted = dates if dates is not None else set(sorted(r["service_date"] for r in rows)[-days:])
    out = {}
    for r in rows:
        if r["service_date"] not in wanted:
            continue
        if releases is not None:
            body = (releases / r["release_tag"] / r["name"]).read_bytes()
        else:
            with urllib.request.urlopen(r["url"], timeout=120) as resp:
                body = resp.read()
        if _sha(body) != r["sha256"]:
            raise SystemExit(f"{r['name']}: not the published file (SHA-256 differs)")
        out[r["service_date"]] = body
    return out


def stop_event_files(data: Path, releases: Path | None) -> dict[str, bytes]:
    """Service date -> the bytes of its stop-event file, checked by SHA-256."""
    return release_files(data, releases, "stop-events", DAYS)


def clock(dt: Any) -> str | None:
    return dt.astimezone(PACIFIC).strftime("%H:%M") if dt is not None else None


def build(
    data: Path, files: dict[str, bytes], trip_days: int = TRIP_DAYS
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    import io

    import pyarrow.parquet as pq

    names: dict[str, str] = {}
    for d in sorted(files):
        day = json.loads((data / "ontime" / "days" / f"{d}.json").read_text())
        for route_stops in day.get("by_route_stop", {}).values():
            for sid, b in route_stops.items():
                names[sid] = b.get("name") or names.get(sid, sid)
    stops: dict[str, list[list[Any]]] = defaultdict(list)
    routes: dict[str, set[str]] = defaultdict(set)
    trips: dict[str, dict[str, list[list[Any]]]] = {}
    keep_trips = sorted(files)[-trip_days:]
    for d, body in sorted(files.items()):
        events = pq.read_table(io.BytesIO(body)).to_pylist()
        per_trip: dict[str, list[list[Any]]] = defaultdict(list)
        for e in events:
            if d in keep_trips:
                per_trip[e["trip_id"]].append([
                    e["stop_sequence"], e["stop_id"], e["is_timepoint"],
                    clock(e["scheduled_arrival"]), clock(e["arrival_time"]),
                    clock(e["scheduled_departure"]), clock(e["departure_time"]),
                    e.get("report_source"),
                ])  # fmt: skip
            if not e["is_timepoint"]:
                continue
            cat, dev, scheduled = classify(e)
            if scheduled is None or scheduled < COLLECTION_START:
                continue
            last = e["is_last_stop"]
            status = e["arrival_status"] if last else e["departure_status"]
            stops[e["stop_id"]].append([
                d, e["route_id"], e["trip_id"], clock(scheduled),
                round(dev / 60, 2) if dev is not None else None, status if dev is None else cat,
                e.get("report_source"),
            ])  # fmt: skip
            routes[e["stop_id"]].add(e["route_id"])
        if d in keep_trips:
            trips[d] = dict(per_trip)
    index = {
        "days": sorted(files),
        "columns": [
            "service_date",
            "route_id",
            "trip_id",
            "scheduled",
            "minutes_late",
            "result",
            "report_source",
        ],
        "stops": sorted(
            (
                {
                    "stop_id": s,
                    "name": names.get(s, s),
                    "routes": sorted(routes[s]),
                    "departures": len(rows),
                }
                for s, rows in stops.items()
            ),
            key=lambda x: str(x["name"]),
        ),
    }
    return index, dict(stops), trips


REPLAY_DAYS = 14
# Positions to 1e-5 degree, about 1 meter (3 feet); times to the second.
SCALE = 100_000
CATEGORY = {"on_time": 0, "early": 1, "late": 2}


def _deltas(values: list[int]) -> list[int]:
    return [b - a for a, b in zip([0, *values[:-1]], values, strict=True)]


def replay_day(day: str, vehicles: bytes, events: bytes) -> dict[str, Any]:
    """One day's bus positions and, for each trip, its timed timepoints.

    Each bus is a list of reports with the time (seconds after Pacific
    midnight), latitude and longitude as whole numbers, each written as the
    change from the report before it, so that small numbers repeat and the
    file compresses well. ``trips`` holds runs ``[first report, trip index]``:
    the trip the bus said it was running from that report on. Reports the
    glitch filter removes (``pipeline/glitch.py``) are left out, as they are
    in the on-time figures. Each trip lists its timed timepoints as
    ``[seconds, tenths of a minute late, category]``, where the category
    follows ``pipeline/events/ontime.classify`` (0 on time, 1 early, 2 late).
    """
    import io
    from datetime import date, datetime, time

    import pyarrow.parquet as pq

    from pipeline import glitch

    origin = datetime.combine(date.fromisoformat(day), time(0), PACIFIC)
    rows = [
        v
        for v in pq.read_table(io.BytesIO(vehicles)).to_pylist()
        if v["vehicle_ts"] is not None and v["lat"] is not None and v["lon"] is not None
    ]
    removed = glitch.flag(rows).removed
    seconds = lambda ts: round((ts - origin).total_seconds())  # noqa: E731
    trip_ids: list[str] = []
    trip_index: dict[str, int] = {}
    trip_routes: list[str | None] = []

    def tix(trip: str | None, route: str | None) -> int:
        if trip is None:
            return -1
        if trip not in trip_index:
            trip_index[trip] = len(trip_ids)
            trip_ids.append(trip)
            trip_routes.append(route)
        return trip_index[trip]

    per_bus: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    labels: dict[str, str] = {}
    for v in rows:
        if (v["vehicle_key"], v["vehicle_ts"]) in removed:
            continue
        per_bus[v["vehicle_key"]].setdefault(seconds(v["vehicle_ts"]), v)
        labels[v["vehicle_key"]] = v["vehicle_label"] or v["vehicle_id"] or v["vehicle_key"]
    buses = []
    for key in sorted(per_bus, key=lambda k: (labels[k], k)):
        reports = [per_bus[key][s] for s in sorted(per_bus[key])]
        ts = [seconds(v["vehicle_ts"]) for v in reports]
        runs: list[list[int]] = []
        for i, v in enumerate(reports):
            k = tix(v["trip_id"], v["route_id"])
            if not runs or runs[-1][1] != k:
                runs.append([i, k])
        buses.append({
            "bus": labels[key],
            "t": _deltas(ts),
            "lat": _deltas([round(v["lat"] * SCALE) for v in reports]),
            "lon": _deltas([round(v["lon"] * SCALE) for v in reports]),
            "trips": runs,
        })  # fmt: skip
    timed: dict[int, list[list[int]]] = defaultdict(list)
    for e in pq.read_table(io.BytesIO(events)).to_pylist():
        if not e["is_timepoint"] or e["trip_id"] not in trip_index:
            continue
        cat, dev, scheduled = classify(e)
        if dev is None or scheduled is None:
            continue
        when = seconds(scheduled) + round(dev)
        timed[trip_index[e["trip_id"]]].append([when, round(dev / 6), CATEGORY[cat]])
    return {
        "service_date": day,
        "scale": SCALE,
        "trips": [
            {"trip_id": trip, "route_id": trip_routes[i], "timed": sorted(timed.get(i, []))}
            for i, trip in enumerate(trip_ids)
        ],
        "buses": buses,
    }


def replay_views(data: Path, releases: Path | None, days: int = REPLAY_DAYS) -> dict[str, Any]:
    """Service date -> its replay, for the last ``days`` days with stop events."""
    events = release_files(data, releases, "stop-events", days)
    vehicles = release_files(data, releases, "vehicles", days, set(events))
    return {d: replay_day(d, vehicles[d], events[d]) for d in sorted(events) if d in vehicles}


def check(data: Path, files: dict[str, bytes], stops: dict[str, list[list[Any]]]) -> list[str]:
    """Every stop's departures per day equal ``by_route_stop`` (scheduled)."""
    want: dict[tuple[str, str], int] = defaultdict(int)
    for d in files:
        day = json.loads((data / "ontime" / "days" / f"{d}.json").read_text())
        for ss in day.get("by_route_stop", {}).values():
            for sid, b in ss.items():
                want[(d, sid)] += b["scheduled"]
    got: dict[tuple[str, str], int] = defaultdict(int)
    for sid, rows in stops.items():
        for r in rows:
            got[(r[0], sid)] += 1
    keys = sorted(want.keys() | got.keys())
    return [f"{d} stop {s}: {got.get((d, s), 0)} departures, day file {want.get((d, s), 0)}"
            for d, s in keys if got.get((d, s), 0) != want.get((d, s), 0)]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the stop and trip views.")
    ap.add_argument("data", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--releases", type=Path, help="local Release files, <tag>/<name>")
    ap.add_argument("--trip-days", type=int, default=TRIP_DAYS, help="days with trip views")
    ap.add_argument("--replay-days", type=int, default=REPLAY_DAYS, help="days with a replay")
    args = ap.parse_args(argv)
    files = stop_event_files(args.data, args.releases)
    if not files:
        print("no stop events: no views")
        return 0
    index, stops, trips = build(args.data, files, args.trip_days)
    problems = check(args.data, files, stops)
    for p in problems:
        print(f"PROBLEM {p}", file=sys.stderr)
    if problems:
        return 1
    views = args.out / "views"
    (views / "stops").mkdir(parents=True, exist_ok=True)
    (views / "trips").mkdir(parents=True, exist_ok=True)
    (views / "stops" / "index.json").write_text(json.dumps(index) + "\n")
    for sid, rows in stops.items():
        (views / "stops" / f"{sid}.json").write_text(
            json.dumps({"stop_id": sid, "rows": rows}) + "\n"
        )
    for d, per_trip in trips.items():
        (views / "trips" / f"{d}.json").write_text(json.dumps(
            {"service_date": d, "columns": ["stop_sequence", "stop_id", "is_timepoint",
             "scheduled_arrival", "arrival", "scheduled_departure", "departure", "report_source"],
             "trips": per_trip}) + "\n")  # fmt: skip
    replays = replay_views(args.data, args.releases, args.replay_days) if args.replay_days else {}
    (views / "replay").mkdir(parents=True, exist_ok=True)
    for d, day_view in replays.items():
        (views / "replay" / f"{d}.json").write_text(
            json.dumps(day_view, separators=(",", ":")) + "\n"
        )
    (views / "replay" / "index.json").write_text(json.dumps({"days": sorted(replays)}) + "\n")
    size = sum(p.stat().st_size for p in views.rglob("*.json"))
    days = f"{len(trips)} trip days, {len(replays)} replays"
    print(f"views: {len(stops)} stops, {days}, {size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
