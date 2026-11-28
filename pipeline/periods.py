"""Figures by period: the home page headline, and hour, day, week and month
views for the On time and Data quality pages.

    python -m pipeline.periods --data data-branch

Writes, from the day files on the data branch:

- ``ontime/headline.json``: the last 28 service days, pooled. Unlike
  ``ontime/recent.json`` (the route tables, which start over at a new
  printed timetable), the headline spans timetable changes, so it does not
  reset on a schedule change. It holds the counts and shares, the 28 service
  days before (once there are at least ``MIN_PREVIOUS`` of them), the change
  in percentage points, and one point per day for a small trend line.
- ``ontime/periods.json``: the on-time figures by ``hour`` of the day (the
  last 28 service days pooled, and the latest day alone), by ``day``, by
  ``week`` (Monday to Sunday, Pacific) and by ``month``. A week or month the
  archive does not cover in full is marked ``partial``.
- ``quality/periods.json``: the data quality figures by the same periods.
  Each figure says how it pools several days: a ratio of sums (for example
  observed trips over scheduled trips, so a busy day counts more), a sum, or
  the median of the daily values with their range. Figures with no hourly
  detail say so instead of showing one.

Since ``periods/0.3.0`` the headline also says how sure it is, three ways
(``current.untimed``, ``current.bounds``, ``current.adjusted``): why the
departures we could not time went untimed, pooled over the window and split
by position in the trip; the worst-case range, if every untimed departure
had been late or every one on time (no-assumption bounds); and the on-time
share with each timed departure weighted for the untimed ones like it (same
route, hour band and position in the trip), with an interval from the same
resampling of whole trips. ``quality/periods.json`` carries the share of
scheduled departures untimed for each reason.

Counts are summed and shares recomputed from the sums; medians come from
every pooled deviation. Days without service (holidays) appear in the day
view, marked, and are left out of everything that pools days. Standard
library only, like the publishing gate that checks these files (I8).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from pipeline.common import DayFiles, load_json
from pipeline.stats import median, share

PERIODS_VERSION = "periods/0.4.0"  # 0.4.0: highlights.json (running late, ferries), since
WINDOW = 28  # service days
MIN_PREVIOUS = 14  # service days before a "previous window" is shown
COUNTS = ("scheduled", "observed", "on_time", "early", "late")
GRAINS = ("hour", "day", "week", "month")
RESAMPLES = 2000
SEED = 20261127  # fixed, so the interval is the same each time it is computed
MIN_CELL = 10  # timed departures a weighting cell needs before it stands alone
MAX_WEIGHT = 10  # the weighted share is shown only while every weight stays under this
UNTIMED_REASONS = ("gap", "skipped", "not_reached", "no_reports", "past_when_first_seen")
REASON_NAMES = {
    "gap": "the feed went quiet",
    "skipped": "the bus left out that part of the route",
    "not_reached": "the bus stopped reporting before it got there",
    "no_reports": "the trip sent nothing",
    "past_when_first_seen": "the bus was already past when it first reported",
    "unknown": "no reason recorded",
}
POSITIONS = ("first", "middle", "last")
MIN_OBSERVED = 10  # timed departures a route or time of day needs to be named (site/lib.js)
MAX_HIGHLIGHTS = 3  # routes or times of day named as running late
# The hour bands of the weighting cells (pipeline/events/ontime.py HOUR_BANDS),
# as the hours they cover, for the page to put in words.
BAND_HOURS = {"early": [0, 7], "morning": [7, 9], "midday": [9, 15], "afternoon": [15, 18],
              "evening": [18, 24]}  # fmt: skip
FERRY_TERMINALS = Path(__file__).resolve().parents[1] / "reference" / "ferry-terminals.csv"

_share = partial(share, digits=4)


def _service(d: dict[str, Any]) -> bool:
    return bool(d.get("service_expected", True))


def _rounded(x: float | None) -> float | None:
    return round(x, 1) if x is not None else None


def ontime_block(days: Iterable[dict[str, Any]], part: Callable[[dict[str, Any]], Any]) -> dict:
    """Counts summed, shares recomputed, and the median of every deviation,
    over ``part(day)`` of each day (a block of counts with ``deviations_s``,
    or None to skip the day)."""
    counts = dict.fromkeys(COUNTS, 0)
    devs: list[int] = []
    n = 0
    for d in days:
        b = part(d)
        if b is None:
            continue
        n += 1
        for k in COUNTS:
            counts[k] += b.get(k, 0)
        devs += b.get("deviations_s", [])
    return {
        "days": n,
        **counts,
        "observed_share": _share(counts["observed"], counts["scheduled"]),
        "on_time_share_of_observed": _share(counts["on_time"], counts["observed"]),
        "early_share_of_observed": _share(counts["early"], counts["observed"]),
        "late_share_of_observed": _share(counts["late"], counts["observed"]),
        "deviation_s_median": _rounded(median(devs)),
        "deviations_counted": len(devs),
    }


def trip_interval(days: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A 95% interval for the on-time share of observed departures, by
    resampling whole trips (a bootstrap): departures of one trip are not
    independent, since a bus that falls behind stays behind. Each resample
    draws as many (day, trip) pairs as there are, with replacement; the
    interval runs from the 2.5th to the 97.5th percentile of the shares.
    None when a day has no counts per trip (made before ``ontime/0.5.0``)."""
    pairs = []
    for d in days:
        if "by_trip" not in d:
            return None
        pairs += list(d["by_trip"].values())
    return pair_interval(pairs)


def pair_interval(pairs: list[list[int]]) -> dict[str, Any] | None:
    """The bootstrap of ``trip_interval`` over given ``[observed, on_time]``
    pairs, one per (day, trip). None for fewer than two trips with a time."""
    import numpy as np

    pairs = [p for p in pairs if p[0] > 0]
    if len(pairs) < 2:
        return None
    counts = np.array(pairs, dtype=np.int64)
    rng = np.random.default_rng(SEED)
    shares = np.empty(RESAMPLES)
    for start in range(0, RESAMPLES, 200):
        pick = rng.integers(0, len(counts), size=(min(200, RESAMPLES - start), len(counts)))
        drawn = counts[pick].sum(axis=1)
        shares[start : start + len(drawn)] = drawn[:, 1] / drawn[:, 0]
    low, high = np.quantile(shares, [0.025, 0.975])
    return {
        "low": round(float(low), 4),
        "high": round(float(high), 4),
        "trips": len(pairs),
        "resamples": RESAMPLES,
        "method": "bootstrap over trips",
    }


def _day_devs(d: dict[str, Any]) -> dict[str, Any]:
    """The day's totals with every deviation (from its stops)."""
    devs = [x for stops in d.get("by_route_stop", {}).values() for b in stops.values()
            for x in b["deviations_s"]]  # fmt: skip
    return {**{k: d.get(k, 0) for k in COUNTS}, "deviations_s": devs}


def week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def month_of(day: date) -> str:
    return f"{day:%Y-%m}"


def _span(key: str, grain: str) -> tuple[date, date]:
    if grain == "week":
        start = date.fromisoformat(key)
        return start, start + timedelta(days=6)
    start = date.fromisoformat(f"{key}-01")
    nxt = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def grouped(days: list[dict[str, Any]], grain: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in days:
        day = date.fromisoformat(d["service_date"])
        out[week_of(day).isoformat() if grain == "week" else month_of(day)].append(d)
    return dict(sorted(out.items()))


def _partial(key: str, grain: str, first: date, last: date) -> bool:
    start, end = _span(key, grain)
    return start < first or end > last


def headline(
    days: list[dict[str, Any]], raw_days: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    service = [d for d in days if _service(d)]
    if not service:
        return None
    window, before = service[-WINDOW:], service[:-WINDOW][-WINDOW:]
    now = ontime_block(window, _day_devs)
    now["on_time_share_interval95"] = trip_interval(window)
    now["untimed"] = untimed_block(window)
    now["bounds"] = bounds(now)
    now["adjusted"] = adjusted(window)
    prev = ontime_block(before, _day_devs) if len(before) >= MIN_PREVIOUS else None
    change = None
    if prev and now["on_time_share_of_observed"] is not None and prev["on_time_share_of_observed"]:
        change = round(
            100 * (now["on_time_share_of_observed"] - prev["on_time_share_of_observed"]), 1
        )
    first, last = window[0]["service_date"], window[-1]["service_date"]
    holidays = [
        d["service_date"] for d in days if not _service(d) and first <= d["service_date"] <= last
    ]
    timetables = {(d.get("timepoints") or "").partition("__timetable-")[2][:10] for d in window}
    previous = None
    if prev is not None:
        previous = {
            "first_service_date": before[0]["service_date"],
            "last_service_date": before[-1]["service_date"],
            **prev,
        }
    spark = [
        {
            "service_date": d["service_date"],
            "on_time_share_of_observed": d.get("on_time_share_of_observed"),
            "observed_share": d.get("observed_share"),
        }
        for d in window
    ]
    return {
        "periods_version": PERIODS_VERSION,
        "window_service_days": WINDOW,
        "first_service_date": first,
        "last_service_date": last,
        "days_without_service": holidays,
        "timetables": sorted(timetables - {""}),
        "current": now,
        "previous": previous,
        "previous_needs_service_days": MIN_PREVIOUS,
        "change_points": change,
        "spark": spark,
        "since": since(days, raw_days or {}),
    }


def since(days: list[dict[str, Any]], raw_days: dict[str, Any]) -> dict[str, Any]:
    """Since ``periods/0.4.0``: the whole archive, for the line under the
    headline. Departures timed and on time are summed over every day file;
    feed responses archived are the ``ok_rows`` of ``manifest/raw.json``
    (every saved answer of the three live feeds) for the same days."""
    dates = {d["service_date"] for d in days}
    service = [d for d in days if _service(d)]
    return {
        "first_service_date": days[0]["service_date"],
        "last_service_date": days[-1]["service_date"],
        "days": len(days),
        "service_days": len(service),
        "departures_scheduled": sum(d.get("scheduled", 0) for d in service),
        "departures_timed": sum(d.get("observed", 0) for d in service),
        "departures_on_time": sum(d.get("on_time", 0) for d in service),
        "feed_responses_archived": sum(
            v.get("ok_rows", 0) for k, v in raw_days.items() if k in dates
        ),
    }


def _reasons(blocks: Iterable[dict[str, int] | None]) -> dict[str, int]:
    total: dict[str, int] = defaultdict(int)
    for b in blocks:
        for k, v in (b or {}).items():
            total[k] += v
    return {**{r: total[r] for r in UNTIMED_REASONS},
            **{r: total[r] for r in sorted(set(total) - set(UNTIMED_REASONS))}}  # fmt: skip


def untimed_block(days: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Why the window's departures went untimed: counts by reason, overall and
    by position in the trip (with that position's scheduled and timed counts).
    None when a day lacks the reasons (made before ``ontime/0.7.0``)."""
    if not days or any("untimed" not in d for d in days):
        return None
    by_position = {}
    for p in POSITIONS:
        sched = sum(d["by_position"][p]["scheduled"] for d in days)
        seen = sum(d["by_position"][p]["observed"] for d in days)
        by_position[p] = {
            "scheduled": sched,
            "observed": seen,
            "untimed": sched - seen,
            "observed_share": _share(seen, sched),
            "by_reason": _reasons(d["untimed"]["by_position"][p] for d in days),
        }
    return {
        "departures": sum(d["untimed"]["departures"] for d in days),
        "by_reason": _reasons(d["untimed"]["by_reason"] for d in days),
        "by_position": by_position,
        "reason_names": REASON_NAMES,
    }


def bounds(b: dict[str, Any]) -> dict[str, Any]:
    """The worst-case range of the on-time share of scheduled departures: low
    if every untimed departure was late, high if every one was on time
    (Manski's no-assumption bounds)."""
    untimed = b["scheduled"] - b["observed"]
    return {
        "low": _share(b["on_time"], b["scheduled"]),
        "high": _share(b["on_time"] + untimed, b["scheduled"]),
        "untimed": untimed,
        "method": "no-assumption bounds",
    }


def _parent(cell: str, level: int) -> str:
    route, band, _ = cell.rsplit("|", 2)
    return (f"{route}|{band}|*", f"{route}|*|*", "*|*|*")[level - 1]


def merge_cells(by_cell: dict[str, list[int]]) -> dict[str, str]:
    """Each fine cell (``route|band|position``) -> the cell it is weighted in.
    A cell with fewer than ``MIN_CELL`` timed departures joins the other small
    cells of its route and hour band (``route|band|*``); a group still that
    small joins its route's (``route|*|*``), then everything left over
    (``*|*|*``), which stands however small it is."""
    target = {c: c for c in sorted(by_cell)}
    for level in (1, 2, 3):
        timed: dict[str, int] = defaultdict(int)
        for c, t in target.items():
            timed[t] += by_cell[c][1]
        for c, t in target.items():
            if timed[t] < MIN_CELL:
                target[c] = _parent(c, level)
    return target


def weighted_share(cells: dict[str, list[int]]) -> tuple[float | None, float | None, int]:
    """(on-time share with each timed departure weighted by scheduled over
    timed in its cell, the largest weight, scheduled departures in cells with
    nothing timed). Those cells are left out, which assumes their departures
    ran like the rest."""
    num = den = 0.0
    top: float | None = None
    left_out = 0
    for key in sorted(cells):
        sched, timed, on_time = cells[key]
        if timed == 0:
            left_out += sched
            continue
        num += sched * on_time / timed
        den += sched
        top = max(top or 0.0, sched / timed)
    return (num / den if den else None), top, left_out


def adjusted(days: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The on-time share of scheduled departures, with each timed departure
    weighted for the untimed departures of its cell (a weighting-class
    adjustment for nonresponse), and a 95% interval from resampling whole
    (day, trip) pairs, the cells kept and their weights recomputed in each
    resample. None when a day lacks cell counts (before ``ontime/0.7.0``)."""
    if not days or any("by_cell" not in d for d in days):
        return None
    fine: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for d in days:
        for c, v in d["by_cell"].items():
            for i in range(3):
                fine[c][i] += v[i]
    if not fine:
        return None
    target = merge_cells(fine)
    merged: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    members: dict[str, list[str]] = defaultdict(list)
    for c, t in target.items():
        members[t].append(c)
        for i in range(3):
            merged[t][i] += fine[c][i]
    value, top, left_out = weighted_share(merged)
    shown = value is not None and top is not None and top < MAX_WEIGHT
    interval = _weighted_interval(days, target, sorted(merged)) if value is not None else None
    return {
        "value": round(value, 4) if value is not None else None,
        "interval95": interval,
        "max_weight": round(top, 4) if top is not None else None,
        "shown": shown,
        "scheduled_left_out": left_out,
        "min_timed_per_cell": MIN_CELL,
        "max_weight_shown": MAX_WEIGHT,
        "method": "weighting cells of route, hour band and position; bootstrap over trips",
        "cells": [
            {"cell": k, "scheduled": merged[k][0], "observed": merged[k][1],
             "on_time": merged[k][2], "members": members[k]}
            for k in sorted(merged)
        ],
    }  # fmt: skip


def _weighted_interval(
    days: list[dict[str, Any]], target: dict[str, str], keys: list[str]
) -> dict[str, Any] | None:
    import numpy as np

    col = {k: i for i, k in enumerate(keys)}
    rows = []
    for d in days:
        for cells in d["by_trip_cell"].values():
            row = np.zeros(3 * len(keys))
            for c, v in cells.items():
                j = 3 * col[target[c]]
                row[j : j + 3] += v
            rows.append(row)
    if len(rows) < 2:
        return None
    m = np.array(rows)
    n = len(m)
    rng = np.random.default_rng(SEED)
    shares = np.empty(RESAMPLES)
    for start in range(0, RESAMPLES, 200):
        k = min(200, RESAMPLES - start)
        pick = rng.integers(0, n, size=(k, n))
        times = np.zeros((k, n))
        np.add.at(times, (np.arange(k)[:, None], pick), 1)
        total = (times @ m).reshape(k, len(keys), 3)
        sched, timed, on_time = total[..., 0], total[..., 1], total[..., 2]
        ok = timed > 0
        rate = np.divide(on_time, timed, out=np.zeros_like(on_time), where=ok)
        den = (sched * ok).sum(axis=1)
        shares[start : start + k] = (sched * rate).sum(axis=1) / den
    low, high = np.quantile(shares, [0.025, 0.975])
    return {
        "low": round(float(low), 4),
        "high": round(float(high), 4),
        "trips": n,
        "resamples": RESAMPLES,
        "method": "bootstrap over trips",
    }


def ontime_periods(days: list[dict[str, Any]]) -> dict[str, Any]:
    service = [d for d in days if _service(d)]
    window = service[-WINDOW:]
    hours = sorted({int(h) for d in window for h in d.get("by_hour", {})})
    first = date.fromisoformat(days[0]["service_date"]) if days else None
    last = date.fromisoformat(days[-1]["service_date"]) if days else None
    out: dict[str, Any] = {"periods_version": PERIODS_VERSION, "grains": {}}

    def hour_part(hour: str) -> Callable[[dict[str, Any]], Any]:
        return lambda d: d.get("by_hour", {}).get(hour)

    latest = service[-1] if service else None
    out["grains"]["hour"] = {
        "pooled": {
            "first_service_date": window[0]["service_date"] if window else None,
            "last_service_date": window[-1]["service_date"] if window else None,
            "rows": [{"hour": h, **ontime_block(window, hour_part(str(h)))} for h in hours],
        },
        "latest_day": {
            "service_date": latest["service_date"] if latest else None,
            "rows": [
                {"hour": int(h), **ontime_block([latest], hour_part(h))}
                for h in sorted(latest["by_hour"], key=int)
            ]
            if latest
            else [],
        },
    }
    out["grains"]["day"] = [
        {"period": d["service_date"], "service_expected": _service(d),
         **ontime_block([d], _day_devs)}
        for d in days
    ]  # fmt: skip
    for grain in ("week", "month"):
        out["grains"][grain] = [
            {"period": key, "partial": _partial(key, grain, first, last),
             "days_without_service": [d["service_date"] for d in group if not _service(d)],
             **ontime_block([d for d in group if _service(d)], _day_devs)}
            for key, group in grouped(days, grain).items()
        ] if first and last else []  # fmt: skip
    return out


# --- highlights (periods/0.4.0) -----------------------------------------------------


def _group_trips(window: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, list[int]]]:
    """(kind, key) -> "day|trip" -> [observed, on_time], from each day's
    ``by_trip_cell``: kind ``route`` keyed by route id, kind ``time_of_day``
    keyed by hour band."""
    out: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(dict)
    for d in window:
        for trip, cells in d["by_trip_cell"].items():
            for c, v in cells.items():
                route, band, _ = c.rsplit("|", 2)
                for g in (("route", route), ("time_of_day", band)):
                    acc = out[g].setdefault(f"{d['service_date']}|{trip}", [0, 0])
                    acc[0] += v[1]
                    acc[1] += v[2]
    return out


def route_names(days: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Route id -> its names, the latest day's winning."""
    names: dict[str, dict[str, str]] = {}
    for d in days:
        names.update(d.get("routes") or {})
    return names


def running_late(window: list[dict[str, Any]], system: float | None) -> dict[str, Any] | None:
    """The routes and times of day whose on-time share is clearly below the
    system's over the window: each with at least ``MIN_OBSERVED`` timed
    departures, a 95% interval from resampling its whole trips, and that
    interval wholly below the system's share. The ``MAX_HIGHLIGHTS`` lowest
    shares are named (``items``); ``groups`` lists every route and time of
    day considered, so the choice can be checked. None when a day lacks
    counts per trip and cell (before ``ontime/0.7.0``)."""
    if not window or any("by_trip_cell" not in d for d in window):
        return None
    names = route_names(window)
    groups = []
    for (kind, key), trips in sorted(_group_trips(window).items()):
        observed = sum(v[0] for v in trips.values())
        on_time = sum(v[1] for v in trips.values())
        if observed < MIN_OBSERVED:
            continue
        g: dict[str, Any] = {"kind": kind, "key": key}
        if kind == "route":
            r = names.get(key, {})
            g |= {"short_name": r.get("short_name", ""), "long_name": r.get("long_name", "")}
        else:
            g["hours"] = BAND_HOURS.get(key)
        iv = pair_interval(list(trips.values()))
        below = bool(iv and system is not None and iv["high"] < system)
        groups.append({**g, "observed": observed, "on_time": on_time,
                       "on_time_share_of_observed": _share(on_time, observed),
                       "interval95": iv, "below_system": below})  # fmt: skip
    items = pick_late(groups)
    return {"items": items, "groups": groups}


def pick_late(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The groups clearly below the system, lowest share first, at most
    ``MAX_HIGHLIGHTS``."""
    below = [g for g in groups if g["below_system"]]
    below.sort(key=lambda g: (g["on_time_share_of_observed"], g["kind"], g["key"]))
    return below[:MAX_HIGHLIGHTS]


def read_ferry_terminals(path: Path = FERRY_TERMINALS) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def ferry_block(
    window: list[dict[str, Any]], terminals: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Each ferry terminal stop of ``reference/ferry-terminals.csv``: the
    departures (and, at a route's last stop, arrivals) timed there over the
    window, summed over the bus routes the file names, with the share on
    time by the headline's rule."""
    names = route_names(window)
    by_short: dict[str, list[str]] = defaultdict(list)
    for rid, r in sorted(names.items()):
        by_short[r.get("short_name", "")].append(rid)
    out = []
    for t in terminals:
        stop, wanted = t["stop_id"].strip(), t["bus_routes"].split()
        routes = []
        for short in wanted:
            for rid in by_short.get(short, []):
                blocks = [d.get("by_route_stop", {}).get(rid, {}).get(stop) for d in window]
                blocks = [b for b in blocks if b]
                c = {k: sum(b.get(k, 0) for b in blocks) for k in COUNTS[:3]}
                position = blocks[-1].get("position") if blocks else None
                routes.append({"route_id": rid, "short_name": short, "position": position, **c})
        total = {k: sum(r[k] for r in routes) for k in ("scheduled", "observed", "on_time")}
        out.append({
            "stop_id": stop, "stop_name": t["stop_name"], "terminal": t["terminal"],
            "ferry_route": t["ferry_route"], "bus_routes": wanted,
            "routes_not_found": [s for s in wanted if s not in by_short],
            **total, "on_time_share_of_observed": _share(total["on_time"], total["observed"]),
            "routes": routes,
        })  # fmt: skip
    return out


def highlights(
    days: list[dict[str, Any]], terminals: list[dict[str, str]] | None = None
) -> dict[str, Any] | None:
    """``ontime/highlights.json``: over the headline's window (the last
    ``WINDOW`` service days), what is running late and how buses do at the
    ferry terminals. I8 checks it against the day files."""
    service = [d for d in days if _service(d)]
    if not service:
        return None
    window = service[-WINDOW:]
    observed = sum(d.get("observed", 0) for d in window)
    on_time = sum(d.get("on_time", 0) for d in window)
    system = _share(on_time, observed)
    return {
        "periods_version": PERIODS_VERSION,
        "window_service_days": WINDOW,
        "days": len(window),
        "first_service_date": window[0]["service_date"],
        "last_service_date": window[-1]["service_date"],
        "system": {"observed": observed, "on_time": on_time, "on_time_share_of_observed": system},
        "min_observed": MIN_OBSERVED,
        "max_items": MAX_HIGHLIGHTS,
        "band_hours": BAND_HOURS,
        "running_late": running_late(window, system),
        "ferry_terminals": ferry_block(
            window, read_ferry_terminals() if terminals is None else terminals
        ),
    }


# --- quality ----------------------------------------------------------------------


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _feed_agreement(o: dict[str, Any] | None) -> tuple[int, int] | None:
    fa = _get(o, "validation", "feed_agreement")
    if not fa or not fa.get("by_route"):
        return None
    return (sum(b["within_tolerance"] for b in fa["by_route"].values()),
            sum(b["compared"] for b in fa["by_route"].values()))  # fmt: skip


# Each figure: (how it pools, a function of (quality day, on-time day) giving
# (numerator, denominator) for a ratio, or the value for a sum or a median).
RATIO, SUM, MEDIAN = "ratio of sums", "sum", "median of daily values"


def _pair(*paths: tuple[str, ...]) -> Callable[..., Any]:
    """A (numerator, denominator) read from the quality day file."""
    return lambda q, o: tuple(_get(q, *p) for p in paths)


def _value(*path: str) -> Callable[..., Any]:
    return lambda q, o: _get(q, *path)


QUALITY_METRICS: dict[str, tuple[str, Callable[..., Any], str]] = {
    "trip_coverage": (
        RATIO,
        _pair(("coverage", "observed_scheduled_trips"), ("coverage", "scheduled_trips")),
        "observed trips over scheduled trips",
    ),
    "timepoint_coverage": (
        RATIO,
        _pair(("events", "stop_coverage", "timepoints", "observed"),
              ("events", "stop_coverage", "timepoints", "scheduled")),
        "timepoint events with a time over scheduled timepoint events",
    ),
    "vehicle_uptime": (
        RATIO,
        _pair(("paradata", "vehiclePositions", "slots_ok"),
              ("paradata", "vehiclePositions", "slots_expected")),
        "20-second fetch slots that worked over slots due (time-weighted)",
    ),
    "off_shape_share": (
        RATIO,
        _pair(("events", "shape_fit", "off_shape"), ("events", "shape_fit", "tracked_reports")),
        "positions more than 100 m off the route over positions tracked",
    ),
    "feed_agreement_share": (
        RATIO,
        lambda q, o: _feed_agreement(o),
        "departures that agree with the feed's stop sequence over those compared",
    ),
    "impossible_jumps": (
        SUM,
        _value("measurement", "impossible_jumps_over_130_kmh"),
        "consecutive positions implying more than 130 km/h",
    ),
    "glitch_reports_removed": (
        SUM,
        _value("measurement", "glitches", "removed_reports"),
        "positions left out as GPS glitches",
    ),
    "position_age_s_p95": (
        MEDIAN,
        _value("measurement", "position_age_s_p95"),
        "95th percentile of position age, seconds",
    ),
}  # fmt: skip


def _untimed_share(reason: str) -> Callable[..., Any]:
    """Since periods/0.3.0: scheduled timepoint departures untimed for one
    reason, over scheduled timepoint departures (from the on-time day file)."""

    def pair(q: dict[str, Any], o: dict[str, Any] | None) -> tuple[int, int] | None:
        if o is None or "untimed" not in o:
            return None
        return o["untimed"]["by_reason"].get(reason, 0), o["scheduled"]

    return pair


for _r in UNTIMED_REASONS:
    QUALITY_METRICS[f"untimed_{_r}"] = (
        RATIO,
        _untimed_share(_r),
        f"timepoint departures with no time because {REASON_NAMES[_r]}, over scheduled ones",
    )
HOURLY = {"trip_coverage", "timepoint_coverage"}


def _pool(how: str, values: list[Any]) -> dict[str, Any]:
    if how == RATIO:
        pairs = [v for v in values if v and v[0] is not None and v[1] is not None]
        num, den = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
        return {"value": share(num, den), "numerator": num, "denominator": den, "days": len(pairs)}
    present = [v for v in values if v is not None]
    if how == SUM:
        return {"value": sum(present) if present else None, "days": len(present)}
    return {"value": _rounded(median(present)), "low": min(present, default=None),
            "high": max(present, default=None), "days": len(present)}  # fmt: skip


def _hourly(metric: str, pairs: list[tuple[dict, dict | None]]) -> list[dict[str, Any]]:
    """Hour of day -> the figure pooled over the given days."""
    num: dict[int, int] = defaultdict(int)
    den: dict[int, int] = defaultdict(int)
    for q, o in pairs:
        if metric == "trip_coverage":
            trips = _get(q, "coverage", "trips") or {}
            cols = trips.get("columns", [])
            if "start_hour" not in cols:
                continue
            h, ob = cols.index("start_hour"), cols.index("observed")
            for r in trips.get("rows", []):
                den[r[h]] += 1
                num[r[h]] += bool(r[ob])
        elif o is not None:
            for hour, b in o.get("by_hour", {}).items():
                den[int(hour)] += b["scheduled"]
                num[int(hour)] += b["observed"]
    return [{"hour": h, "value": share(num[h], den[h]), "numerator": num[h], "denominator": den[h]}
            for h in sorted(den)]  # fmt: skip


def quality_periods(quality: list[dict[str, Any]], ontime: dict[str, dict[str, Any]]) -> dict:
    pairs = [(q, ontime.get(q["service_date"])) for q in quality]
    service = [(q, o) for q, o in pairs if (q.get("schedule") or {}).get("service_expected", True)]
    window = service[-WINDOW:]
    first = date.fromisoformat(quality[0]["service_date"]) if quality else None
    last = date.fromisoformat(quality[-1]["service_date"]) if quality else None

    def pooled(group: list[tuple[dict, dict | None]]) -> dict[str, Any]:
        return {m: _pool(how, [f(q, o) for q, o in group])
                for m, (how, f, _) in QUALITY_METRICS.items()}  # fmt: skip

    def key(q: dict, grain: str) -> str:
        day = date.fromisoformat(q["service_date"])
        return week_of(day).isoformat() if grain == "week" else month_of(day)

    grains: dict[str, Any] = {
        "hour": {
            "pooled": {
                "first_service_date": window[0][0]["service_date"] if window else None,
                "last_service_date": window[-1][0]["service_date"] if window else None,
                "metrics": {m: _hourly(m, window) for m in sorted(HOURLY)},
            },
            "latest_day": {
                "service_date": service[-1][0]["service_date"] if service else None,
                "metrics": {m: _hourly(m, service[-1:]) for m in sorted(HOURLY)},
            },
            "not_measured_by_hour": sorted(set(QUALITY_METRICS) - HOURLY),
        },
        "day": [{"period": q["service_date"],
                 "service_expected": (q.get("schedule") or {}).get("service_expected", True),
                 "metrics": pooled([(q, o)])} for q, o in pairs],
    }  # fmt: skip
    for grain in ("week", "month"):
        groups: dict[str, list] = defaultdict(list)
        for q, o in service:
            groups[key(q, grain)].append((q, o))
        grains[grain] = [
            {"period": k, "partial": _partial(k, grain, first, last), "days": len(g),
             "metrics": pooled(g)}
            for k, g in sorted(groups.items())
        ] if first and last else []  # fmt: skip
    return {
        "periods_version": PERIODS_VERSION,
        "metrics": {m: {"pooled_as": how, "definition": text}
                    for m, (how, _, text) in QUALITY_METRICS.items()},
        "grains": grains,
    }  # fmt: skip


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")


def write(data: Path) -> dict[str, int]:
    ontime_days = DayFiles(data / "ontime" / "days").all()
    quality_days = DayFiles(data / "quality" / "days").all()
    if ontime_days:
        raw = (load_json(data / "manifest" / "raw.json") or {}).get("days", {})
        head = headline(ontime_days, raw)
        if head is not None:
            _dump(data / "ontime" / "headline.json", head)
        high = highlights(ontime_days)
        if high is not None:
            _dump(data / "ontime" / "highlights.json", high)
        _dump(data / "ontime" / "periods.json", ontime_periods(ontime_days))
    if quality_days:
        by_date = {d["service_date"]: d for d in ontime_days}
        _dump(data / "quality" / "periods.json", quality_periods(quality_days, by_date))
    return {"ontime_days": len(ontime_days), "quality_days": len(quality_days)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the period files.")
    ap.add_argument("--data", type=Path, default=Path("data-branch"))
    args = ap.parse_args(argv)
    n = write(args.data)
    print(f"periods: {n['ontime_days']} on-time days, {n['quality_days']} quality days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
