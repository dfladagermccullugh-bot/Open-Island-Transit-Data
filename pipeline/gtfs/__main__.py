"""Update the static GTFS version registry from the raw archive.

    python -m pipeline.gtfs --raw-manifest work/manifest/raw.json \
        --manifest work/manifest/static.json --raw-dir work/files

Every static download listed in the raw manifest that is not yet in the
registry is read (from ``--raw-dir`` if a copy with the right SHA-256 is
there, otherwise from its release), fingerprinted and added. Then every
archived day is pinned to one version per source. See
``pipeline/gtfs/versions.py`` for the rules. On a day whose complete Passio
downloads disagree, the versions are compared with the trip ids in that
day's vehicles table (``live_match``), and reviewed rows of
``reference/static-pin-overrides.csv`` replace a pin.

For each pair of pinned Passio and Trillium versions, the crosswalk
(``pipeline/gtfs/crosswalk.py``) is written once to
``<crosswalk-dir>/<passio version>__<trillium version>/``. For each pair of
Passio version and printed timetable in effect, the true timepoints
(``pipeline/gtfs/timepoints.py``) are written once to
``<timepoints-dir>/<passio version>__timetable-<date>-<hash>/``, and the
route map for the site (``pipeline/gtfs/routemap.py``) to
``<maps-dir>/<same key>.json``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from pipeline import release
from pipeline.common import load_json, sha_of
from pipeline.config import REPO
from pipeline.gtfs import crosswalk, routemap, timepoints
from pipeline.gtfs.schedule import scheduled_trips
from pipeline.gtfs.tables import rows as gtfs_rows
from pipeline.gtfs.versions import (
    OVERRIDES,
    Evidence,
    add_download,
    fingerprint,
    load_overrides,
    pin_days,
)
from pipeline.gtfs.zipread import ZipContents, load_zip, read_zip


def load(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {"version": 1, "repo": REPO, "versions": {}, "downloads": {}, "days": {}}


def save(path: Path, registry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["versions"] = dict(sorted(registry["versions"].items()))
    registry["downloads"] = dict(sorted(registry["downloads"].items(), key=lambda kv: int(kv[0])))
    path.write_text(json.dumps(registry, indent=1) + "\n")


def update(
    registry: dict[str, Any],
    raw: dict[str, Any],
    raw_dir: Path,
    repo: str,
    *,
    evidence: Evidence | None = None,
    overrides: dict[tuple[str, str], dict[str, str]] | None = None,
    problems: list[str] | None = None,
) -> int:
    """Add new downloads and pin every archived day; return how many
    downloads were added."""
    added = 0
    for day, entry in sorted(raw["days"].items()):
        if not entry.get("release_tag"):
            continue
        sizes = {f["name"]: f for f in entry["files"]}
        for s in entry.get("static", []):
            if str(s["static_fetch_id"]) in registry["downloads"] or not s.get("file"):
                continue
            path = release.fetch(
                entry["release_tag"], repo, s["file"], raw_dir, sizes[s["file"]]["sha256"]
            )
            download = {
                "static_fetch_id": s["static_fetch_id"],
                "source": s["source"],
                "day": day,
                "requested_at": s["requested_at"],
                "release_tag": entry["release_tag"],
                "file": s["file"],
                "sha256": s["sha256"],
            }
            add_download(registry, download, fingerprint(s["source"], read_zip(path.read_bytes())))
            added += 1
    archived = [day for day, e in raw["days"].items() if e.get("release_tag")]
    registry["days"] = pin_days(
        registry,
        archived,
        registry.get("days"),
        evidence=evidence,
        overrides=overrides,
        problems=problems,
    )
    return added


def live_match(
    day: str,
    source: str,
    versions: list[str],
    *,
    registry: dict[str, Any],
    parsed: dict[str, Any],
    raw_dir: Path,
    tables_dir: Path,
    repo: str,
) -> list[dict[str, Any]] | None:
    """How well each of ``versions`` fits the trips the live feed reported on
    ``day`` (the evidence ``pin_days`` asks for when a day's downloads
    disagree). Only Passio's ids appear in the live feed. None means the
    day's vehicles table does not exist yet."""
    if source != "passio":
        return []
    entry = parsed.get("days", {}).get(day)
    name = f"vehicles-{day}.parquet"
    if not entry or not entry.get("release_tag"):
        return None
    path = release.fetch(entry["release_tag"], repo, name, tables_dir, sha_of(entry, name))
    live = {t for t in pq.read_table(path, columns=["trip_id"])["trip_id"].to_pylist() if t}
    out = []
    for version in versions:
        contents = _zip(registry, version, raw_dir, repo)
        known = {t["trip_id"] for t in gtfs_rows(contents, "trips.txt")}
        scheduled = {t.trip_id for t in scheduled_trips(contents, date.fromisoformat(day))}
        out.append(
            {
                "version": version,
                "scheduled_trips": len(scheduled),
                "scheduled_trips_seen": len(scheduled & live),
                "live_trip_ids": len(live),
                "live_trip_ids_in_version": len(known & live),
            }
        )
    return out


def _zip(registry: dict[str, Any], version: str, raw_dir: Path, repo: str) -> ZipContents:
    ex = registry["versions"][version]["example"]
    path = release.fetch(ex["release_tag"], repo, ex["file"], raw_dir, ex["sha256"])
    return load_zip(path, ex["sha256"])


def _made_by(path: Path, field: str, version: str, seen: dict[Path, bool] | None = None) -> bool:
    """True if the JSON file exists and records that code version. A file
    from older code is rebuilt in place, so a fix reaches every folder.

    Many days share one folder. ``seen`` remembers the files already found
    up to date in this pass, so each is opened once and not once per day."""
    if seen is not None and seen.get(path):
        return True
    if not path.exists():
        return False
    try:
        current = json.loads(path.read_text()).get(field) == version
    except json.JSONDecodeError:
        return False
    if seen is not None and current:
        seen[path] = True
    return current


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["empty"])
        writer.writeheader()
        writer.writerows(rows)


def _reference_id(effective: date, reference_dir: Path = timepoints.REFERENCE_DIR) -> str:
    """``timetable-<date>-<hash>``: the hash covers the reference files and the
    matching code version, so a change to either gets a new folder instead of
    silently replacing one."""
    h = hashlib.sha256(timepoints.TIMEPOINTS_VERSION.encode())
    for path in timepoints.references(reference_dir)[effective]:
        h.update(path.read_bytes() if path.exists() else b"")
    return f"timetable-{effective.isoformat()}-{h.hexdigest()[:8]}"


def write_timepoints(
    registry: dict[str, Any],
    out: Path,
    raw_dir: Path,
    repo: str,
    reference_dir: Path = timepoints.REFERENCE_DIR,
) -> list[str]:
    """Match the printed timetable in effect each day to that day's Passio
    version, write it once per pair, and record which applies to each day."""
    written = []
    # Every day under one printed timetable has the same reference id; work
    # it out (it hashes the reference files) once per timetable.
    ids: dict[date, str] = {}
    for day, pins in registry["days"].items():
        pv = (pins.get("passio") or {}).get("version")
        effective = timepoints.reference_for(date.fromisoformat(day), reference_dir)
        if effective and effective not in ids:
            ids[effective] = _reference_id(effective, reference_dir)
        key = f"{pv}__{ids[effective]}" if pv and effective else None
        pins["timepoints"] = key
        if not key or (out / key / "summary.json").exists():
            continue
        built = timepoints.build(_zip(registry, pv, raw_dir, repo), effective, reference_dir)
        folder = out / key
        folder.mkdir(parents=True, exist_ok=True)
        _write_csv(folder / "timepoints.csv", built["matched"])
        summary = {"passio_version": pv, **built["summary"]}
        (folder / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
        written.append(key)
    return written


def write_maps(
    registry: dict[str, Any], tp_dir: Path, out: Path, raw_dir: Path, repo: str
) -> list[str]:
    """Write the route map once for every timepoints folder in use."""
    written = []
    seen: dict[Path, bool] = {}
    for pins in registry["days"].values():
        key = pins.get("timepoints")
        if not key or _made_by(out / f"{key}.json", "map_version", routemap.MAP_VERSION, seen):
            continue
        pv = pins["passio"]["version"]
        with (tp_dir / key / "timepoints.csv").open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        built = routemap.build(_zip(registry, pv, raw_dir, repo), rows)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{key}.json").write_text(json.dumps({"passio_version": pv, **built}) + "\n")
        written.append(key)
    return written


def write_crosswalks(registry: dict[str, Any], out: Path, raw_dir: Path, repo: str) -> list[str]:
    """Write the crosswalk for every pinned version pair that lacks one, and
    record which crosswalk applies to each day. Returns the pairs written."""
    written = []
    seen: dict[Path, bool] = {}
    for pins in registry["days"].values():
        pv = (pins.get("passio") or {}).get("version")
        tv = (pins.get("trillium") or {}).get("version")
        pins["crosswalk"] = f"{pv}__{tv}" if pv and tv else None
        done = out / str(pins["crosswalk"]) / "summary.json"
        if not pins["crosswalk"] or _made_by(
            done, "crosswalk_version", crosswalk.CROSSWALK_VERSION, seen
        ):
            continue
        passio, trillium = (_zip(registry, v, raw_dir, repo) for v in (pv, tv))
        built = crosswalk.build(passio, trillium)
        folder = out / pins["crosswalk"]
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("stops", "routes", "timepoint_stops", "schedule_agreement"):
            _write_csv(folder / f"{name}.csv", built[name])
        summary = {
            "passio_version": pv,
            "trillium_version": tv,
            "crosswalk_version": crosswalk.CROSSWALK_VERSION,
            **built["summary"],
        }
        (folder / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
        written.append(pins["crosswalk"])
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Update the static GTFS version registry.")
    ap.add_argument("--raw-manifest", type=Path, default=Path("work/manifest/raw.json"))
    ap.add_argument("--manifest", type=Path, default=Path("work/manifest/static.json"))
    ap.add_argument("--raw-dir", type=Path, default=Path("work/files"))
    ap.add_argument("--crosswalk-dir", type=Path, default=Path("work/crosswalk"))
    ap.add_argument("--timepoints-dir", type=Path, default=Path("work/timepoints"))
    ap.add_argument("--maps-dir", type=Path, help="default: maps/ beside the timepoints dir")
    ap.add_argument("--parsed-manifest", type=Path, help="default: beside the static manifest")
    ap.add_argument("--tables-dir", type=Path, default=Path("work/tables"))
    ap.add_argument("--pin-overrides", type=Path, default=OVERRIDES)
    ap.add_argument("--reference-dir", type=Path, default=timepoints.REFERENCE_DIR)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    args = ap.parse_args()

    if not args.raw_manifest.exists():
        print("no raw manifest yet")
        return 0
    raw = json.loads(args.raw_manifest.read_text())
    registry = load(args.manifest)
    parsed_path = args.parsed_manifest or args.manifest.parent / "parsed.json"
    evidence = partial(
        live_match,
        registry=registry,
        parsed=load_json(parsed_path, {"days": {}}),
        raw_dir=args.raw_dir,
        tables_dir=args.tables_dir,
        repo=args.repo,
    )
    problems: list[str] = []
    added = update(
        registry,
        raw,
        args.raw_dir,
        args.repo,
        evidence=evidence,
        overrides=load_overrides(args.pin_overrides),
        problems=problems,
    )
    written = write_crosswalks(registry, args.crosswalk_dir, args.raw_dir, args.repo)
    tps = write_timepoints(
        registry, args.timepoints_dir, args.raw_dir, args.repo, args.reference_dir
    )
    maps_dir = args.maps_dir or args.timepoints_dir.parent / "maps"
    maps = write_maps(registry, args.timepoints_dir, maps_dir, args.raw_dir, args.repo)
    save(args.manifest, registry)
    print(f"{added} downloads added; {len(registry['versions'])} versions; ", end="")
    print(f"{len(registry['days'])} days pinned; crosswalks written: {written or 'none'}; ", end="")
    print(f"timepoints written: {tps or 'none'}; maps written: {maps or 'none'}")
    for day, pins in registry["days"].items():
        pin = pins.get("passio") or {}
        if pin.get("changed_during_day") or pin.get("override"):
            print(f"{day}: passio pinned to {pin.get('version')} ({pin.get('basis')})")
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
