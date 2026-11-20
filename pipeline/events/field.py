"""Field check of inferred stop events (docs/methods.md, section 5, check 1).

Arrivals and departures timed in person at timepoints are compared with the
inferred times for the same trip and stop. The inferred arrival is the moment
the bus comes within 40 m of the stop along its route, the departure the
moment it is more than 40 m past (docs/methods.md, section 3); observers time
the same two moments.

Two commands:

    python -m pipeline.events.field sheet --date 2026-11-23 \
        --stop "Harbor Station" --from 07:00 --to 09:00 > sheet.csv

writes a sheet listing every scheduled bus at the timepoints whose names
contain the given text, within the time window, with trip and stop ids filled
in, which moment to time (``time_this``: departure at a trip's first stop,
arrival at its last, both otherwise) and blank columns for the times.
Filled-in rows go into ``reference/field-observations.csv``.

    python -m pipeline.events.field score --data data-branch

compares those rows with the stop events of each day already processed and
writes ``ontime/field-check.json`` on the data branch. The target is a mean
absolute error of 30 s or less.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from pipeline.config import PACIFIC, REPO, USER_AGENT
from pipeline.gtfs import timepoints as tp
from pipeline.gtfs.schedule import _rows, _seconds, day_origin, scheduled_trips
from pipeline.gtfs.zipread import ZipContents, read_zip

FIELD_VERSION = "field/0.1.0"
OBSERVATIONS = tp.REFERENCE_DIR / "field-observations.csv"
PASSIO_ZIP = "https://passio3.com/islandtransit/passioTransit/gtfs/google_transit.zip"
SHEET_COLUMNS = ("service_date", "stop_id", "stop_name", "route", "trip_id", "scheduled",
                 "time_this", "arrive_40m", "depart_40m", "note")  # fmt: skip
TARGET_MAE_S = 30.0

Row = dict[str, Any]


# --- the sheet ------------------------------------------------------------------


def sheet(
    passio: ZipContents,
    day: date,
    queries: list[str],
    start: time | None = None,
    end: time | None = None,
) -> list[Row]:
    """Every scheduled stop time on ``day`` at a printed-timetable timepoint
    whose stop name contains one of ``queries`` (case-insensitive), with a
    Pacific scheduled time from ``start`` to ``end`` inclusive. ``time_this``
    says which moment to time: at a trip's first stop only the departure
    exists, at its last only the arrival."""
    effective = tp.reference_for(day)
    if effective is None:
        raise ValueError(f"no timetable reference covers {day}")
    pairs = tp.timepoint_pairs(tp.build(passio, effective)["matched"])
    stops = {s["stop_id"]: s["stop_name"] for s in _rows(passio, "stops.txt")}
    wanted = {i for i, name in stops.items() if any(q.lower() in name.lower() for q in queries)}
    routes = {r["route_id"]: r.get("route_short_name") or r.get("route_long_name") or r["route_id"]
              for r in _rows(passio, "routes.txt")}  # fmt: skip
    trips = {t.trip_id: t for t in scheduled_trips(passio, day)}
    origin = day_origin(day)
    stop_times = [st for st in _rows(passio, "stop_times.txt") if st["trip_id"] in trips]
    ends: dict[str, tuple[int, int]] = {}
    for st in stop_times:
        q = int(st["stop_sequence"])
        lo, hi = ends.get(st["trip_id"], (q, q))
        ends[st["trip_id"]] = (min(lo, q), max(hi, q))
    out = []
    for st in stop_times:
        t = trips[st["trip_id"]]
        if st["stop_id"] not in wanted or (t.route_id, st["stop_id"]) not in pairs:
            continue
        q, (first, last) = int(st["stop_sequence"]), ends[t.trip_id]
        role = "departure" if q == first else "arrival" if q == last else "both"
        hms = (st.get("arrival_time") if role == "arrival" else st.get("departure_time")) or (
            st.get("departure_time") or st.get("arrival_time")
        )
        when = (origin + timedelta(seconds=_seconds(hms))).astimezone(PACIFIC)
        if (start and when.time() < start) or (end and when.time() > end):
            continue
        out.append({
            "service_date": day.isoformat(), "stop_id": st["stop_id"],
            "stop_name": stops[st["stop_id"]], "route": routes.get(t.route_id, t.route_id),
            "trip_id": t.trip_id, "scheduled": when.strftime("%H:%M"), "time_this": role,
            "arrive_40m": "", "depart_40m": "", "note": "",
        })  # fmt: skip
    return sorted(out, key=lambda r: (r["stop_name"], r["stop_id"], r["scheduled"], r["trip_id"]))


def write_csv(rows: list[Row], fh) -> None:
    w = csv.DictWriter(fh, fieldnames=SHEET_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


# --- reading observations ---------------------------------------------------------

_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$", re.I)


def parse_local(day: date, text: str) -> datetime | None:
    """A Pacific clock time written on the sheet ("14:03:27", "2:03:27 pm")
    as a UTC instant on ``day``; None if blank. Seconds are required."""
    if not text or not text.strip():
        return None
    m = _TIME.match(text)
    if not m or m.group(3) is None:
        raise ValueError(f"time {text!r} must look like 14:03:27 or 2:03:27 pm")
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if m.group(4):
        h = h % 12 + (12 if m.group(4).lower().startswith("p") else 0)
    return datetime.combine(day, time(h, mi, s), tzinfo=PACIFIC).astimezone(UTC)


def load(path: Path = OBSERVATIONS) -> tuple[list[Row], list[str]]:
    """Rows with at least one time, and a list of rows that could not be read."""
    if not path.exists():
        return [], []
    rows, problems = [], []
    for n, r in enumerate(csv.DictReader(io.StringIO(path.read_text())), start=2):
        if not (r.get("arrive_40m") or "").strip() and not (r.get("depart_40m") or "").strip():
            continue
        try:
            day = date.fromisoformat(r["service_date"].strip())
            rows.append({
                "line": n, "service_date": day.isoformat(), "stop_id": r["stop_id"].strip(),
                "trip_id": r["trip_id"].strip(),
                "arrival": parse_local(day, r.get("arrive_40m", "")),
                "departure": parse_local(day, r.get("depart_40m", "")),
            })  # fmt: skip
        except (KeyError, ValueError) as exc:
            problems.append(f"line {n}: {exc}")
    return rows, problems


# --- scoring ----------------------------------------------------------------------


def _stats(diffs: list[float]) -> dict[str, Any]:
    if not diffs:
        return {"n": 0, "bias_s": None, "median_s": None, "mae_s": None,
                "within_30s_share": None, "within_60s_share": None, "max_abs_s": None}  # fmt: skip
    return {
        "n": len(diffs),
        "bias_s": round(statistics.fmean(diffs), 1),
        "median_s": round(statistics.median(diffs), 1),
        "mae_s": round(statistics.fmean(abs(d) for d in diffs), 1),
        "within_30s_share": round(sum(abs(d) <= 30 for d in diffs) / len(diffs), 4),
        "within_60s_share": round(sum(abs(d) <= 60 for d in diffs) / len(diffs), 4),
        "max_abs_s": round(max(abs(d) for d in diffs), 1),
    }


def score(observations: list[Row], events: dict[str, list[Row]]) -> dict[str, Any]:
    """Compare timed rows with inferred events. ``events`` maps a service
    date to that day's stop-event rows; dates not in it are not scored yet.
    Differences are inferred minus field time, in seconds."""
    index = {(d, e["trip_id"], e["stop_id"]): e for d, rows in events.items() for e in rows}
    diffs: dict[str, list[float]] = {"arrival": [], "departure": []}
    by_stop: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"arrival": [], "departure": []}
    )
    pending = no_event = not_inferred = 0
    pairs = []
    for o in observations:
        if o["service_date"] not in events:
            pending += 1
            continue
        e = index.get((o["service_date"], o["trip_id"], o["stop_id"]))
        if e is None:
            no_event += 1
            continue
        for kind in ("arrival", "departure"):
            if o[kind] is None:
                continue
            inferred = e[f"{kind}_time"]
            if inferred is None:
                not_inferred += 1
                continue
            d = (inferred - o[kind]).total_seconds()
            diffs[kind].append(d)
            by_stop[o["stop_id"]][kind].append(d)
            pairs.append({"line": o["line"], "service_date": o["service_date"],
                          "trip_id": o["trip_id"], "stop_id": o["stop_id"], "event": kind,
                          "field": o[kind].isoformat(), "inferred": inferred.isoformat(),
                          "difference_s": round(d, 1)})  # fmt: skip
    both = diffs["arrival"] + diffs["departure"]
    overall = _stats(both)
    return {
        "field_version": FIELD_VERSION,
        "definition": "inferred minus field-timed, seconds; arrival = bus within 40 m of the "
        "stop along its route, departure = more than 40 m past it",
        "target_mae_s": TARGET_MAE_S,
        "meets_target": None if not both else overall["mae_s"] <= TARGET_MAE_S,
        "overall": overall,
        "arrival": _stats(diffs["arrival"]),
        "departure": _stats(diffs["departure"]),
        "by_stop": {s: {k: _stats(v) for k, v in d.items()} for s, d in sorted(by_stop.items())},
        "timed_rows": len(observations),
        "rows_waiting_for_their_day": pending,
        "rows_with_no_scheduled_event": no_event,
        "timed_moments_not_inferred": not_inferred,
        "days": sorted({o["service_date"] for o in observations if o["service_date"] in events}),
        "pairs": pairs,
    }


# --- commands ---------------------------------------------------------------------


def _passio() -> ZipContents:
    import requests

    r = requests.get(PASSIO_ZIP, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return read_zip(r.content)


def _events_for(data: Path, days: set[str], tables_dir: Path, repo: str) -> dict[str, list[Row]]:
    import pyarrow.parquet as pq

    from pipeline import release

    manifest = data / "manifest" / "events.json"
    entries = json.loads(manifest.read_text())["days"] if manifest.exists() else {}
    out = {}
    for d in sorted(days):
        entry = entries.get(d)
        if not entry or not entry.get("release_tag"):
            continue
        name = f"stop-events-{d}.parquet"
        sha = next(f["sha256"] for f in entry["files"] if f["name"] == name)
        path = release.fetch(entry["release_tag"], repo, name, tables_dir, sha)
        out[d] = pq.read_table(path).to_pylist()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Field check of inferred stop events.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet", help="print a blank field sheet as CSV")
    s.add_argument("--date", type=date.fromisoformat, required=True)
    s.add_argument("--stop", action="append", required=True, help="text in the stop name")
    s.add_argument("--from", dest="start", type=time.fromisoformat, help="Pacific HH:MM")
    s.add_argument("--to", dest="end", type=time.fromisoformat, help="Pacific HH:MM")
    s.add_argument("--zip", type=Path, help="Passio zip (default: download the current one)")
    c = sub.add_parser("score", help="score reference/field-observations.csv")
    c.add_argument("--data", type=Path, default=Path("work"), help="data branch checkout")
    c.add_argument("--observations", type=Path, default=OBSERVATIONS)
    c.add_argument("--tables-dir", type=Path, default=Path("work/tables"))
    c.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    args = ap.parse_args()

    if args.cmd == "sheet":
        passio = read_zip(args.zip.read_bytes()) if args.zip else _passio()
        rows = sheet(passio, args.date, args.stop, args.start, args.end)
        write_csv(rows, sys.stdout)
        print(f"{len(rows)} scheduled stop times", file=sys.stderr)
        return 0

    observations, problems = load(args.observations)
    for p in problems:
        print(f"unreadable: {p}", file=sys.stderr)
    days = {o["service_date"] for o in observations}
    result = score(observations, _events_for(args.data, days, args.tables_dir, args.repo))
    result["unreadable_rows"] = problems
    out = args.data / "ontime" / "field-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1) + "\n")
    o = result["overall"]
    print(f"field check: {o['n']} timed moments scored, MAE {o['mae_s']} s, "
          f"{result['rows_waiting_for_their_day']} rows waiting for their day")  # fmt: skip
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
