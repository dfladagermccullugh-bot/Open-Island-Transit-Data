"""Monthly report and release checksums for every closed month.

    python -m pipeline.monthly --data data-branch [--release]
    python -m pipeline.monthly --data data-branch --month 2026-11 --preview DIR

A month is closed once its last day has been archived and has its quality
file, or seven days after the month ended (then the days never archived are
listed as missing). For each closed month the report is built from the
manifests, ``quality/days/``, ``quality/daily.csv`` and ``ontime/days/`` on
the data branch and written to ``reports/YYYY-MM.json`` and
``reports/YYYY-MM.md``; ``reports/index.json`` lists every month. A report is
rebuilt only when its inputs change (a rebuilt day, a new report version).

With ``--release``, each rebuilt month also gets ``SHA256SUMS-<tag>.txt`` in
each of its Releases (``raw-YYYY-MM``, ``parsed-YYYY-MM``), listing every
file the manifests record there, and the Markdown report becomes the
release notes. ``--preview`` builds one month, closed or not, into DIR and
publishes nothing.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pipeline import release
from pipeline.common import asset_of, code_sha, load_json, stamp, utcnow
from pipeline.config import PACIFIC, REPO
from pipeline.stats import share

REPORT_VERSION = "monthly/0.2.0"
GRACE_DAYS = 7
FEEDS = ("vehiclePositions", "tripUpdates", "serviceAlerts")
ONTIME_COUNTS = ("scheduled", "observed", "on_time", "early", "late")
TAG_TITLES = {"raw": "Raw archive", "parsed": "Parsed tables"}


def _load(path: Path) -> Any:
    return load_json(path)


def _manifests(data: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for name in ("raw", "parsed", "events"):
        m = _load(data / "manifest" / f"{name}.json")
        out[name] = m["days"] if m else {}
    return out


def month_days(month: str) -> list[date]:
    y, m = (int(x) for x in month.split("-"))
    return [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]


def closed_months(data: Path, today: date) -> list[str]:
    """Months with archived days that are complete and fully processed."""
    raw = _manifests(data)["raw"]
    months = sorted({d[:7] for d in raw})
    out = []
    for month in months:
        days = month_days(month)
        last = days[-1]
        if today <= last:
            continue
        archived = [d for d in raw if d.startswith(month)]
        processed = all((data / "quality" / "days" / f"{d}.json").exists() for d in archived)
        if not processed:
            continue
        if last.isoformat() in raw or today >= last + timedelta(days=GRACE_DAYS):
            out.append(month)
    return out


def _range(values: list[tuple[str, float]]) -> dict[str, Any] | None:
    if not values:
        return None
    lo = min(values, key=lambda v: v[1])
    hi = max(values, key=lambda v: v[1])
    return {
        "days": len(values),
        "min": lo[1],
        "min_date": lo[0],
        # statistics.median on purpose; see the note in pipeline/quality/stops.py.
        "median": round(statistics.median(v for _, v in values), 6),
        "max": hi[1],
        "max_date": hi[0],
    }


def _daily_rows(data: Path, month: str) -> list[dict[str, str]]:
    path = data / "quality" / "daily.csv"
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [r for r in csv.DictReader(fh) if r["service_date"].startswith(month)]


def _number(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _route_key(r: dict[str, Any], route_id: str) -> tuple[int, int, str]:
    """Routes in timetable order: 1, 1C, 2, ..., 10, 58, 411C; unnamed last."""
    name = r.get("short_name") or ""
    digits = "".join(ch for ch in name if ch.isdigit())
    return (0 if digits else 1, int(digits) if digits else 0, name or route_id)


# When a file was written and from which commit. A day rebuilt by newer code
# carries new values of these even when every figure in it is the same.
STAMPS = frozenset({"computed_at", "built_at", "archived_at", "code_sha"})


def _without_stamps(value: Any) -> Any:
    """``value`` with every stamp removed, at any depth."""
    if isinstance(value, dict):
        return {k: _without_stamps(v) for k, v in value.items() if k not in STAMPS}
    if isinstance(value, list):
        return [_without_stamps(v) for v in value]
    return value


def inputs_digest(data: Path, month: str) -> str:
    """Changes whenever anything the report reads for ``month`` changes.

    The stamps on the inputs (``STAMPS``) are left out. A month's report is
    rebuilt, its checksum files uploaded again and its release notes reset
    only when a figure, a file or a version behind it changed, not each time
    a day is rebuilt to the same result."""
    h = hashlib.sha256(REPORT_VERSION.encode())

    def take(value: Any) -> None:
        h.update(json.dumps(_without_stamps(value), sort_keys=True).encode())

    for name, days in _manifests(data).items():
        for day in sorted(d for d in days if d.startswith(month)):
            h.update(f"{name}:{day}:".encode())
            take(days[day])
    for folder in ("quality/days", "ontime/days"):
        for path in sorted((data / folder).glob(f"{month}-*.json")):
            h.update(path.name.encode())
            take(json.loads(path.read_text()))
    for row in _daily_rows(data, month):
        take(row)
    return h.hexdigest()


def checksums(data: Path, month: str) -> dict[str, list[dict[str, Any]]]:
    """Release tag -> every file the manifests record in it for ``month``."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for days in _manifests(data).values():
        for day, entry in days.items():
            tag = entry.get("release_tag")
            if not day.startswith(month) or not tag:
                continue
            for f in entry["files"]:
                out.setdefault(tag, {})[f["name"]] = f
    return {tag: [files[n] for n in sorted(files)] for tag, files in sorted(out.items())}


def sums_text(files: list[dict[str, Any]]) -> str:
    return "".join(f"{f['sha256']}  {asset_of(f)}\n" for f in files)


def _collection(all_quality: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    """For each feed, the fetch slots expected and successful over every
    archived day of the month, holidays included (the collector runs on
    them too), and the collector versions that made them."""
    collection = {}
    for feed in FEEDS:
        exp = sum(q["paradata"][feed]["slots_expected"] for q in all_quality.values())
        ok = sum(q["paradata"][feed]["slots_ok"] for q in all_quality.values())
        att = sum(q["nonresponse"]["fetch"][feed]["attempted"] for q in all_quality.values())
        fail = sum(q["nonresponse"]["fetch"][feed]["failed"] for q in all_quality.values())
        collection[feed] = {
            "slots_expected": exp,
            "slots_ok": ok,
            "uptime": share(ok, exp),
            "attempted": att,
            "failed": fail,
            "failure_rate": share(fail, att),
            "uptime_by_day": _range(
                [(d, q["paradata"][feed]["uptime"]) for d, q in sorted(all_quality.items())]
            ),
        }
    versions: set[str] = set()
    for q in all_quality.values():
        for feed in FEEDS:
            versions.update(q["paradata"][feed].get("collector_versions", []))
    return collection, versions


def _coverage(quality: dict[str, Any]) -> dict[str, Any]:
    """Scheduled trips and the trips seen, summed over the days with service."""
    sched = sum(q["coverage"]["scheduled_trips"] for q in quality.values())
    seen = sum(q["coverage"]["observed_scheduled_trips"] for q in quality.values())
    never = sorted({r for q in quality.values() for r in q["coverage"]["routes_never_reporting"]})
    coverage = {
        "scheduled_trips": sched,
        "observed_scheduled_trips": seen,
        "trip_coverage": share(seen, sched),
        "trip_coverage_by_day": _range(
            [
                (d, q["coverage"]["trip_coverage"])
                for d, q in sorted(quality.items())
                if q["coverage"]["trip_coverage"] is not None
            ]
        ),
        "routes_never_reporting_on_some_day": never,
    }
    return coverage


def _ontime(ontime: dict[str, Any]) -> dict[str, Any]:
    """Counts of timepoint events summed over the days with service, the
    shares recomputed from those sums, overall and by route."""
    totals = {k: sum(o[k] for o in ontime.values()) for k in ONTIME_COUNTS}
    routes: dict[str, dict[str, Any]] = {}
    names: dict[str, dict[str, str]] = {}
    for _, o in sorted(ontime.items()):
        names.update(o.get("routes", {}))
        for route, r in o["by_route"].items():
            acc = routes.setdefault(route, dict.fromkeys(ONTIME_COUNTS, 0))
            for k in ONTIME_COUNTS:
                acc[k] += r[k]
    by_route = {}
    for route, acc in routes.items():
        by_route[route] = {
            # Empty, not missing, for a route without a number or a name, as
            # in the on-time files.
            "short_name": names.get(route, {}).get("short_name") or "",
            "long_name": names.get(route, {}).get("long_name") or "",
            **acc,
            "observed_share": share(acc["observed"], acc["scheduled"]),
            "on_time_share_of_observed": share(acc["on_time"], acc["observed"]),
        }
    definition = next(iter(ontime.values()))["definition"] if ontime else None
    ontime_out = {
        "definition": definition,
        **totals,
        "observed_share": share(totals["observed"], totals["scheduled"]),
        "on_time_share_of_observed": share(totals["on_time"], totals["observed"]),
        "early_share_of_observed": share(totals["early"], totals["observed"]),
        "late_share_of_observed": share(totals["late"], totals["observed"]),
        "on_time_share_by_day": _range(
            [
                (d, o["on_time_share_of_observed"])
                for d, o in sorted(ontime.items())
                if o["on_time_share_of_observed"] is not None
            ]
        ),
        "deviation_s_median_by_day": _range(
            [
                (d, o["deviation_s_median"])
                for d, o in sorted(ontime.items())
                if o["deviation_s_median"] is not None
            ]
        ),
        "by_route": dict(sorted(by_route.items(), key=lambda kv: _route_key(kv[1], kv[0]))),
    }
    return ontime_out


def _headline(rows: list[dict[str, str]]) -> dict[str, Any]:
    """The lowest, middle and highest value over the month of every numeric
    column of ``quality/daily.csv``."""
    headline = {}
    for col in rows[0].keys() if rows else []:
        numbers = [(r["service_date"], _number(r[col])) for r in rows]
        values = [(d, v) for d, v in numbers if v is not None]
        if col != "service_date" and values:
            headline[col] = _range(values)
    return headline


def _releases(data: Path, month: str) -> dict[str, Any]:
    """The files in each of the month's Releases and its checksum file."""
    releases = {}
    for tag, files in checksums(data, month).items():
        text = sums_text(files)
        releases[tag] = {
            "files": len(files),
            "bytes": sum(f["bytes"] for f in files),
            "checksums_file": f"SHA256SUMS-{tag}.txt",
            "checksums_bytes": len(text.encode()),
            "checksums_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
    return releases


def build(data: Path, month: str, today: date | None = None) -> dict[str, Any]:
    manifests = _manifests(data)
    raw = manifests["raw"]
    days = month_days(month)
    archived = sorted(d for d in raw if d.startswith(month))
    first_collected = min(raw) if raw else None
    expected = [
        d.isoformat()
        for d in days
        if first_collected and d.isoformat() >= first_collected and (today is None or d < today)
    ]
    quality = {d: _load(data / "quality" / "days" / f"{d}.json") for d in archived}
    quality = {d: q for d, q in quality.items() if q}
    ontime = {d: _load(data / "ontime" / "days" / f"{d}.json") for d in archived}
    ontime = {d: o for d, o in ontime.items() if o}

    # Days with no service stay in the collection figures (the collector ran)
    # but not in coverage or on-time figures, where they would read as a day
    # of lost buses.
    no_service = sorted(
        d for d, q in quality.items() if not (q.get("schedule") or {}).get("service_expected", True)
    )
    all_quality = quality
    quality = {d: q for d, q in quality.items() if d not in no_service}
    ontime_all = ontime
    ontime = {d: o for d, o in ontime.items() if d not in no_service}

    partial = [
        {
            "service_date": d,
            "observed_from": q["paradata"]["observed_from"],
            "scheduled_trips_ended_before_collection": q["scheduled_service"][
                "trips_ended_before_collection"
            ],
        }
        for d, q in sorted(quality.items())
        if q["scheduled_service"].get("trips_ended_before_collection")
    ]

    collection, versions = _collection(all_quality)
    coverage = _coverage(quality)
    ontime_out = _ontime(ontime)
    rows = [r for r in _daily_rows(data, month) if r["service_date"] not in no_service]
    headline = _headline(rows)
    releases = _releases(data, month)

    def _versions(source: dict[str, Any], key: str) -> list[str]:
        return sorted({v[key] for v in source.values() if v.get(key)})

    return {
        "report_version": REPORT_VERSION,
        "month": month,
        "computed_at": stamp(),
        "code_sha": code_sha(),
        "inputs_digest": inputs_digest(data, month),
        "period": {
            "days_in_month": len(days),
            "first_service_date": archived[0] if archived else None,
            "last_service_date": archived[-1] if archived else None,
            "days_archived": len(archived),
            "days_missing": [d for d in expected if d not in raw],
            "days_without_quality": [d for d in archived if d not in all_quality],
            "days_without_ontime": [d for d in archived if d not in ontime_all],
            "days_without_service": no_service,
            "partial_days": partial,
        },
        "collection": collection,
        "coverage": coverage,
        "ontime": ontime_out,
        "daily_headline": headline,
        "releases": releases,
        "versions": {
            "collector": sorted(versions),
            "tables": _versions(manifests["parsed"], "tables_version"),
            "events": _versions(manifests["events"], "events_version"),
            "quality": _versions(all_quality, "quality_version"),
            "ontime": _versions(ontime_all, "ontime_version"),
        },
    }


def _pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def _pacific(ts: str) -> str:
    t = datetime.fromisoformat(ts).astimezone(PACIFIC)
    return t.strftime("%-I:%M %p Pacific")


def _long(day: str) -> str:
    """ "November 20, 2026" from "2026-11-20"."""
    d = date.fromisoformat(day)
    return f"{d:%B} {d.day}, {d.year}"


def markdown(r: dict[str, Any]) -> str:
    p, c, o = r["period"], r["coverage"], r["ontime"]
    title = datetime.strptime(r["month"], "%Y-%m").strftime("%B %Y")
    lines = [f"# Island Transit real-time feeds: {title}", ""]
    if p["first_service_date"]:
        lines.append(
            f"Service days {_long(p['first_service_date'])} to {_long(p['last_service_date'])}: "
            f"{p['days_archived']} archived of {p['days_in_month']} in the month."
        )
    lines.append(
        "Missing days: "
        + ("; ".join(map(_long, p["days_missing"])) if p["days_missing"] else "none")
        + "."
    )
    if p.get("days_without_service"):
        lines.append(
            "Days with no service (Island Transit holidays), left out of the trip coverage and "
            "on-time figures: " + "; ".join(map(_long, p["days_without_service"])) + "."
        )
    for d in p["partial_days"]:
        lines.append(
            f"Partial day: {_long(d['service_date'])}, collection from "
            f"{_pacific(d['observed_from'])} "
            f"({d['scheduled_trips_ended_before_collection']} scheduled trips ended before that)."
        )
    lines += ["", "## Collection", "", "| Feed | Fetches expected | Successful | Uptime | "
              "Lowest day |", "|---|---:|---:|---:|---|"]  # fmt: skip
    for feed, f in r["collection"].items():
        low = f["uptime_by_day"]
        low_txt = f"{_pct(low['min'], 2)} ({_long(low['min_date'])})" if low else "n/a"
        lines.append(
            f"| {feed} | {f['slots_expected']:,} | {f['slots_ok']:,} | "
            f"{_pct(f['uptime'], 2)} | {low_txt} |"
        )
    lines += [
        "",
        "## Coverage",
        "",
        f"Scheduled trips while collecting: {c['scheduled_trips']:,}. Seen in the vehicle "
        f"feed: {c['observed_scheduled_trips']:,} ({_pct(c['trip_coverage'])}).",
    ]
    if c["trip_coverage_by_day"]:
        t = c["trip_coverage_by_day"]
        lines.append(
            f"Daily range {_pct(t['min'])} ({_long(t['min_date'])}) to {_pct(t['max'])} "
            f"({_long(t['max_date'])})."
        )
    lines += ["", "## On time at printed timetable timepoints", ""]
    if o["definition"]:
        early, late = (-o["definition"]["window_s"][0] // 60, o["definition"]["window_s"][1] // 60)
        lines.append(f"On time means {early} minute early to {late} minutes late.")
    lines.append(
        f"Timepoint events scheduled: {o['scheduled']:,}. Observed: {o['observed']:,} "
        f"({_pct(o['observed_share'])}). Of those observed: on time "
        f"{_pct(o['on_time_share_of_observed'])}, early {_pct(o['early_share_of_observed'])}, "
        f"late {_pct(o['late_share_of_observed'])}."
    )
    if o["by_route"]:
        lines += ["", "| Route | Scheduled | Observed | On time (of observed) |",
                  "|---|---:|---:|---:|"]  # fmt: skip
        for route, x in o["by_route"].items():
            name = x["short_name"] or x["long_name"] or route
            lines.append(
                f"| {name} | {x['scheduled']:,} | {x['observed']:,} | "
                f"{_pct(x['on_time_share_of_observed'])} |"
            )
    lines += ["", "## Files", "", "| Release | Files | Size (MB) | Checksums |",
              "|---|---:|---:|---|"]  # fmt: skip
    for tag, x in r["releases"].items():
        lines.append(f"| {tag} | {x['files']} | {x['bytes'] / 1e6:.1f} | `{x['checksums_file']}` |")
    v = r["versions"]
    lines += [
        "",
        "## Versions",
        "",
        "Collector " + ", ".join(v["collector"]) + "; tables " + ", ".join(v["tables"])
        + "; stop events " + ", ".join(v["events"]) + "; quality " + ", ".join(v["quality"])
        + "; on-time " + ", ".join(v["ontime"]) + ".",
        "",
        f"Daily figures: `quality/daily.csv` and `ontime/daily.csv` on the `data` branch. "
        f"This report: `reports/{r['month']}.json`, built by `pipeline/monthly.py` "
        f"({r['report_version']}).",
        "",
    ]  # fmt: skip
    return "\n".join(lines)


def _index_entry(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "month": r["month"],
        "days_archived": r["period"]["days_archived"],
        "days_missing_count": len(r["period"]["days_missing"]),
        "vehicle_uptime": r["collection"]["vehiclePositions"]["uptime"],
        "trip_coverage": r["coverage"]["trip_coverage"],
        "timepoint_observed_share": r["ontime"]["observed_share"],
        "on_time_share_of_observed": r["ontime"]["on_time_share_of_observed"],
        "bytes": sum(x["bytes"] for x in r["releases"].values()),
        "computed_at": r["computed_at"],
    }


def write(out: Path, report: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    month = report["month"]
    (out / f"{month}.json").write_text(json.dumps(report, indent=1) + "\n")
    (out / f"{month}.md").write_text(markdown(report))
    index = [_index_entry(json.loads(p.read_text())) for p in sorted(out.glob("????-??.json"))]
    (out / "index.json").write_text(json.dumps(index, indent=1) + "\n")


def publish(data: Path, report: dict[str, Any], repo: str, work: Path) -> None:
    """Upload each release's checksum file and set its notes to the report."""
    work.mkdir(parents=True, exist_ok=True)
    month = report["month"]
    notes = markdown(report)
    for tag, files in checksums(data, month).items():
        entry = report["releases"][tag]
        path = work / entry["checksums_file"]
        path.write_text(sums_text(files))
        kind = tag.split("-")[0]
        title = f"{TAG_TITLES.get(kind, kind)}, {datetime.strptime(month, '%Y-%m'):%B %Y}"
        release.ensure_release(tag, repo, title=title, notes=notes)
        release.upload(
            tag,
            repo,
            work,
            [{"name": path.name, "bytes": entry["checksums_bytes"]}],
            content_names=False,
        )
        release.set_notes(tag, repo, notes)


def main() -> int:
    ap = argparse.ArgumentParser(description="Monthly reports and release checksums.")
    ap.add_argument("--data", type=Path, default=Path("data-branch"))
    ap.add_argument("--release", action="store_true", help="upload checksums, set notes")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    ap.add_argument("--work", type=Path, default=Path("work/monthly"))
    ap.add_argument("--today", type=date.fromisoformat, help="Pacific date (default: today)")
    ap.add_argument("--month", help="with --preview: build this month even if open")
    ap.add_argument("--preview", type=Path, help="write one month here; publish nothing")
    args = ap.parse_args()
    today = args.today or utcnow().astimezone(PACIFIC).date()

    if args.preview:
        if not args.month:
            ap.error("--preview needs --month")
        report = build(args.data, args.month, today)
        write(args.preview, report)
        print(f"preview {args.month}: {args.preview / (args.month + '.md')}")
        return 0

    failed = 0
    out = args.data / "reports"
    for month in closed_months(args.data, today):
        old = _load(out / f"{month}.json")
        if old and old.get("inputs_digest") == inputs_digest(args.data, month):
            continue
        report = build(args.data, month, today)
        try:
            if args.release:
                publish(args.data, report, args.repo, args.work)
        except Exception as exc:  # keep the old report so the next run retries
            print(f"{month}: release update failed: {exc}")
            failed = 1
            continue
        write(out, report)
        print(f"{month}: report written ({report['period']['days_archived']} days)")
    return failed


if __name__ == "__main__":
    sys.exit(main())
