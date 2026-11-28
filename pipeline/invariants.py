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
  over hours, and over route and stop; since ``ontime/0.7.0`` the untimed
  departures add up by reason and by position, and the weighting cells and
  each trip's cells add up to the day's totals and to ``by_trip``.
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
- I8: the period files (``ontime/headline.json``, ``ontime/periods.json``,
  ``quality/periods.json``) reconcile with the day files. Since
  ``periods/0.3.0`` the headline's untimed reasons are the sum of its days,
  its worst-case range follows its counts, and its weighted share is
  recomputed from its cells, each the sum of its members in the day files;
  a day's untimed reasons match its quality file when no trip was canceled.
  Since ``periods/0.4.0``: ``ontime/highlights.json`` covers the headline's
  window; each route and time of day it considered is the sum of its days
  (``by_route``, ``by_cell``), the ones it names as running late follow its
  rule (enough departures, interval wholly below the system, lowest first,
  at most three), each ferry terminal is the sum of its stop's days; and the
  headline's ``since`` is the sum of every day file and ``manifest/raw.json``.
- I9: every scheduled stop of every tracked trip has one stop event, in stop
  order, with statuses from the known set; a relay (``events/0.6.0``) names
  a second vehicle, not the first, with reports inside the trip's window.
- I10: one bus cannot be in two places at once: on each vehicle, a trip's
  last timed event is no later than the next trip's first plus 30 seconds,
  counting the part of a trip a relay vehicle ran as that vehicle's, and no
  timepoint deviation is beyond two hours.
- I11: the on-time counts of the last days, recounted from their stop-event
  files (checked by SHA-256) with ``ontime.classify``, equal the day files.
- I12: the data branch agrees with Releases (``pipeline/check.py``).
- I13: the comparisons (``compare/summary.json``, ``compare/0.3.0``) explain
  the one difference between our published figure and the recount under our
  own rule exactly: both cover the headline's service days, and the
  published count of departures on time less the recount is the sum of the
  days' early arrivals at last stops (``last_stop_early_arrivals``); the
  recount's share follows its counts; the recount over a peer's hours names
  only service days of its pool, on its weekdays.

I1 to I8 and I13 read only the data branch and need no third-party library;
they are the ``--light`` set. I9 to I11 read the stop-event files of the last
``--days`` days (3 by default, 0 for all), and I12 lists the Releases.

``--report <file>`` writes what ran as JSON (``checks/gate.json`` on the data
branch, ``GATE_VERSION``): the rules, whether they passed, every problem and
note, the data branch commit that was checked (``--commit``) and when.
``--extra-check NAME=CODE`` adds the result of a check run outside this
module (the Frictionless validation of the tables): any code but 0 is a
problem and fails the gate. The writers publish to ``data-staging`` and move
``data`` to the checked commit only when the gate passed
(``scripts/promote_data.sh``), so ``data`` holds only checked figures.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pipeline.columns import ONTIME_DAILY, QUALITY_HEADLINE
from pipeline.common import DayFiles, csv_cell, load_json, stamp
from pipeline.config import COLLECTION_START, REPO
from pipeline.events.routes import COUNTS, WINDOW_DAYS, window_of
from pipeline.stats import quantile, share

GATE_VERSION = "gate/0.1.0"
LOCK = Path(__file__).with_name("versions.lock.json")
TOLERANCE_S = 30.0  # I10: a report may time one trip's end and the next one's start
RELAY_SOURCES = frozenset({"relay", "terminal_relay"})  # I10: timed by a relay vehicle
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
    check_untimed_day(res, d)
    early_last = d.get("last_stop_early_arrivals")
    last = (d.get("by_position") or {}).get("last") or {}
    if early_last is not None and early_last > last.get("on_time", 0):
        res.problem("I3", f"ontime {day}: more early last-stop arrivals than on time at last stops")


def _vsum(vectors: Iterable[list[int]]) -> list[int]:
    out = [0, 0, 0]
    for v in vectors:
        for i in range(3):
            out[i] += v[i]
    return out


def check_untimed_day(res: Result, d: dict[str, Any]) -> None:
    """I2, since ``ontime/0.7.0``: the untimed departures add up by reason and
    by position, and the weighting cells and their per-trip counts add up to
    the day's totals."""
    day = d["service_date"]
    u = d.get("untimed")
    if u is not None:
        gap = d["scheduled"] - d["observed"]
        if not (u["departures"] == gap == sum(u["by_reason"].values())):
            res.problem("I2", f"ontime {day}: untimed reasons do not add up to the untimed")
        for p, reasons in u["by_position"].items():
            b = d["by_position"][p]
            if sum(reasons.values()) != b["scheduled"] - b["observed"]:
                res.problem("I2", f"ontime {day}: untimed reasons at {p} stops do not add up")
        pooled: Counter = Counter()
        for reasons in u["by_position"].values():
            pooled.update(reasons)
        if any(pooled[k] != v for k, v in u["by_reason"].items()):
            res.problem("I2", f"ontime {day}: untimed reasons by position != overall")
    cells = d.get("by_cell")
    if cells is not None:
        if _vsum(cells.values()) != [d["scheduled"], d["observed"], d["on_time"]]:
            res.problem("I2", f"ontime {day}: by_cell does not add up to the day's totals")
        per_trip: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        for trip_cells in d.get("by_trip_cell", {}).values():
            for c, v in trip_cells.items():
                for i in range(3):
                    per_trip[c][i] += v[i]
        if dict(per_trip) != cells:
            res.problem("I2", f"ontime {day}: by_trip_cell does not add up to by_cell")
        for trip, (seen, on_time) in d.get("by_trip", {}).items():
            v = _vsum(d.get("by_trip_cell", {}).get(trip, {}).values())
            if [v[1], v[2]] != [seen, on_time]:
                res.problem("I2", f"ontime {day}: trip {trip}: by_trip != its cells")
                break


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
    allow_old: bool = False,
) -> None:
    """I6. ``allow_old``: a newest day on an older version is only a note
    (the site job, which can run after a version change is pushed and
    before the nightly job has rebuilt the days)."""
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
        if newest in stale and not allow_old:
            res.problem("I6", f"{stage}: newest day {newest} is {by_day[newest]}, the code {want}")
        elif newest in stale:
            res.note("I6", f"{stage}: every day is still on {by_day[newest]}; the code is {want} "
                     "and the next nightly run rebuilds them")  # fmt: skip
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


def _sums(blocks: Iterable[dict[str, Any] | None]) -> dict[str, int]:
    total: Counter = Counter()
    for b in blocks:
        if b:
            _add(total, b)
    return {k: total[k] for k in COUNTS}


def check_periods(
    res: Result, data: Path, ontime: list[dict[str, Any]], quality: list[dict[str, Any]]
) -> None:
    """I8: the period files (``pipeline/periods.py``) reconcile with the day
    files: the headline window, each day, each week and month, and each hour
    pooled over the window are the sums of the days they cover, with shares
    that follow their counts."""
    head = load_json(data / "ontime" / "headline.json")
    periods = load_json(data / "ontime" / "periods.json")
    qperiods = load_json(data / "quality" / "periods.json")
    by_date = {d["service_date"]: d for d in ontime}
    service = [d for d in ontime if d.get("service_expected", True)]
    if head is not None:
        want = service[-head["window_service_days"] :]
        if [d["service_date"] for d in want][:1] != [head["first_service_date"]] or (
            want and want[-1]["service_date"] != head["last_service_date"]
        ):
            res.problem("I8", "headline.json: its window is not the last service days")
        if _counts(head["current"]) != _sums(want):
            res.problem("I8", "headline.json: counts are not the sum of its days")
        check_block(res, "headline.json current", head["current"], extra_shares=True)
        check_headline_ranges(res, head["current"], want)
    check_highlights(res, data, ontime, head)
    if periods is not None:
        grains = periods["grains"]
        for row in grains.get("day", []):
            d = by_date.get(row["period"])
            if d is None or _counts(row) != _counts(d):
                res.problem("I8", f"ontime periods day {row['period']}: not its day file")
        for grain in ("week", "month"):
            for row in grains.get(grain, []):
                days = [d for d in service if _period_of(d["service_date"], grain) == row["period"]]
                if _counts(row) != _sums(days):
                    res.problem(
                        "I8", f"ontime periods {grain} {row['period']}: not the sum of its days"
                    )
                check_block(res, f"ontime periods {grain} {row['period']}", row, extra_shares=True)
        pooled = grains.get("hour", {}).get("pooled", {})
        first, last = pooled.get("first_service_date"), pooled.get("last_service_date")
        window = [d for d in service if first and last and first <= d["service_date"] <= last]
        for row in pooled.get("rows", []):
            if _counts(row) != _sums(d.get("by_hour", {}).get(str(row["hour"])) for d in window):
                res.problem("I8", f"ontime periods hour {row['hour']}: not the sum of its days")
    if qperiods is not None:
        for row in qperiods["grains"].get("day", []):
            q = next((x for x in quality if x["service_date"] == row["period"]), None)
            cov = (q or {}).get("coverage") or {}
            m = row["metrics"].get("trip_coverage", {})
            if q is None or (cov and m.get("numerator") != cov.get("observed_scheduled_trips")):
                res.problem("I8", f"quality periods day {row['period']}: not its day file")
        for grain in ("week", "month"):
            for row in qperiods["grains"].get(grain, []):
                for name, m in row["metrics"].items():
                    if "numerator" in m and m["value"] != share(m["numerator"], m["denominator"]):
                        res.problem("I8", f"quality periods {grain} {row['period']} {name}: "
                                    "value does not follow its counts")  # fmt: skip


def check_highlights(
    res: Result, data: Path, ontime: list[dict[str, Any]], head: dict[str, Any] | None
) -> None:
    """I8, since ``periods/0.4.0``: ``ontime/highlights.json`` and the
    headline's ``since`` reconcile with the day files (``check_periods``)."""
    if head is not None and "since" in head:
        s = head["since"]
        raw = (load_json(data / "manifest" / "raw.json") or {}).get("days", {})
        dates = {d["service_date"] for d in ontime}
        service = [d for d in ontime if d.get("service_expected", True)]
        want = {
            "first_service_date": ontime[0]["service_date"] if ontime else None,
            "last_service_date": ontime[-1]["service_date"] if ontime else None,
            "days": len(ontime),
            "service_days": len(service),
            "departures_scheduled": sum(d.get("scheduled", 0) for d in service),
            "departures_timed": sum(d.get("observed", 0) for d in service),
            "departures_on_time": sum(d.get("on_time", 0) for d in service),
            "feed_responses_archived": sum(
                v.get("ok_rows", 0) for k, v in raw.items() if k in dates
            ),
        }
        if {k: s.get(k) for k in want} != want:
            res.problem("I8", "headline.json: 'since' is not the sum of the day files")
    high = load_json(data / "ontime" / "highlights.json")
    if high is None:
        return
    if head is not None and (high["first_service_date"], high["last_service_date"]) != (
        head["first_service_date"],
        head["last_service_date"],
    ):
        res.problem("I8", "highlights.json: its window is not the headline's")
    first, last = high["first_service_date"], high["last_service_date"]
    window = [d for d in ontime if d.get("service_expected", True)
              and first <= d["service_date"] <= last]  # fmt: skip
    if high.get("days") != len(window):
        res.problem("I8", "highlights.json: its count of days is not its window's")
    sys_ = high["system"]
    obs, on = sum(d.get("observed", 0) for d in window), sum(d.get("on_time", 0) for d in window)
    if (sys_["observed"], sys_["on_time"]) != (obs, on) or sys_[
        "on_time_share_of_observed"
    ] != share(on, obs, 4):
        res.problem("I8", "highlights.json: the system figure is not the sum of its days")
    late = high.get("running_late")
    if late is not None:
        for g in late["groups"]:
            where = f"highlights.json: {g['kind']} {g['key']}"
            if g["kind"] == "route":
                blocks = [(d.get("by_route") or {}).get(g["key"]) or {} for d in window]
                got = (sum(b.get("observed", 0) for b in blocks),
                       sum(b.get("on_time", 0) for b in blocks))  # fmt: skip
            else:
                cells = [v for d in window for c, v in (d.get("by_cell") or {}).items()
                         if c.rsplit("|", 2)[1] == g["key"]]  # fmt: skip
                got = (sum(v[1] for v in cells), sum(v[2] for v in cells))
            if got != (g["observed"], g["on_time"]):
                res.problem("I8", f"{where}: not the sum of its days")
            if g["on_time_share_of_observed"] != share(g["on_time"], g["observed"], 4):
                res.problem("I8", f"{where}: its share does not follow its counts")
            iv = g["interval95"]
            below = bool(iv and iv["high"] < sys_["on_time_share_of_observed"])
            if g["observed"] < high["min_observed"] or g["below_system"] != below:
                res.problem("I8", f"{where}: does not follow the rule for running late")
        want_items = sorted(
            (g for g in late["groups"] if g["below_system"]),
            key=lambda g: (g["on_time_share_of_observed"], g["kind"], g["key"]),
        )[: high["max_items"]]
        if [(i["kind"], i["key"]) for i in late["items"]] != [
            (g["kind"], g["key"]) for g in want_items
        ] or late["items"] != want_items:
            res.problem("I8", "highlights.json: the routes and times named are not the rule's")
    for t in high.get("ferry_terminals", []):
        total: Counter = Counter()
        for r in t["routes"]:
            stops = [((d.get("by_route_stop") or {}).get(r["route_id"]) or {}).get(t["stop_id"])
                     for d in window]  # fmt: skip
            counts = {k: sum((b or {}).get(k, 0) for b in stops)
                      for k in ("scheduled", "observed", "on_time")}  # fmt: skip
            if counts != {k: r[k] for k in counts}:
                res.problem("I8", f"highlights.json: ferry stop {t['stop_id']} route "
                            f"{r['route_id']} is not the sum of its days")  # fmt: skip
            total.update(counts)
        if any(t[k] != total[k] for k in ("scheduled", "observed", "on_time")) or t[
            "on_time_share_of_observed"
        ] != share(t["on_time"], t["observed"], 4):
            res.problem("I8", f"highlights.json: ferry stop {t['stop_id']} does not add up")


def _reason_sum(blocks: Iterable[dict[str, int]]) -> dict[str, int]:
    total: Counter = Counter()
    for b in blocks:
        total.update(b)
    return {k: v for k, v in total.items() if v}


def check_headline_ranges(res: Result, cur: dict[str, Any], window: list[dict[str, Any]]) -> None:
    """I8, since ``periods/0.3.0``: the untimed reasons are the sum of the
    window's day files, and each untimed day agrees with its quality day
    file; the worst-case range follows the counts; the weighted share is
    recomputed from its cells, and each cell is the sum of its members over
    the window's day files."""
    sched, seen, on_time = cur["scheduled"], cur["observed"], cur["on_time"]
    b = cur.get("bounds")
    if b is not None and (
        b["low"] != share(on_time, sched, 4) or b["high"] != share(on_time + sched - seen, sched, 4)
    ):
        res.problem("I8", "headline.json: the worst-case range does not follow its counts")
    u = cur.get("untimed")
    if u is not None:
        if _reason_sum([u["by_reason"]]) != _reason_sum(d["untimed"]["by_reason"] for d in window):
            res.problem("I8", "headline.json: untimed reasons are not the sum of its days")
        if u["departures"] != sched - seen or sum(u["by_reason"].values()) != sched - seen:
            res.problem("I8", "headline.json: untimed reasons do not add up to the untimed")
        for p, block in u["by_position"].items():
            got = _reason_sum([block["by_reason"]])
            if (
                got != _reason_sum(d["untimed"]["by_position"][p] for d in window)
                or sum(got.values()) != block["scheduled"] - block["observed"]
            ):
                res.problem("I8", f"headline.json: untimed reasons at {p} stops do not add up")
    a = cur.get("adjusted")
    if a is None:
        return
    fine: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for d in window:
        for c, v in d["by_cell"].items():
            for i in range(3):
                fine[c][i] += v[i]
    seen_members: list[str] = []
    num = den = 0.0
    top = 0.0
    left_out = 0
    for cell in sorted(a["cells"], key=lambda c: c["cell"]):
        v = [cell["scheduled"], cell["observed"], cell["on_time"]]
        seen_members += cell["members"]
        if _vsum(fine[m] for m in cell["members"]) != v:
            res.problem("I8", f"headline.json: weighting cell {cell['cell']} is not its days")
        if "*" not in cell["cell"] and v[1] < a["min_timed_per_cell"]:
            res.problem("I8", f"headline.json: weighting cell {cell['cell']} is too small")
        if v[1] == 0:
            left_out += v[0]
            continue
        num += v[0] * v[2] / v[1]
        den += v[0]
        top = max(top, v[0] / v[1])
    if sorted(seen_members) != sorted(fine):
        res.problem("I8", "headline.json: the weighting cells do not cover each cell once")
    value = round(num / den, 4) if den else None
    if (a["value"], a["max_weight"], a["scheduled_left_out"]) != (
        value,
        round(top, 4) if den else None,
        left_out,
    ) or a["shown"] != (value is not None and top < a["max_weight_shown"]):
        res.problem("I8", "headline.json: the weighted share does not follow its cells")


def check_untimed_sources(
    res: Result, ontime: list[dict[str, Any]], quality: list[dict[str, Any]]
) -> None:
    """I8: a day's untimed reasons in the on-time file match the quality
    file's when the feed canceled no trip (the quality file counts a
    canceled trip's departures; the on-time file leaves them out)."""
    by_date = {q["service_date"]: q for q in quality}
    for d in ontime:
        q = by_date.get(d["service_date"])
        tp = (((q or {}).get("events") or {}).get("stop_coverage") or {}).get("timepoints")
        if "untimed" not in d or tp is None or (d.get("canceled") or {}).get("trip_ids"):
            continue
        if _reason_sum([d["untimed"]["by_reason"]]) != _reason_sum([tp["not_observed_by_reason"]]):
            res.problem("I8", f"{d['service_date']}: untimed reasons differ from the quality file")


def _period_of(day: str, grain: str) -> str:
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat() if grain == "week" else f"{d:%Y-%m}"


def check_compare(
    res: Result,
    data: Path,
    ontime: list[dict[str, Any]],
    versions: dict[str, str],
    allow_old: bool = False,
) -> None:
    """I13. With ``allow_old`` (the site job, as for I6) a summary made by an
    older version of the stage is a note."""
    from pipeline.compare import WINDOW_DAYS as COMPARE_DAYS

    summary = load_json(data / "compare" / "summary.json")
    if summary is None:
        return
    want = versions.get("compare")
    if want and summary.get("compare_version") != want:
        text = f"compare/summary.json is {summary.get('compare_version')}, not {want}"
        (res.note if allow_old else res.problem)("I13", text)
        return
    it = summary["island_transit"]
    window = [d for d in ontime if d.get("service_expected", True)][-COMPARE_DAYS:]
    if not window:
        return
    span = (window[0]["service_date"], window[-1]["service_date"])
    if (it.get("first_service_date"), it.get("last_service_date")) != span:
        res.problem("I13", "compare/summary.json: not the last service days")
    if (it.get("observed"), it.get("on_time")) != (
        sum(d["observed"] for d in window),
        sum(d["on_time"] for d in window),
    ):
        res.problem("I13", "compare/summary.json: counts are not the sum of its days")
    head = load_json(data / "ontime" / "headline.json")
    cur = (head or {}).get("current") or {}
    if head is not None and (
        (head.get("first_service_date"), head.get("last_service_date")) != span
        or it.get("headline_on_time_share") != cur.get("on_time_share_of_observed")
    ):
        res.problem("I13", "compare/summary.json: not the home page's figure and days")
    early = [d.get("last_stop_early_arrivals") for d in window]
    if None in early:
        res.note("I13", "the one-rule difference waits for days rebuilt by ontime/0.8.0")
        return
    early_last, recount = it.get("last_stop_early_arrivals"), it.get("recount_on_time")
    if early_last != sum(early):  # type: ignore[arg-type]
        res.problem("I13", "compare/summary.json: early last-stop arrivals are not its days'")
    if recount is None or it["on_time"] - recount != early_last:
        res.problem("I13", "the published figure less the recount is not the early arrivals")
    elif it.get("recount_under_our_rule") != share(recount, it["observed"], 4):
        res.problem("I13", "compare/summary.json: the recount's share does not follow its counts")
    for p in summary.get("peers", []):
        same, pool = p.get("island_same_hours"), p.get("our_method_on_their_feed")
        if same is None or pool is None:
            continue
        for day in same.get("service_dates", []):
            weekday = date.fromisoformat(day).strftime("%A")
            if not (pool["pool_first_date"] <= day <= pool["pool_last_date"]) or (
                weekday not in pool.get("hours", {})
            ):
                res.problem("I13", f"{p['agency']}: {day} is outside its pool's days")


def light(res: Result, data: Path, versions: dict[str, str], allow_old: bool = False) -> None:
    """I1 to I8 and I13."""
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
    check_versions(res, data, versions, ontime, quality, allow_old)
    check_complete(res, data, ontime_dir, quality_dir)
    check_periods(res, data, ontime, quality)
    check_untimed_sources(res, ontime, quality)
    check_compare(res, data, ontime, versions, allow_old)
    res.ran += ["I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I13"]


# --- I9 to I11: the stop-event files of the last days ---------------------------


Fetch = Callable[[str, str, str], Path]


def local_fetch(releases: Path) -> Fetch:
    """Release files from a folder laid out as ``<tag>/<name>``, checked by SHA-256."""
    from pipeline.common import local_release_file, sha256_file

    def get(tag: str, name: str, sha256: str) -> Path:
        path = local_release_file(releases, tag, name, sha256)
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
        _check_relay(res, day, t)
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
        # Times a relay vehicle's reports gave are that vehicle's (below).
        mine = [e for e in rows if e.get("report_source") not in RELAY_SOURCES]
        times = [e[f"{k}_time"] for e in mine for k in ("arrival", "departure")]
        times = [x for x in times if x is not None]
        vehicle = rows[0]["vehicle_key"]
        if times and vehicle is not None:
            spans[vehicle].append((min(times), max(times), trip))
    for t in tracks:
        if t.get("relay_vehicle_key") and t.get("relay_from") and t.get("relay_to"):
            spans[t["relay_vehicle_key"]].append((t["relay_from"], t["relay_to"], t["trip_id"]))
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


def _check_relay(res: Result, day: str, t: dict[str, Any]) -> None:
    """I9 for the relay columns of one trip track (absent before events/0.6.0)."""
    if "relay_vehicle_key" not in t:
        return
    other, n = t["relay_vehicle_key"], t["reports_relay"]
    first, last = t["relay_from"], t["relay_to"]
    if other is None:
        if n or first is not None or last is not None:
            res.problem("I9", f"{day} trip {t['trip_id']}: relay reports without a relay vehicle")
        return
    margin = timedelta(minutes=30)
    if (
        other == t["vehicle_key"]
        or not n
        or first is None
        or last is None
        or first > last
        or first < t["scheduled_start"] - margin
        or last > t["scheduled_end"] + margin
    ):
        res.problem("I9", f"{day} trip {t['trip_id']}: relay with vehicle {other} does not add up")


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
    if again["last_stop_early_arrivals"] != day.get("last_stop_early_arrivals"):
        res.problem("I11", f"{d}: recount of early last-stop arrivals differs from the day file")
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
    allow_old: bool = False,
) -> Result:
    res = Result()
    light(res, data, current_versions() if versions is None else versions, allow_old)
    if not light_only:
        fetch = (
            local_fetch(releases) if releases else github_fetch(repo, work or Path("work/check"))
        )
        heavy(res, data, fetch, days)
        releases_agree(res, data, releases, repo)
    return res


def add_extra(res: Result, checks: list[str]) -> None:
    """Results of checks run outside this module, given as ``NAME=CODE``."""
    for item in checks:
        name, _, code = item.partition("=")
        res.ran.append(name)
        if code.strip() != "0":
            res.problem(name, f"exited with code {code.strip() or 'none'}")


def gate_report(res: Result, data: Path, *, light_only: bool, commit: str | None) -> dict[str, Any]:
    """What ran and what it found, for ``checks/gate.json``."""
    quality = data / "quality" / "daily.csv"
    latest = None
    if quality.exists():
        with quality.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        latest = rows[-1]["service_date"] if rows else None
    run = os.environ.get("GITHUB_RUN_ID")
    return {
        "gate_version": GATE_VERSION,
        "passed": not res.problems,
        "mode": "light" if light_only else "full",
        "rules": res.ran,
        "rules_count": len(res.ran),
        "problems": res.problems,
        "notes": res.notes,
        "latest_service_date": latest,
        "checked_commit": commit,
        "ran_at": stamp(),
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "run_url": (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', REPO)}/actions/runs/{run}"
            if run
            else None
        ),
    }


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
    ap.add_argument(
        "--light", action="store_true", help="only I1 to I8 and I13 (no third-party library)"
    )
    ap.add_argument("--releases", type=Path, help="local Release files, <tag>/<name>")
    ap.add_argument("--work", type=Path, help="where Release files are downloaded or found")
    ap.add_argument("--days", type=int, default=3, help="days for I9 to I11 (0: all)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    ap.add_argument("--report", type=Path, help="write what ran as JSON (checks/gate.json)")
    ap.add_argument("--commit", help="the data branch commit being checked, for --report")
    ap.add_argument("--extra-check", action="append", default=[], metavar="NAME=CODE",
                    help="the exit code of a check run outside this module")  # fmt: skip
    ap.add_argument(
        "--allow-old-versions",
        action="store_true",
        help="I6: a newest day on an older version is a note, not a problem",
    )
    args = ap.parse_args(argv)
    res = run(
        args.data,
        light_only=args.light,
        releases=args.releases,
        work=args.work,
        repo=args.repo,
        days=args.days,
        allow_old=args.allow_old_versions,
    )
    add_extra(res, args.extra_check)
    if args.report:
        import json

        out = gate_report(res, args.data, light_only=args.light, commit=args.commit)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return report(res)


if __name__ == "__main__":
    sys.exit(main())
