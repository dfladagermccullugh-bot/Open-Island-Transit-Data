"""Per-route on-time tables for the site, built from ``ontime/days/*.json``.

- ``ontime/routes.csv``: one row per service day and route.
- ``ontime/recent.json``: the last ``WINDOW_DAYS`` calendar days pooled, by
  route and by route and timepoint stop. Counts are summed; shares are
  recomputed from the sums; medians come from every deviation in the window
  (kept in ``by_route_stop`` since ``ontime/0.2.0``). Days made by an older
  version add to the counts but not to the medians or the stop breakdown.
  Days on which Island Transit runs no service (``service_expected`` false,
  see ``pipeline/gtfs/holidays.py``) are left out and listed.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pipeline.stats import quantile

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


def _share(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


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


def write_routes(ontime_dir: Path, window_days: int = WINDOW_DAYS) -> int:
    days = [json.loads(p.read_text()) for p in sorted((ontime_dir / "days").glob("*.json"))]
    if not days:
        return 0
    names: dict[str, dict[str, str]] = {}
    for d in days:
        names.update(d.get("routes", {}))

    with (ontime_dir / "routes.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(ROUTE_COLUMNS)
        for d in days:
            for route, b in sorted(d["by_route"].items()):
                w.writerow(
                    [d["service_date"], route, route_name(names, route)]
                    + [b.get(k) for k in ROUTE_COLUMNS[3:]]
                )

    last = date.fromisoformat(days[-1]["service_date"])
    first = last - timedelta(days=window_days - 1)
    in_window = [d for d in days if date.fromisoformat(d["service_date"]) >= first]
    window = [d for d in in_window if d.get("service_expected", True)]
    left_out = [d["service_date"] for d in in_window if not d.get("service_expected", True)]
    if not window:
        return len(days)

    def zero() -> dict[str, int]:
        return dict.fromkeys(COUNTS, 0)

    total, total_devs = zero(), []
    route_counts: dict[str, dict[str, int]] = defaultdict(zero)
    route_devs: dict[str, list[int]] = defaultdict(list)
    stop_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(zero)
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

    routes_out = {}
    for route, counts in route_counts.items():
        stops = [
            {"stop_id": stop, **stop_info[(r, stop)], **_summary(c, stop_devs[(r, stop)])}
            for (r, stop), c in stop_counts.items()
            if r == route
        ]
        n = names.get(route, {})
        routes_out[route] = {
            "name": route_name(names, route),
            "short_name": n.get("short_name", ""),
            "long_name": n.get("long_name", ""),
            **_summary(counts, route_devs[route]),
            "daily": daily[route],
            "stops": sorted(stops, key=lambda s: (s["order"], s["name"])),
        }
    recent = {
        "window_days": window_days,
        "first_service_date": window[0]["service_date"],
        "last_service_date": window[-1]["service_date"],
        "days": len(window),
        "days_without_service": left_out,
        "definition": window[-1].get("definition"),
        "timepoints": window[-1].get("timepoints"),
        "passio_version": window[-1].get("passio_version"),
        "ontime_versions": sorted({d.get("ontime_version", "") for d in window}),
        "overall": _summary(total, total_devs),
        "routes": dict(sorted(routes_out.items())),
    }
    (ontime_dir / "recent.json").write_text(json.dumps(recent, indent=1) + "\n")
    return len(days)
