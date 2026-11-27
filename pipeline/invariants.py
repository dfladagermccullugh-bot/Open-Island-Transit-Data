"""The publishing gate: rules every published figure must obey.

    python -m pipeline.invariants --data data-branch [--releases <dir>] [--work <dir>]
    python -m pipeline.invariants --data data-branch --light

The nightly job runs this after the last stage and redeploys the site only
when it passes; the site job runs ``--light`` before it copies the data in.
A failed rule prints ``PROBLEM`` and the command exits 1. Something worth
knowing that is not wrong (a backlog of days still being rebuilt after a
version change) prints ``NOTE`` and does not fail.

Each rule has a number, used in the messages and in ``docs/methods.md``:

- I1: the pooled figures in ``ontime/recent.json`` equal the sums of the day
  files in their window, by route and by stop, and the pooled medians and
  quartiles equal those of the pooled deviations.
- I2: in each on-time day file, the day's totals equal the sum over routes,
  over hours, and over route and stop.
- I3: in every block of counts, ``on_time + early + late = observed`` and
  ``observed <= scheduled``; every share equals its counts divided and
  rounded as published; a stop keeps one deviation for each observed event.
  The quality coverage counts obey the same rules.
- I4: every row of ``ontime/daily.csv`` and ``quality/daily.csv`` is the
  day file it was written from, cell for cell.
- I5: the window of ``recent.json`` follows its rules: the last 28 calendar
  days up to the latest day, measured against one printed timetable, with
  days without service (holidays) left out and listed.
- I6: every day was made by the current version of each stage (read from
  ``pipeline/versions.lock.json``). Older days while the newest day is
  current are a backlog the nightly job works off seven days at a time: a
  note. A newest day on an old version is a problem.
- I7: every Pacific day from the first archived day to the last has raw
  files, parsed tables, a schedule pin, stop events, an on-time day file and
  a quality day file, and a quality day file of a service day has its
  stop-event metrics.
- I8: the period files, once they exist, reconcile with the day files.
- I9: every scheduled stop of every tracked trip has one stop event, in stop
  order, with statuses from the known set.
- I10: one bus cannot be in two places at once: on each vehicle, a trip's
  last timed event is no later than the next trip's first plus 30 seconds,
  and no timepoint deviation is beyond two hours.
- I11: the on-time counts of the last days, recounted from their stop-event
  files (checked by SHA-256) with ``ontime.classify``, equal the day files.
- I12: the data branch agrees with Releases (``pipeline/check.py``).

I1 to I8 read only the data branch and need no third-party library; they are
the ``--light`` set. I9 to I11 read the stop-event files of the last
``--days`` days (3 by default, 0 for all), and I12 lists the Releases.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pipeline.columns import ONTIME_DAILY, QUALITY_HEADLINE
from pipeline.common import DayFiles, csv_cell, load_json
from pipeline.config import COLLECTION_START, REPO
from pipeline.events.routes import COUNTS, WINDOW_DAYS, window_of
from pipeline.stats import quantile, share

LOCK = Path(__file__).with_name("versions.lock.json")
TOLERANCE_S = 30.0  # I10: a report may time one trip's end and the next one's start
MAX_DEVIATION_S = 7200.0  # I10
STATUSES = {
    "observed",
    "no_reports",
    "skipped",
    "not_reached",
    "past_when_first_seen",
    "gap",
    "not_applicable",
    "no_shape",
    "unplaced",
}


@dataclass
class Result:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)

    def problem(self, rule: str, text: str) -> None:
        self.problems.append(f"{rule} {text}")

    def note(self, rule: str, text: str) -> None:
        self.notes.append(f"{rule} {text}")


def current_versions(lock: Path = LOCK) -> dict[str, str]:
    """Stage name -> the version the code carries now, as the lock records it
    (``scripts/check_versions.py`` keeps the lock equal to the code)."""
    stages = load_json(lock, {"stages": {}})["stages"]
    return {name: s["version"] for name, s in stages.items()}


# --- I1 to I5: the on-time and quality files -----------------------------------


def _counts(block: dict[str, Any]) -> dict[str, int]:
    return {k: block.get(k, 0) for k in COUNTS}


def _add(into: Counter, block: dict[str, Any]) -> None:
    for k in COUNTS:
        into[k] += block.get(k, 0)


def check_block(res: Result, where: str, b: dict[str, Any], *, extra_shares: bool = False) -> None:
    """I3 for one block of on-time counts."""
    sched, obs = b["scheduled"], b["observed"]
    if b["on_time"] + b["early"] + b["late"] != obs:
        res.problem("I3", f"{where}: on_time + early + late != observed ({obs})")
    if obs > sched:
        res.problem("I3", f"{where}: observed {obs} > scheduled {sched}")
    shares = [
        ("observed_share", obs, sched),
        ("on_time_share_of_observed", b["on_time"], obs),
    ]
    if extra_shares:
        shares += [
            ("early_share_of_observed", b["early"], obs),
            ("late_share_of_observed", b["late"], obs),
        ]
    for name, num, den in shares:
        if b.get(name) != share(num, den, 4):
            res.problem("I3", f"{where}: {name} {b.get(name)} != {num}/{den}")
    if "deviations_s" in b and len(b["deviations_s"]) != obs:
        res.problem("I3", f"{where}: {len(b['deviations_s'])} deviations for {obs} observed")


def check_ontime_day(res: Result, d: dict[str, Any]) -> None:
    """I2 and I3 for one on-time day file."""
    day = d["service_date"]
    check_block(res, f"ontime {day}", d)
    totals = _counts(d)
    for name, blocks in (("by_route", d["by_route"]), ("by_hour", d["by_hour"])):
        summed: Counter = Counter()
        for key, b in blocks.items():
            check_block(res, f"ontime {day} {name} {key}", b)
            _add(summed, b)
        if dict(summed) != totals and (blocks or any(totals.values())):
            res.problem("I2", f"ontime {day}: sum of {name} {dict(summed)} != totals {totals}")
    if "by_route_stop" in d:
        summed = Counter()
        for route, stops in d["by_route_stop"].items():
            per_route: Counter = Counter()
            for stop, b in stops.items():
                check_block(res, f"ontime {day} stop {route}/{stop}", b)
                _add(per_route, b)
            _add(summed, per_route)
            if dict(per_route) != _counts(d["by_route"].get(route, {})):
                res.problem("I2", f"ontime {day}: stops of route {route} do not sum to the route")
        if dict(summed) != totals and (d["by_route_stop"] or any(totals.values())):
            res.problem("I2", f"ontime {day}: sum of by_route_stop != totals")


def _quartiles_match(res: Result, where: str, b: dict[str, Any], devs: list[int]) -> None:
    for name, q in (
        ("deviation_s_median", 0.5),
        ("deviation_s_p25", 0.25),
        ("deviation_s_p75", 0.75),
    ):
        x = quantile(devs, q)
        want = round(x, 1) if x is not None else None
        if b.get(name) != want:
            res.problem("I1", f"{where}: {name} {b.get(name)} != {want} from the pooled deviations")
    if b.get("deviations_counted") != len(devs):
        res.problem(
            "I1", f"{where}: deviations_counted {b.get('deviations_counted')} != {len(devs)}"
        )


def check_recent(res: Result, recent: dict[str, Any], days: list[dict[str, Any]]) -> None:
    """I1, I3 and I5 for ``ontime/recent.json``."""
    w = window_of(days, recent["window_days"])
    if recent["window_days"] != WINDOW_DAYS:
        res.problem("I5", f"recent.json: window_days {recent['window_days']} != {WINDOW_DAYS}")
    want = {
        "first_service_date": w.days[0]["service_date"] if w.days else None,
        "last_service_date": w.days[-1]["service_date"] if w.days else None,
        "days": len(w.days),
        "days_without_service": w.without_service,
        "timetable_reference": w.timetable,
    }
    for k, v in want.items():
        if recent.get(k) != v:
            res.problem("I5", f"recent.json: {k} {recent.get(k)!r}, the window rules give {v!r}")
    if any(not d.get("service_expected", True) for d in w.days):
        res.problem("I5", "recent.json: a day without service is pooled")
    if days and recent.get("last_service_date") not in {
        d["service_date"] for d in days if d.get("service_expected", True)
    }:
        res.problem("I5", "recent.json: last_service_date is not a service day")

    total: Counter = Counter()
    devs: list[int] = []
    routes: dict[str, Counter] = defaultdict(Counter)
    route_devs: dict[str, list[int]] = defaultdict(list)
    stops: dict[tuple[str, str], Counter] = defaultdict(Counter)
    stop_devs: dict[tuple[str, str], list[int]] = defaultdict(list)
    for d in w.days:
        _add(total, d)
        for r, b in d["by_route"].items():
            _add(routes[r], b)
        for r, ss in d.get("by_route_stop", {}).items():
            for s, b in ss.items():
                _add(stops[(r, s)], b)
                stop_devs[(r, s)] += b["deviations_s"]
                route_devs[r] += b["deviations_s"]
                devs += b["deviations_s"]
    overall = recent["overall"]
    if _counts(overall) != dict(total):
        res.problem(
            "I1", f"recent.json overall {_counts(overall)} != sum of its days {dict(total)}"
        )
    check_block(res, "recent.json overall", overall, extra_shares=True)
    _quartiles_match(res, "recent.json overall", overall, devs)
    if set(recent["routes"]) != set(routes):
        res.problem("I1", "recent.json: its routes are not the routes of its days")
    for r, rb in recent["routes"].items():
        where = f"recent.json route {r}"
        if _counts(rb) != dict(routes.get(r, Counter())):
            res.problem("I1", f"{where}: counts are not the sum of its days")
        check_block(res, where, rb, extra_shares=True)
        _quartiles_match(res, where, rb, route_devs[r])
        if len(rb.get("daily", [])) != sum(r in d["by_route"] for d in w.days):
            res.problem("I1", f"{where}: one daily entry per day expected")
        for sb in rb.get("stops", []):
            key = (r, sb["stop_id"])
            if _counts(sb) != dict(stops.get(key, Counter())):
                res.problem(
                    "I1", f"{where} stop {sb['stop_id']}: counts are not the sum of its days"
                )
            check_block(res, f"{where} stop {sb['stop_id']}", sb, extra_shares=True)
            _quartiles_match(res, f"{where} stop {sb['stop_id']}", sb, stop_devs[key])


def check_quality_day(res: Result, q: dict[str, Any]) -> None:
    """I3 for the coverage counts of one quality day file."""
    day, cov = q["service_date"], q.get("coverage") or {}
    if not cov:
        return
    sched, obs = cov["scheduled_trips"], cov["observed_scheduled_trips"]
    if obs > sched:
        res.problem("I3", f"quality {day}: observed trips {obs} > scheduled {sched}")
    if cov["trip_coverage"] != share(obs, sched):
        res.problem("I3", f"quality {day}: trip_coverage != {obs}/{sched}")
    by_route = cov.get("by_route", {})
    if by_route:
        if sum(b["scheduled"] for b in by_route.values()) != sched:
            res.problem("I3", f"quality {day}: routes' scheduled trips do not sum to the day's")
        if sum(b["observed"] for b in by_route.values()) != obs:
            res.problem("I3", f"quality {day}: routes' observed trips do not sum to the day's")
        for r, b in by_route.items():
            if b["coverage"] != share(b["observed"], b["scheduled"]):
                res.problem("I3", f"quality {day} route {r}: coverage != observed/scheduled")
    events = q.get("events") or {}
    for kind, b in (events.get("stop_coverage") or {}).items():
        if (
            isinstance(b, dict)
            and "scheduled" in b
            and (
                b["observed"] > b["scheduled"]
                or b["coverage"] != share(b["observed"], b["scheduled"])
            )
        ):
            res.problem("I3", f"quality {day}: stop_coverage {kind} does not follow its counts")


def _cell(value: Any) -> str:
    return str(csv_cell(value))


def _get(obj: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _path(path: tuple[str, ...]) -> Callable[[dict[str, Any]], Any]:
    return lambda d: _get(d, path)


def check_table(
    res: Result,
    path: Path,
    days: list[dict[str, Any]],
    columns: list[tuple[str, Callable[[dict[str, Any]], Any]]],
) -> None:
    """I4: the table holds one row per day file, each cell as written from it."""
    if not path.exists():
        if days:
            res.problem("I4", f"{path.parent.name}/{path.name} is missing")
        return
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    header = [name for name, _ in columns]
    if not rows or rows[0] != header:
        res.problem("I4", f"{path.parent.name}/{path.name}: header differs from the code's")
        return
    if len(rows) - 1 != len(days):
        res.problem("I4", f"{path.parent.name}/{path.name}: {len(rows) - 1} rows, {len(days)} days")
    for row, d in zip(rows[1:], days, strict=False):
        want = [_cell(get(d)) for _, get in columns]
        if row != want:
            bad = [h for h, a, b in zip(header, row, want, strict=True) if a != b]
            res.problem("I4", f"{path.parent.name}/{path.name} {d.get('service_date')}: {bad}")


# --- I6, I7: versions and completeness ------------------------------------------


def _manifest(data: Path, name: str) -> dict[str, Any]:
    return load_json(data / "manifest" / f"{name}.json", {"days": {}})


def check_versions(
    res: Result,
    data: Path,
    versions: dict[str, str],
    ontime: list[dict[str, Any]],
    quality: list[dict[str, Any]],
) -> None:
    """I6."""
    found: dict[str, dict[str, str | None]] = defaultdict(dict)
    for day, e in _manifest(data, "parsed")["days"].items():
        found["tables"][day] = e.get("tables_version")
    for day, e in _manifest(data, "events")["days"].items():
        for stage in ("events", "ontime", "validate"):
            found[stage][day] = e.get(f"{stage}_version")
    for d in ontime:
        found["ontime"][d["service_date"]] = d.get("ontime_version")
    for q in quality:
        found["quality"][q["service_date"]] = q.get("quality_version")
    for stage, by_day in sorted(found.items()):
        want = versions.get(stage)
        if want is None or not by_day:
            continue
        stale = sorted(day for day, v in by_day.items() if v != want)
        if not stale:
            continue
        newest = max(by_day)
        if newest in stale:
            res.problem("I6", f"{stage}: newest day {newest} is {by_day[newest]}, the code {want}")
        else:
            res.note("I6", f"{stage}: {len(stale)} older days still on an older version "
                     f"(first {stale[0]}); the nightly job rebuilds them")  # fmt: skip


def check_complete(res: Result, data: Path, ontime_dir: Path, quality_dir: Path) -> None:
    """I7."""
    raw = _manifest(data, "raw")["days"]
    if not raw:
        return
    parsed = _manifest(data, "parsed")["days"]
    static = _manifest(data, "static").get("days", {})
    events = _manifest(data, "events")["days"]
    first, last = date.fromisoformat(min(raw)), date.fromisoformat(max(raw))
    day = first
    while day <= last:
        d = day.isoformat()
        missing = []
        if not (raw.get(d) or {}).get("release_tag"):
            missing.append("raw files")
        if not (parsed.get(d) or {}).get("release_tag"):
            missing.append("parsed tables")
        if not ((static.get(d) or {}).get("passio") or {}).get("version"):
            missing.append("schedule pin")
        if not (events.get(d) or {}).get("release_tag"):
            missing.append("stop events")
        if not (ontime_dir / "days" / f"{d}.json").exists():
            missing.append("on-time day file")
        q = load_json(quality_dir / "days" / f"{d}.json")
        if q is None:
            missing.append("quality day file")
        elif (q.get("schedule") or {}).get("service_expected", True) and q.get("events") is None:
            missing.append("stop-event metrics in the quality day file")
        if missing:
            res.problem("I7", f"{d}: no {', '.join(missing)}")
        day += timedelta(days=1)


def check_periods(res: Result, data: Path) -> None:
    """I8: nothing to check until the period files exist (docs/plan-2026-11-27.md)."""
    for name in ("ontime/periods.json", "quality/periods.json", "ontime/headline.json"):
        if (data / name).exists():
            res.note("I8", f"{name} exists but its rules are not written yet")


def light(res: Result, data: Path, versions: dict[str, str]) -> None:
    """I1 to I8."""
    ontime_dir, quality_dir = data / "ontime", data / "quality"
    ontime = DayFiles(ontime_dir / "days").all()
    quality = DayFiles(quality_dir / "days").all()
    for d in ontime:
        check_ontime_day(res, d)
    recent = load_json(ontime_dir / "recent.json")
    if recent is not None and ontime:
        check_recent(res, recent, ontime)
    elif ontime and any(d.get("service_expected", True) for d in ontime):
        res.problem("I1", "ontime/recent.json is missing")
    for q in quality:
        check_quality_day(res, q)
    check_table(
        res,
        ontime_dir / "daily.csv",
        ontime,
        [(k, _path((k,))) for k in ONTIME_DAILY],
    )
    check_table(
        res,
        quality_dir / "daily.csv",
        quality,
        [(name, _path(p)) for name, p in QUALITY_HEADLINE],
    )
    check_versions(res, data, versions, ontime, quality)
    check_complete(res, data, ontime_dir, quality_dir)
    check_periods(res, data)
    res.ran += ["I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8"]


# --- I9 to I11: the stop-event files of the last days ---------------------------


Fetch = Callable[[str, str, str], Path]


def local_fetch(releases: Path) -> Fetch:
    """Release files from a folder laid out as ``<tag>/<name>``, checked by SHA-256."""
    from pipeline.common import sha256_file

    def get(tag: str, name: str, sha256: str) -> Path:
        path = releases / tag / name
        if not path.exists() or sha256_file(path) != sha256:
            raise FileNotFoundError(f"{tag}/{name} missing or not the published file")
        return path

    return get


def github_fetch(repo: str, work: Path) -> Fetch:
    from pipeline import release

    def get(tag: str, name: str, sha256: str) -> Path:
        return release.fetch(tag, repo, name, work, sha256)

    return get


def stop_times_by_trip(fetch: Fetch, data: Path, day: str) -> dict[str, list[int]]:
    from pipeline.gtfs.tables import rows
    from pipeline.gtfs.zipread import load_zip

    static = _manifest(data, "static")
    ex = static["versions"][static["days"][day]["passio"]["version"]]["example"]
    contents = load_zip(fetch(ex["release_tag"], ex["file"], ex["sha256"]), ex["sha256"])
    out: dict[str, list[int]] = defaultdict(list)
    for r in rows(contents, "stop_times.txt"):
        out[r["trip_id"]].append(int(r["stop_sequence"]))
    return {t: sorted(s) for t, s in out.items()}


def check_events(
    res: Result, day: str, events: list[dict[str, Any]], tracks: list[dict[str, Any]],
    stop_times: dict[str, list[int]],
) -> None:  # fmt: skip
    """I9 and I10 for one day."""
    by_trip: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_trip[e["trip_id"]].append(e)
        for kind in ("arrival", "departure"):
            if e[f"{kind}_status"] not in STATUSES:
                res.problem(
                    "I9", f"{day} trip {e['trip_id']}: unknown status {e[f'{kind}_status']}"
                )
            if (e[f"{kind}_time"] is not None) != (e[f"{kind}_status"] == "observed"):
                res.problem("I9", f"{day} trip {e['trip_id']}: a time without 'observed' or back")
    for t in tracks:
        trip = t["trip_id"]
        rows = by_trip.pop(trip, [])
        if t["problem"]:
            if rows:
                res.problem("I9", f"{day} trip {trip}: has a problem and stop events")
            continue
        seqs = [e["stop_sequence"] for e in rows]
        if seqs != stop_times.get(trip):
            res.problem("I9", f"{day} trip {trip}: stop events {len(seqs)}, schedule "
                        f"{len(stop_times.get(trip, []))}, or out of order")  # fmt: skip
            continue
        if not (rows[0]["is_first_stop"] and rows[-1]["is_last_stop"]):
            res.problem("I9", f"{day} trip {trip}: first and last stop not marked")
        if rows[0]["arrival_status"] != "not_applicable" or (
            rows[-1]["departure_status"] != "not_applicable"
        ):
            res.problem("I9", f"{day} trip {trip}: a first-stop arrival or last-stop departure")
    for trip in by_trip:
        res.problem("I9", f"{day} trip {trip}: stop events but no trip track")

    # I10. Each trip's span of timed events, on the vehicle it was tracked with.
    spans: dict[str, list[tuple[Any, Any, str]]] = defaultdict(list)
    by_trip = defaultdict(list)
    for e in events:
        by_trip[e["trip_id"]].append(e)
    for trip, rows in by_trip.items():
        times = [e[f"{k}_time"] for e in rows for k in ("arrival", "departure")]
        times = [x for x in times if x is not None]
        vehicle = rows[0]["vehicle_key"]
        if times and vehicle is not None:
            spans[vehicle].append((min(times), max(times), trip))
    for vehicle, trips in spans.items():
        trips.sort()
        for (_, p_end, p), (n_start, _, n) in itertools.pairwise(trips):
            if (p_end - n_start).total_seconds() > TOLERANCE_S:
                over = (p_end - n_start).total_seconds()
                res.problem("I10", f"{day} vehicle {vehicle}: trip {p} still timed "
                            f"{over:.0f} s after trip {n} began")  # fmt: skip
    from pipeline.events.ontime import classify

    for e in events:
        if e["is_timepoint"]:
            _, dev, _ = classify(e)
            if dev is not None and abs(dev) > MAX_DEVIATION_S:
                res.problem("I10", f"{day} trip {e['trip_id']} stop {e['stop_id']}: "
                            f"deviation {dev:.0f} s")  # fmt: skip


def _stops(day: dict[str, Any]) -> dict[tuple[str, str], tuple[dict[str, int], list[int]]]:
    return {
        (r, s): (_counts(b), b["deviations_s"])
        for r, ss in day.get("by_route_stop", {}).items()
        for s, b in ss.items()
    }


def recount(res: Result, day: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """I11: the day file's counts from its own stop events."""
    from pipeline.events.ontime import ONTIME_VERSION, summarize

    if day.get("ontime_version") != ONTIME_VERSION:
        return
    canceled = set((day.get("canceled") or {}).get("trip_ids") or [])
    again = summarize(events, COLLECTION_START, canceled=canceled)
    d = day["service_date"]
    if _counts(again) != _counts(day):
        res.problem("I11", f"{d}: recount {_counts(again)} != day file {_counts(day)}")
    for name in ("by_route", "by_hour"):
        a = {k: _counts(b) for k, b in again[name].items()}
        b = {k: _counts(v) for k, v in day[name].items()}
        if a != b:
            res.problem("I11", f"{d}: recount of {name} differs from the day file")
    if _stops(again) != _stops(day):
        res.problem("I11", f"{d}: recount of by_route_stop differs from the day file")


def heavy(res: Result, data: Path, fetch: Fetch, days: int) -> None:
    """I9 to I11 over the last ``days`` days with stop events (0: all)."""
    import pyarrow.parquet as pq

    manifest = _manifest(data, "events")["days"]
    files = DayFiles(data / "ontime" / "days")
    chosen = sorted(manifest)[-days:] if days else sorted(manifest)
    for d in chosen:
        entry = manifest[d]
        tag = entry.get("release_tag")
        if not tag:
            continue
        shas = {f["name"]: f["sha256"] for f in entry["files"]}
        try:
            tables = {
                kind: pq.read_table(
                    fetch(tag, f"{kind}-{d}.parquet", shas[f"{kind}-{d}.parquet"])
                ).to_pylist()
                for kind in ("stop-events", "trip-tracks")
            }
            stop_times = stop_times_by_trip(fetch, data, d)
        except Exception as exc:  # report it and go on with the other days
            res.problem("I9", f"{d}: stop events not readable: {exc}")
            continue
        check_events(res, d, tables["stop-events"], tables["trip-tracks"], stop_times)
        day = files.read(d)
        if day is None:
            continue  # I7 reports it
        recount(res, day, tables["stop-events"])
    res.ran += ["I9", "I10", "I11"]


def releases_agree(res: Result, data: Path, releases: Path | None, repo: str) -> None:
    """I12."""
    from pipeline import check

    assets: check.Assets
    if releases is not None:
        folder = releases

        def assets(tag: str) -> dict[str, int]:
            return {f.name: f.stat().st_size for f in (folder / tag).glob("*")}

    else:
        assets = check.gh_assets(repo)
    problems, counts = check.check(data, assets)
    for p in problems:
        res.problem("I12", p)
    print(f"I12 checked {counts['days']} days, {counts['files']} release files")
    res.ran.append("I12")


def run(
    data: Path,
    *,
    light_only: bool = False,
    releases: Path | None = None,
    work: Path | None = None,
    repo: str = REPO,
    days: int = 3,
    versions: dict[str, str] | None = None,
) -> Result:
    res = Result()
    light(res, data, current_versions() if versions is None else versions)
    if not light_only:
        fetch = (
            local_fetch(releases) if releases else github_fetch(repo, work or Path("work/check"))
        )
        heavy(res, data, fetch, days)
        releases_agree(res, data, releases, repo)
    return res


def report(res: Result) -> int:
    for p in res.problems:
        print(f"PROBLEM {p}")
    for n in res.notes:
        print(f"NOTE {n}")
    print(f"invariants {', '.join(res.ran)}: {len(res.problems)} problems, {len(res.notes)} notes")
    return 1 if res.problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check the rules every published figure obeys.")
    ap.add_argument("--data", type=Path, default=Path("data-branch"))
    ap.add_argument("--light", action="store_true", help="only I1 to I8 (no third-party library)")
    ap.add_argument("--releases", type=Path, help="local Release files, <tag>/<name>")
    ap.add_argument("--work", type=Path, help="where Release files are downloaded or found")
    ap.add_argument("--days", type=int, default=3, help="days for I9 to I11 (0: all)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    args = ap.parse_args(argv)
    res = run(
        args.data,
        light_only=args.light,
        releases=args.releases,
        work=args.work,
        repo=args.repo,
        days=args.days,
    )
    return report(res)


if __name__ == "__main__":
    sys.exit(main())
