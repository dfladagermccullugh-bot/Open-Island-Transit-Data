"""The feeds checked by the industry's own validators.

    python -m pipeline.feed_validation --data data-branch \\
        --static-jar gtfs-validator-cli.jar [--rt-jar gtfs-rt-validator-lib.jar] \\
        [--releases <dir>] [--work <dir>]

Two validators published by MobilityData, the nonprofit that keeps the GTFS
standards, run on what we archived:

- The GTFS Schedule validator (``gtfs-validator``, a pinned release) on each
  Passio schedule version in ``manifest/static.json`` that has no report yet,
  using the version's first day as the date for the time-based rules.
  Summary: ``validation/static/<version>.json``.
- The GTFS Realtime validator (``gtfs-realtime-validator``, built from a
  pinned commit) in its batch mode, once a week, on one archived hour of a
  weekday: 10 to 11 AM Pacific, every vehicle-positions and trip-updates
  response saved in that hour, each feed in order, against the Passio
  schedule pinned for that day. Summary: ``validation/realtime/<date>.json``.

A summary keeps the validator's name and version, each rule (its code,
severity and title) and how often it fired; the validators' full reports
are not kept. ``scripts/anomaly.py`` reads the summaries: an error code that
a new schedule version or a new week brings is a Tier 2 finding.

Java runs the validators; this module only prepares their input and reads
their output.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from pipeline.common import sha256_file, utcnow
from pipeline.config import PACIFIC

VALIDATION_VERSION = "feedcheck/0.1.0"
STATIC_VALIDATOR = "MobilityData gtfs-validator 8.0.1"
REALTIME_VALIDATOR = "MobilityData gtfs-realtime-validator, commit 7041fa3f"
RT_FEEDS = ("vehiclePositions", "tripUpdates")
RT_HOUR = 10  # 10 to 11 AM Pacific, a busy hour on weekdays
RT_EVERY_DAYS = 7


def _java(args: list[str]) -> None:
    """Run a validator; its log is shown only if it fails."""
    done = subprocess.run(["java", "-jar", *args], capture_output=True, text=True)
    if done.returncode != 0:
        print(done.stdout[-4000:], done.stderr[-4000:], sep="\n", file=sys.stderr)
        raise SystemExit(f"validator failed with exit code {done.returncode}")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _release_file(
    tag: str, name: str, sha: str, releases: Path | None, work: Path, repo: str
) -> Path:
    if releases is not None:
        path = releases / tag / name
        if sha256_file(path) != sha:
            raise SystemExit(f"{name}: not the published file (SHA-256 differs)")
        return path
    from pipeline import release

    return release.fetch(tag, repo, name, work / tag, sha)


# --- the schedule validator --------------------------------------------------


def summarize_static(report: dict[str, Any], version: str, day: str) -> dict[str, Any]:
    """The rules a schedule report says fired, with their counts."""
    notices = sorted(
        ({"code": n["code"], "severity": n["severity"], "count": int(n["totalNotices"])}
         for n in report.get("notices", [])),
        key=lambda n: (-["INFO", "WARNING", "ERROR"].index(n["severity"]), n["code"]),
    )  # fmt: skip
    by: Counter[str] = Counter()
    for n in notices:
        by[n["severity"]] += n["count"]
    return {
        "validation_version": VALIDATION_VERSION,
        "validator": STATIC_VALIDATOR,
        "validator_version": report.get("summary", {}).get("validatorVersion"),
        "schedule_version": version,
        "date_for_validation": day,
        "errors": by["ERROR"],
        "warnings": by["WARNING"],
        "infos": by["INFO"],
        "notices": notices,
    }


def run_static(zip_path: Path, jar: Path, day: str, out: Path) -> dict[str, Any]:
    if out.exists():
        shutil.rmtree(out)
    _java([str(jar), "-i", str(zip_path), "-o", str(out), "-c", "us", "-d", day, "-svu"])
    report: dict[str, Any] = _load(out / "report.json")
    return report


def validate_static(
    data: Path, jar: Path, releases: Path | None, work: Path, repo: str
) -> list[str]:
    """Report every Passio version that has none yet; return their names."""
    manifest = _load(data / "manifest" / "static.json")
    done = []
    for name, v in sorted(manifest.get("versions", {}).items()):
        target = data / "validation" / "static" / f"{name}.json"
        if v.get("source") != "passio" or target.exists() or not v.get("example"):
            continue
        ex = v["example"]
        zip_path = _release_file(ex["release_tag"], ex["file"], ex["sha256"], releases, work, repo)
        day = v.get("first_day") or ex["file"].split("-passio")[0].removeprefix("static-")
        report = run_static(zip_path, jar, day, work / f"static-{name}")
        summary = summarize_static(report, name, day)
        summary["file"] = ex["file"]
        summary["checked_at"] = utcnow().isoformat(timespec="seconds")
        _write(target, summary)
        done.append(name)
    return done


# --- the realtime validator --------------------------------------------------


def due_day(data: Path, today: date) -> str | None:
    """The latest archived weekday, if no realtime report is under a week old."""
    out = data / "validation" / "realtime"
    reports = sorted(p.stem for p in out.glob("*.json")) if out.exists() else []
    if reports and (today - date.fromisoformat(reports[-1])).days < RT_EVERY_DAYS:
        return None
    raw = _load(data / "manifest" / "raw.json").get("days", {})
    weekdays = [d for d in sorted(raw) if date.fromisoformat(d).weekday() < 5 and d not in reports]
    return weekdays[-1] if weekdays else None


def hour_window(day: str) -> tuple[datetime, datetime]:
    start = datetime.combine(date.fromisoformat(day), time(RT_HOUR), PACIFIC)
    return start, start + timedelta(hours=1)


def write_hour(raw_parquet: bytes | Path, day: str, dest: Path) -> dict[str, int]:
    """Each saved response of the hour as its own file, named so the
    validator reads the time from the name: ``<feed>/<feed>-<UTC time>Z.pb``."""
    import pyarrow.parquet as pq

    src = io.BytesIO(raw_parquet) if isinstance(raw_parquet, bytes) else raw_parquet
    lo, hi = hour_window(day)
    counts: Counter[str] = Counter()
    for r in pq.read_table(
        src, columns=["feed", "requested_at", "http_status", "body"]
    ).to_pylist():
        if r["feed"] not in RT_FEEDS or r["http_status"] != 200 or not r["body"]:
            continue
        if not lo <= r["requested_at"] < hi:
            continue
        stamp = r["requested_at"].strftime("%Y-%m-%dT%H-%M-%SZ")
        folder = dest / r["feed"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{r['feed']}-{stamp}.pb").write_bytes(r["body"])
        counts[r["feed"]] += 1
    return dict(counts)


def summarize_realtime(results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Per rule: severity, title, responses it fired in, occurrences.

    ``results`` maps each response file to the validator's list for it."""
    rules: dict[str, dict[str, Any]] = {}
    files: dict[str, set[str]] = defaultdict(set)
    for name, items in results.items():
        for item in items:
            rule = item["errorMessage"]["validationRule"]
            code = rule["errorId"]
            r = rules.setdefault(code, {"code": code, "severity": rule["severity"],
                                        "title": rule["title"], "occurrences": 0})  # fmt: skip
            r["occurrences"] += len(item.get("occurrenceList") or [])
            files[code].add(name)
    out = []
    for code, r in rules.items():
        out.append({**r, "responses": len(files[code])})
    return sorted(out, key=lambda r: (r["severity"] != "ERROR", r["code"]))


def run_realtime(jar: Path, schedule: Path, folder: Path) -> dict[str, list[dict[str, Any]]]:
    _java([str(jar), "-gtfs", str(schedule), "-gtfsRealtimePath", str(folder), "-sort", "name"])
    return {p.name.removesuffix(".results.json"): _load(p)
            for p in sorted(folder.glob("*.results.json"))}  # fmt: skip


def validate_realtime(
    data: Path, jar: Path, releases: Path | None, work: Path, repo: str, today: date
) -> str | None:
    day = due_day(data, today)
    if day is None:
        return None
    raw = _load(data / "manifest" / "raw.json")["days"][day]
    raw_file = next((f for f in raw.get("files", []) if f["name"].startswith("raw-")), None)
    if raw_file is None:
        print(f"{day}: no raw responses archived", file=sys.stderr)
        return None
    raw_path = _release_file(
        raw["release_tag"], raw_file["name"], raw_file["sha256"], releases, work, repo
    )
    pin = _load(data / "manifest" / "static.json")["days"][day]["passio"]["version"]
    ex = _load(data / "manifest" / "static.json")["versions"][pin]["example"]
    schedule = _release_file(ex["release_tag"], ex["file"], ex["sha256"], releases, work, repo)
    hour = work / f"realtime-{day}"
    if hour.exists():
        shutil.rmtree(hour)
    counts = write_hour(raw_path, day, hour)
    feeds = {}
    for feed in RT_FEEDS:
        folder = hour / feed
        if not folder.exists():
            feeds[feed] = {"responses": 0, "rules": []}
            continue
        results = run_realtime(jar, schedule, folder)
        feeds[feed] = {"responses": counts.get(feed, 0), "rules": summarize_realtime(results)}
    lo, hi = hour_window(day)
    _write(data / "validation" / "realtime" / f"{day}.json", {
        "validation_version": VALIDATION_VERSION,
        "validator": REALTIME_VALIDATOR,
        "service_date": day,
        "hour": {"from": lo.isoformat(), "to": hi.isoformat()},
        "schedule_version": pin,
        "feeds": feeds,
        "checked_at": utcnow().isoformat(timespec="seconds"),
    })  # fmt: skip
    return day


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the official GTFS validators on the archive.")
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--static-jar", type=Path)
    ap.add_argument("--rt-jar", type=Path)
    ap.add_argument("--releases", type=Path, help="local Release files, <tag>/<name>")
    ap.add_argument("--work", type=Path, default=Path("validator-work"))
    ap.add_argument("--repo", default="dfladagermccullugh-bot/Open-Island-Transit-Data")
    ap.add_argument(
        "--due", action="store_true", help="print whether a realtime check is due, and stop"
    )
    args = ap.parse_args(argv)
    today = utcnow().astimezone(PACIFIC).date()
    if args.due:
        print("realtime=" + ("true" if due_day(args.data, today) else "false"))
        return 0
    args.work.mkdir(parents=True, exist_ok=True)
    if args.static_jar:
        for name in validate_static(
            args.data, args.static_jar, args.releases, args.work, args.repo
        ):
            print(f"schedule {name}: checked")
    if args.rt_jar:
        day = validate_realtime(args.data, args.rt_jar, args.releases, args.work, args.repo, today)
        print(f"realtime {day}: checked" if day else "realtime: not due")
    return 0


if __name__ == "__main__":
    sys.exit(main())
