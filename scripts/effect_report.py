"""What a change did to the published figures, as a table for docs/decisions.md.

    python scripts/effect_report.py <baseline> <changed> [--out report.md]

Both folders are replays written by ``scripts/rehearse_nightly.py --replay``
with the same ``--now``: ``data-branch/`` and ``releases/<tag>/``. The report
gives, before and after:

1. the headline figures of ``ontime/recent.json``;
2. each day's on-time and quality figures that moved;
3. how often a stop's moment was observed, by first, middle and last stop,
   and the count of each status (from the stop-event files);
4. each route's on-time and observed share, largest change first.

Paste its output into the row of ``docs/decisions.md`` for any change to
figures (CLAUDE.md, "A change to how a number is computed").
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

HEADLINE = (
    "scheduled",
    "observed",
    "observed_share",
    "on_time",
    "early",
    "late",
    "on_time_share_of_observed",
    "deviation_s_median",
)
QUALITY = (
    "trip_coverage",
    "timepoint_coverage",
    "stop_coverage",
    "off_shape_share",
    "impossible_jumps",
    "vehicles_seen",
)
ONTIME = (
    "scheduled",
    "observed",
    "observed_share",
    "on_time_share_of_observed",
    "feed_agreement_share",
)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["service_date"]: r for r in csv.DictReader(fh)}


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _change(a: Any, b: Any) -> str:
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return ""
    d = y - x
    if d == 0:
        return "0"
    return f"{d:+.4f}".rstrip("0").rstrip(".") if abs(d) < 10 else f"{d:+.1f}"


def _fmt(x: Any) -> str:
    return "" if x is None else str(x)


def table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_fmt(c) for c in r) + " |" for r in rows]
    return out


def positions(root: Path) -> tuple[Counter, Counter]:
    """(observed and scheduled count by position, count of each status by
    position) over every stop-event file: the measured moment of each stop
    (departure; arrival at the last stop)."""
    seen: Counter = Counter()
    status: Counter = Counter()
    for path in sorted((root / "releases").glob("*/stop-events-*.parquet")):
        cols = [
            "is_first_stop",
            "is_last_stop",
            "is_timepoint",
            "arrival_status",
            "departure_status",
        ]
        for e in pq.read_table(path, columns=cols).to_pylist():
            pos = "first" if e["is_first_stop"] else "last" if e["is_last_stop"] else "middle"
            s = e["arrival_status"] if e["is_last_stop"] else e["departure_status"]
            for p in (pos, f"{pos}, timepoints") if e["is_timepoint"] else (pos,):
                seen[(p, "scheduled")] += 1
                seen[(p, "observed")] += s == "observed"
            status[(pos, s)] += 1
    return seen, status


def report(base: Path, new: Path) -> str:
    lines = [f"Effect report: {base.name} -> {new.name}", ""]
    a = _json(base / "data-branch" / "ontime" / "recent.json") or {}
    b = _json(new / "data-branch" / "ontime" / "recent.json") or {}
    ao, bo = a.get("overall", {}), b.get("overall", {})
    lines += [f"Headline ({b.get('first_service_date')} to {b.get('last_service_date')}, "
              f"{b.get('days')} days)", ""]  # fmt: skip
    lines += table(
        ["figure", "before", "after", "change"],
        [[k, ao.get(k), bo.get(k), _change(ao.get(k), bo.get(k))] for k in HEADLINE],
    )

    rows = []
    for name, cols in (("ontime", ONTIME), ("quality", QUALITY)):
        ra = _rows(base / "data-branch" / name / "daily.csv")
        rb = _rows(new / "data-branch" / name / "daily.csv")
        for day in sorted(ra.keys() | rb.keys()):
            for c in cols:
                x, y = ra.get(day, {}).get(c), rb.get(day, {}).get(c)
                if x != y:
                    rows.append([day, f"{name} {c}", x, y, _change(x, y)])
    lines += ["", "Daily figures that moved", ""]
    lines += table(["day", "figure", "before", "after", "change"], rows) if rows else ["none"]

    sa, ta = positions(base)
    sb, tb = positions(new)
    places = sorted({p for p, _ in sa | sb}, key=lambda p: (p.split(",")[0] != "first",
                    p.split(",")[0] != "middle", p))  # fmt: skip
    pos_rows = []
    for p in places:
        share_a = sa[(p, "observed")] / sa[(p, "scheduled")] if sa[(p, "scheduled")] else None
        share_b = sb[(p, "observed")] / sb[(p, "scheduled")] if sb[(p, "scheduled")] else None
        pos_rows.append(
            [p, f"{sa[(p, 'observed')]} of {sa[(p, 'scheduled')]}",
             f"{sb[(p, 'observed')]} of {sb[(p, 'scheduled')]}",
             _change(share_a, share_b)]
        )  # fmt: skip
    lines += ["", "Observed moments by stop position (all days)", ""]
    lines += table(["stops", "before", "after", "change in share"], pos_rows)
    st_rows = [
        [pos, s, ta[(pos, s)], tb[(pos, s)], tb[(pos, s)] - ta[(pos, s)]]
        for pos, s in sorted(ta.keys() | tb.keys())
        if ta[(pos, s)] != tb[(pos, s)] or s != "not_applicable"
    ]
    lines += ["", "Status of the measured moment (all days)", ""]
    lines += table(["stops", "status", "before", "after", "change"], st_rows)

    ar, br = a.get("routes", {}), b.get("routes", {})
    route_rows = []
    for r in sorted(ar.keys() | br.keys()):
        x, y = ar.get(r, {}), br.get(r, {})
        d_on = _num(_change(x.get("on_time_share_of_observed"), y.get("on_time_share_of_observed")))
        route_rows.append(
            (abs(d_on or 0), [y.get("name") or x.get("name") or r,
                              x.get("on_time_share_of_observed"),
                              y.get("on_time_share_of_observed"),
                              x.get("observed_share"), y.get("observed_share")])
        )  # fmt: skip
    route_rows.sort(key=lambda t: -t[0])
    lines += ["", "Routes (largest change in on-time share first)", ""]
    lines += table(
        ["route", "on time before", "on time after", "observed before", "observed after"],
        [r for _, r in route_rows],
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Before and after figures of two replays.")
    ap.add_argument("baseline", type=Path)
    ap.add_argument("changed", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    text = report(args.baseline, args.changed)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
