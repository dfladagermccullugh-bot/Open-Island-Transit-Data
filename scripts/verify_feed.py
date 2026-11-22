"""Step 1 feed check: is Island Transit's GTFS-Realtime feed usable?

Run during service hours (roughly 6 AM to 7 PM Pacific):

    python scripts/verify_feed.py --out docs/feed-check-daytime.md \
        --save-fixtures tests/fixtures

It polls the three feeds for a few minutes, decodes them, and reports:
  1. entity counts and HTTP results
  2. which fields are populated
  3. how fresh the data is and how often it changes
  4. whether tripUpdates carry real predictions or echo the schedule
  5. whether real-time trip, route and stop IDs exist in the static GTFS

Exit code 0 = usable (vehicles with trip_id, position and timestamp were seen),
2 = feed empty or missing key fields, 1 = could not complete the check.
"""

from __future__ import annotations

import argparse
import csv
import io
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from google.protobuf.message import DecodeError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.gtfs.zipread import read_zip
from pipeline.parse.realtime import ParsedFeed, field_population, parse_feed

HOST = "https://passio3.com"
AGENCY = "islandtransit"
BASE = f"{HOST}/{AGENCY}/passioTransit/gtfs"
FEEDS = ("vehiclePositions", "tripUpdates", "serviceAlerts")
USER_AGENT = (
    "OpenIslandTransitData/0.1 "
    "(+https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data)"
)
PACIFIC = ZoneInfo("America/Los_Angeles")


def get(url: str, timeout: float = 20) -> tuple[requests.Response | None, str | None]:
    try:
        return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout), None
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__}: {exc}"


def table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in columns) + " |")
    return "\n".join(out) + "\n"


def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def load_static(base: str) -> tuple[dict[str, list[dict]], list[str]]:
    """Download the Passio static GTFS; return its tables and any defects."""
    resp, err = get(f"{base}/google_transit.zip", timeout=60)
    if resp is None:
        return {}, [err or "no response"]
    if resp.status_code != 200:
        return {}, [err or f"HTTP {resp.status_code}"]
    z = read_zip(resp.content)
    tables = {
        name: list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        for name, body in z.files.items()
        if name.endswith(".txt")
    }
    defects = list(z.problems)
    if z.missing_required:
        defects.append("missing: " + ", ".join(z.missing_required))
    return tables, defects


def scheduled_epoch(service_date: datetime, hhmmss: str) -> int:
    """GTFS times count from 'noon minus 12 h' of the service day."""
    h, m, s = (int(x) for x in hhmmss.strip().split(":"))
    noon = service_date.replace(hour=12, minute=0, second=0, microsecond=0)
    return int((noon - timedelta(hours=12) + timedelta(hours=h, minutes=m, seconds=s)).timestamp())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--polls", type=int, default=12, help="number of polls (default 12)")
    ap.add_argument("--interval", type=float, default=20, help="seconds between polls")
    ap.add_argument(
        "--agency",
        default=AGENCY,
        help="Passio agency slug (default islandtransit; others are for testing this script)",
    )
    ap.add_argument("--out", type=Path, help="write the Markdown report here")
    ap.add_argument("--save-fixtures", type=Path, help="save one raw sample per feed here")
    args = ap.parse_args()

    base = f"{HOST}/{args.agency}/passioTransit/gtfs"
    started = datetime.now(PACIFIC)
    lines: list[str] = []
    say = lines.append

    # ------------------------------------------------------------------ poll
    fetch_rows: list[dict] = []
    last: dict[str, ParsedFeed] = {}
    last_raw: dict[str, bytes] = {}
    vehicle_ts_seen: dict[str, list[int]] = defaultdict(list)
    position_age: list[float] = []
    header_age: list[float] = []
    body_changed: dict[str, int] = defaultdict(int)
    prev_entities: dict[str, bytes] = {}

    for i in range(args.polls):
        tick = time.time()
        for feed in FEEDS:
            if feed == "serviceAlerts" and i not in (0, args.polls - 1):
                continue
            t0 = time.time()
            resp, err = get(f"{base}/realtime/{feed}")
            now = time.time()
            row = {
                "poll": i + 1,
                "feed": feed,
                "status": resp.status_code if resp is not None else "-",
                "bytes": len(resp.content) if resp is not None else "-",
                "ms": round(1000 * (now - t0)),
                "entities": "-",
                "error": err or "",
            }
            if resp is not None and resp.status_code == 200:
                try:
                    parsed = parse_feed(resp.content)
                except DecodeError as exc:
                    row["error"] = f"decode: {exc}"
                else:
                    row["entities"] = parsed.entity_count
                    last[feed] = parsed
                    last_raw[feed] = resp.content
                    if parsed.header_ts:
                        header_age.append(now - parsed.header_ts)
                    # Compare everything after the header to spot real change.
                    entities = repr(
                        (parsed.vehicles, parsed.trip_update_stops, parsed.alerts)
                    ).encode()
                    if feed in prev_entities and prev_entities[feed] != entities:
                        body_changed[feed] += 1
                    prev_entities[feed] = entities
                    if feed == "vehiclePositions":
                        for v in parsed.vehicles:
                            key = v["vehicle_id"] or v["entity_id"]
                            if v["vehicle_ts"] is not None:
                                position_age.append(now - v["vehicle_ts"])
                                if v["vehicle_ts"] not in vehicle_ts_seen[key]:
                                    vehicle_ts_seen[key].append(v["vehicle_ts"])
            fetch_rows.append(row)
        if i < args.polls - 1:
            time.sleep(max(0.0, args.interval - (time.time() - tick)))

    vp = last.get("vehiclePositions")
    tu = last.get("tripUpdates")
    al = last.get("serviceAlerts")
    vehicles = vp.vehicles if vp else []

    say(f"# Feed check ({args.agency}), {started:%Y-%m-%d %H:%M %Z}")
    say("")
    say(
        f"{args.polls} polls, {args.interval:g} s apart. "
        f"Produced by `scripts/verify_feed.py`; nothing here is edited by hand."
    )
    say("")

    # --------------------------------------------------------------- summary
    ok = [r for r in fetch_rows if r["status"] == 200 and not r["error"]]
    say("## 1. Fetches")
    say("")
    summary = []
    for feed in FEEDS:
        rows = [r for r in fetch_rows if r["feed"] == feed]
        good = [r for r in rows if r in ok]
        ents = [r["entities"] for r in good]
        summary.append(
            {
                "feed": feed,
                "requests": len(rows),
                "HTTP 200 + decoded": len(good),
                "entities (min–max)": f"{min(ents)}–{max(ents)}" if ents else "-",
                "bytes (min–max)": (
                    f"{min(r['bytes'] for r in good)}–{max(r['bytes'] for r in good)}"
                    if good
                    else "-"
                ),
                "median ms": round(statistics.median(r["ms"] for r in rows)) if rows else "-",
                "content changed between polls": (
                    f"{body_changed[feed]} of {max(len(good) - 1, 0)}"
                ),
            }
        )
    say(table(summary, list(summary[0].keys())))
    errors = [r for r in fetch_rows if r["error"]]
    if errors:
        say("Errors:")
        say("")
        say(table(errors, ["poll", "feed", "status", "error"]))

    # ------------------------------------------------------------ freshness
    say("## 2. Freshness and update rate")
    say("")
    if header_age:
        say(
            f"- Header timestamp age at receipt: median {statistics.median(header_age):.1f} s, "
            f"max {max(header_age):.1f} s."
        )
    if position_age:
        q = statistics.quantiles(position_age, n=20) if len(position_age) > 1 else position_age
        say(
            f"- Vehicle timestamp age at receipt: median {statistics.median(position_age):.0f} s, "
            f"95th percentile {q[-1]:.0f} s, max {max(position_age):.0f} s."
        )
        gaps = [
            b - a
            for seen in vehicle_ts_seen.values()
            for a, b in zip(sorted(seen), sorted(seen)[1:], strict=False)
        ]
        distinct = [len(s) for s in vehicle_ts_seen.values()]
        say(
            f"- Vehicles seen: {len(vehicle_ts_seen)}. Distinct timestamps per vehicle over "
            f"{args.polls} polls: median {statistics.median(distinct):.0f}, "
            f"min {min(distinct)}, max {max(distinct)}."
        )
        if gaps:
            say(
                f"- Gap between consecutive distinct vehicle timestamps: "
                f"median {statistics.median(gaps):.0f} s, max {max(gaps)} s."
            )
    else:
        say("- No vehicle timestamps seen.")
    say("")

    # --------------------------------------------------------------- fields
    say("## 3. Fields populated (last successful poll)")
    say("")
    for title, rows in (
        ("Vehicle positions", vehicles),
        ("Trip updates (per stop)", tu.trip_update_stops if tu else []),
        ("Alerts", al.alerts if al else []),
    ):
        say(f"**{title}**: {len(rows)} rows")
        say("")
        if rows:
            pop = field_population(rows)
            say(
                table(
                    [{"field": k, "populated": pct(v)} for k, v in pop.items()],
                    ["field", "populated"],
                )
            )
        else:
            say("_none_")
            say("")
    occ = sorted({v["occupancy_status"] for v in vehicles if v["occupancy_status"]})
    if occ:
        say(f"Occupancy values seen: {', '.join(occ)}")
        say("")

    # --------------------------------------------------------- static match
    say("## 4. Match against static GTFS (Passio)")
    say("")
    static, defects = load_static(base)
    if defects:
        say("Static zip defects: " + "; ".join(defects))
        say("")
    match_rows = []
    if static:
        ids = {
            "trip_id": {r["trip_id"] for r in static.get("trips.txt", [])},
            "route_id": {r["route_id"] for r in static.get("routes.txt", [])},
            "stop_id": {r["stop_id"] for r in static.get("stops.txt", [])},
        }
        sources = (
            ("vehiclePositions", vehicles),
            ("tripUpdates", tu.trip_update_stops if tu else []),
        )
        for name, rows in sources:
            for key, known in ids.items():
                values = {r[key] for r in rows if r.get(key) is not None}
                if not values:
                    continue
                hit = values & known
                match_rows.append(
                    {
                        "feed": name,
                        "id": key,
                        "distinct in feed": len(values),
                        "found in static": len(hit),
                        "match": pct(len(hit) / len(values)),
                        "examples not found": ", ".join(sorted(values - known)[:5]),
                    }
                )
    say(table(match_rows, list(match_rows[0].keys())) if match_rows else "_no IDs to compare_\n")

    # ------------------------------------------------- predictions vs schedule
    say("## 5. Do tripUpdates carry real predictions?")
    say("")
    stops = tu.trip_update_stops if tu else []
    if not stops:
        say("No trip updates in the last poll.")
    else:
        with_delay = sum(
            1 for s in stops if s["arrival_delay"] is not None or s["departure_delay"] is not None
        )
        with_time = sum(1 for s in stops if s["arrival_time"] or s["departure_time"])
        say(f"- Stop updates: {len(stops)}; with `delay`: {with_delay}; with `time`: {with_time}.")
        sched = {
            (r["trip_id"], r["stop_sequence"]): r["arrival_time"]
            for r in static.get("stop_times.txt", [])
        }
        deviations = []
        for s in stops:
            hhmmss = sched.get((s["trip_id"], str(s["stop_sequence"])))
            if hhmmss and s["arrival_time"]:
                deviations.append(s["arrival_time"] - scheduled_epoch(started, hhmmss))
        if deviations:
            zero = sum(1 for d in deviations if d == 0)
            whole_min = sum(1 for d in deviations if d % 60 == 0)
            q = statistics.quantiles(deviations, n=10) if len(deviations) > 1 else deviations
            say(
                f"- Predicted minus scheduled arrival, {len(deviations)} stop updates: "
                f"median {statistics.median(deviations):.0f} s, "
                f"10th–90th percentile {q[0]:.0f} to {q[-1]:.0f} s."
            )
            say(
                f"- Exactly equal to schedule: {zero} ({pct(zero / len(deviations))}); "
                f"whole-minute differences: {whole_min} ({pct(whole_min / len(deviations))})."
            )
            med = abs(statistics.median(deviations))
            if zero / len(deviations) > 0.9:
                verdict = "echo the schedule"
            elif med > 3600:
                verdict = (
                    "are more than an hour from the schedule, so they are not credible "
                    "in this sample (parked or mislabelled vehicles?)"
                )
            else:
                verdict = "differ from the schedule by plausible amounts, so they look real"
            say(f"- Reading: predictions {verdict}.")
        else:
            say("- Could not line predictions up with scheduled times.")
    say("")

    # --------------------------------------------------------------- verdict
    usable = [
        v
        for v in vehicles
        if v["trip_id"] and v["lat"] is not None and v["lon"] is not None and v["vehicle_ts"]
    ]
    say("## Verdict")
    say("")
    if usable:
        say(
            f"**Usable.** {len(usable)} of {len(vehicles)} vehicles in the last poll carried "
            f"a trip ID, a position and a timestamp."
        )
        code = 0
    elif vehicles:
        say(
            f"**Not usable as planned.** {len(vehicles)} vehicles reported, but none carried "
            f"trip ID + position + timestamp together."
        )
        code = 2
    else:
        hour = started.hour + started.minute / 60
        note = "" if 6 <= hour < 19 else " This run was outside 6 AM to 7 PM Pacific."
        say(f"**No vehicles in the feed.**{note}")
        code = 2
    say("")

    report = "\n".join(lines)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report + "\n")
    if args.save_fixtures and vehicles:
        args.save_fixtures.mkdir(parents=True, exist_ok=True)
        stamp = f"{started:%Y%m%dT%H%M}"
        for feed, raw in last_raw.items():
            (args.save_fixtures / f"{args.agency}_{feed}_{stamp}.pb").write_bytes(raw)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
