"""Per-route on-time tables for the site, built from ``ontime/days/*.json``.

- ``ontime/routes.csv``: one row per service day and route.
- ``ontime/recent.json``: the last ``WINDOW_DAYS`` calendar days pooled, by
  route and by route and timepoint stop. Counts are summed; shares are
  recomputed from the sums; medians come from every deviation in the window
  (kept in ``by_route_stop`` since ``ontime/0.2.0``). Days made by an older
  version add to the counts but not to the medians or the stop breakdown.
  Days on which Island Transit runs no service (``service_expected`` false,
  see ``pipeline/gtfs/holidays.py``) are left out and listed.
  Since ``routes/0.2.0`` the window holds only days measured against the
  printed timetable in effect on its last day, so after a schedule change
  the pooled figures start over (``schedule_change`` says so) and stops and
  times of the old timetable are not mixed with the new one.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from pipeline.common import DayFiles, write_csv
from pipeline.gtfs.timepoints import reference_of
from pipeline.stats import quantile, share

ROUTES_VERSION = "routes/0.2.0"
WINDOW_DAYS = 28
COUNTS = ("scheduled", "observed", "on_time", "early", "late")
ROUTE_COLUMNS = [
    "service_date",
    "route_id",
    "route_name",
    *COUNTS,
    "observed_share",
    "on_time_share_of_observed",
    "deviation_s_median",
]


# Shares in the on-time files are published to four decimal places.
_share = partial(share, digits=4)


def _quantile(values: list[int], q: float) -> float | None:
    x = quantile(values, q)
    return round(x, 1) if x is not None else None


def _summary(counts: dict[str, int], devs: list[int]) -> dict[str, Any]:
    return {
        **counts,
        "observed_share": _share(counts["observed"], counts["scheduled"]),
        "on_time_share_of_observed": _share(counts["on_time"], counts["observed"]),
        "early_share_of_observed": _share(counts["early"], counts["observed"]),
        "late_share_of_observed": _share(counts["late"], counts["observed"]),
        "deviation_s_median": _quantile(devs, 0.5),
        "deviation_s_p25": _quantile(devs, 0.25),
        "deviation_s_p75": _quantile(devs, 0.75),
        "deviations_counted": len(devs),
    }


def route_name(names: dict[str, dict[str, str]], route_id: str) -> str:
    n = names.get(route_id, {})
    return n.get("short_name") or n.get("long_name") or route_id


def all_names(days: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Route id -> names, each route's latest names winning."""
    names: dict[str, dict[str, str]] = {}
    for d in days:
        names.update(d.get("routes", {}))
    return names


def write_routes_csv(
    path: Path, days: list[dict[str, Any]], names: dict[str, dict[str, str]]
) -> None:
    """One row per service day and route, for every day there is."""
    write_csv(
        path,
        ROUTE_COLUMNS,
        (
            [d["service_date"], route, route_name(names, route)]
            + [b.get(k) for k in ROUTE_COLUMNS[3:]]
            for d in days
            for route, b in sorted(d["by_route"].items())
        ),
    )


@dataclass(frozen=True)
class Window:
    """The days the route tables pool, and the days left out and why."""

    days: list[dict[str, Any]]  # pooled: in the window, on the timetable, with service
    without_service: list[str]  # dates in the window with no service (holidays)
    other_timetable: list[dict[str, Any]]  # in the window but measured against an older timetable
    timetable: str | None  # the printed timetable in effect on the last day


def window_of(days: list[dict[str, Any]], window_days: int) -> Window:
    """The last ``window_days`` calendar days up to the latest day, keeping
    only days measured against the latest day's printed timetable."""
    last = date.fromisoformat(days[-1]["service_date"])
    first = last - timedelta(days=window_days - 1)
    in_window = [d for d in days if date.fromisoformat(d["service_date"]) >= first]
    timetable = reference_of(days[-1].get("timepoints"))
    other = [d for d in in_window if reference_of(d.get("timepoints")) != timetable]
    in_window = [d for d in in_window if reference_of(d.get("timepoints")) == timetable]
    return Window(
        days=[d for d in in_window if d.get("service_expected", True)],
        without_service=[
            d["service_date"] for d in in_window if not d.get("service_expected", True)
        ],
        other_timetable=other,
        timetable=timetable,
    )


def _zero() -> dict[str, int]:
    return dict.fromkeys(COUNTS, 0)


def pool(
    window: list[dict[str, Any]], names: dict[str, dict[str, str]]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Counts summed and deviations gathered over the window: the summary of
    everything, and one of each route with its days and its timepoint stops."""
    total, total_devs = _zero(), []
    route_counts: dict[str, dict[str, int]] = defaultdict(_zero)
    route_devs: dict[str, list[int]] = defaultdict(list)
    stop_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(_zero)
    stop_devs: dict[tuple[str, str], list[int]] = defaultdict(list)
    stop_info: dict[tuple[str, str], dict[str, Any]] = {}
    daily: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in window:
        for k in COUNTS:
            total[k] += d[k]
        for route, b in d["by_route"].items():
            for k in COUNTS:
                route_counts[route][k] += b[k]
            daily[route].append(
                {
                    "service_date": d["service_date"],
                    "observed_share": b["observed_share"],
                    "on_time_share_of_observed": b["on_time_share_of_observed"],
                    "deviation_s_median": b["deviation_s_median"],
                }
            )
        for route, stops in d.get("by_route_stop", {}).items():
            for stop, b in stops.items():
                key = (route, stop)
                for k in COUNTS:
                    stop_counts[key][k] += b[k]
                stop_devs[key] += b["deviations_s"]
                route_devs[route] += b["deviations_s"]
                total_devs += b["deviations_s"]
                stop_info[key] = {"name": b["name"], "order": b["stop_sequence_median"]}

    # Each route's stops, gathered in one pass over the stops.
    stops_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (route, stop), c in stop_counts.items():
        stops_of[route].append(
            {"stop_id": stop, **stop_info[(route, stop)], **_summary(c, stop_devs[(route, stop)])}
        )
    routes = {}
    for route, counts in route_counts.items():
        n = names.get(route, {})
        routes[route] = {
            "name": route_name(names, route),
            "short_name": n.get("short_name", ""),
            "long_name": n.get("long_name", ""),
            **_summary(counts, route_devs[route]),
            "daily": daily[route],
            "stops": sorted(stops_of[route], key=lambda s: (s["order"], s["name"])),
        }
    return _summary(total, total_devs), dict(sorted(routes.items()))


def recent(
    days: list[dict[str, Any]], names: dict[str, dict[str, str]], window_days: int = WINDOW_DAYS
) -> dict[str, Any] | None:
    """The contents of ``recent.json``; None when no day in the window has service."""
    w = window_of(days, window_days)
    if not w.days:
        return None
    overall, routes = pool(w.days, names)
    change = None
    if w.other_timetable:
        change = {
            "effective": w.timetable,
            "days_left_out": len(w.other_timetable),
            "last_day_left_out": w.other_timetable[-1]["service_date"],
        }
    return {
        "routes_version": ROUTES_VERSION,
        "window_days": window_days,
        "first_service_date": w.days[0]["service_date"],
        "last_service_date": w.days[-1]["service_date"],
        "days": len(w.days),
        "days_without_service": w.without_service,
        "timetable_reference": w.timetable,
        "schedule_change": change,
        "definition": w.days[-1].get("definition"),
        "timepoints": w.days[-1].get("timepoints"),
        "passio_version": w.days[-1].get("passio_version"),
        "ontime_versions": sorted({d.get("ontime_version", "") for d in w.days}),
        "overall": overall,
        "routes": routes,
    }


def write_routes(
    ontime_dir: Path, window_days: int = WINDOW_DAYS, files: DayFiles | None = None
) -> int:
    """Write ``routes.csv`` and ``recent.json``; return the number of days read."""
    days = (files or DayFiles(ontime_dir / "days")).all()
    if not days:
        return 0
    names = all_names(days)
    write_routes_csv(ontime_dir / "routes.csv", days, names)
    pooled = recent(days, names, window_days)
    if pooled is not None:
        (ontime_dir / "recent.json").write_text(json.dumps(pooled, indent=1) + "\n")
    return len(days)
