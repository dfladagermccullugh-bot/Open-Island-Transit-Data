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

Counts are summed and shares recomputed from the sums; medians come from
every pooled deviation. Days without service (holidays) appear in the day
view, marked, and are left out of everything that pools days. Standard
library only, like the publishing gate that checks these files (I8).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from pipeline.common import DayFiles
from pipeline.stats import median, share

PERIODS_VERSION = "periods/0.1.0"
WINDOW = 28  # service days
MIN_PREVIOUS = 14  # service days before a "previous window" is shown
COUNTS = ("scheduled", "observed", "on_time", "early", "late")
GRAINS = ("hour", "day", "week", "month")

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


def headline(days: list[dict[str, Any]]) -> dict[str, Any] | None:
    service = [d for d in days if _service(d)]
    if not service:
        return None
    window, before = service[-WINDOW:], service[:-WINDOW][-WINDOW:]
    now = ontime_block(window, _day_devs)
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
        head = headline(ontime_days)
        if head is not None:
            _dump(data / "ontime" / "headline.json", head)
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
