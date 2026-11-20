"""Update the static GTFS version registry from the raw archive.

    python -m pipeline.gtfs --raw-manifest work/manifest/raw.json \
        --manifest work/manifest/static.json --raw-dir work/files

Every static download listed in the raw manifest that is not yet in the
registry is read (from ``--raw-dir`` if a copy with the right SHA-256 is
there, otherwise from its release), fingerprinted and added. Then every
archived day is pinned to one version per source. See
``pipeline/gtfs/versions.py`` for the rules.

For each pair of pinned Passio and Trillium versions, the crosswalk
(``pipeline/gtfs/crosswalk.py``) is written once to
``<crosswalk-dir>/<passio version>__<trillium version>/``. For each pair of
Passio version and printed timetable in effect, the true timepoints
(``pipeline/gtfs/timepoints.py``) are written once to
``<timepoints-dir>/<passio version>__timetable-<date>-<hash>/``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from pipeline import release
from pipeline.config import REPO
from pipeline.gtfs import crosswalk, timepoints
from pipeline.gtfs.versions import add_download, fingerprint, pin_days
from pipeline.gtfs.zipread import read_zip


def load(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {"version": 1, "repo": REPO, "versions": {}, "downloads": {}, "days": {}}


def save(path: Path, registry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["versions"] = dict(sorted(registry["versions"].items()))
    registry["downloads"] = dict(sorted(registry["downloads"].items(), key=lambda kv: int(kv[0])))
    path.write_text(json.dumps(registry, indent=1) + "\n")


def update(registry: dict[str, Any], raw: dict[str, Any], raw_dir: Path, repo: str) -> int:
    """Add new downloads; return how many were added."""
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
    registry["days"] = pin_days(registry, archived)
    return added


def _zip(registry: dict[str, Any], version: str, raw_dir: Path, repo: str):
    ex = registry["versions"][version]["example"]
    path = release.fetch(ex["release_tag"], repo, ex["file"], raw_dir, ex["sha256"])
    return read_zip(path.read_bytes())


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["empty"])
        writer.writeheader()
        writer.writerows(rows)


def _reference_id(effective: date) -> str:
    """``timetable-<date>-<hash>``: the hash covers the reference files and the
    matching code version, so a change to either gets a new folder instead of
    silently replacing one."""
    h = hashlib.sha256(timepoints.TIMEPOINTS_VERSION.encode())
    for path in timepoints.references()[effective]:
        h.update(path.read_bytes() if path.exists() else b"")
    return f"timetable-{effective.isoformat()}-{h.hexdigest()[:8]}"


def write_timepoints(registry: dict[str, Any], out: Path, raw_dir: Path, repo: str) -> list[str]:
    """Match the printed timetable in effect each day to that day's Passio
    version, write it once per pair, and record which applies to each day."""
    written = []
    for day, pins in registry["days"].items():
        pv = (pins.get("passio") or {}).get("version")
        effective = timepoints.reference_for(date.fromisoformat(day))
        key = f"{pv}__{_reference_id(effective)}" if pv and effective else None
        pins["timepoints"] = key
        if not key or (out / key / "summary.json").exists():
            continue
        built = timepoints.build(_zip(registry, pv, raw_dir, repo), effective)
        folder = out / key
        folder.mkdir(parents=True, exist_ok=True)
        _write_csv(folder / "timepoints.csv", built["matched"])
        summary = {"passio_version": pv, **built["summary"]}
        (folder / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
        written.append(key)
    return written


def write_crosswalks(registry: dict[str, Any], out: Path, raw_dir: Path, repo: str) -> list[str]:
    """Write the crosswalk for every pinned version pair that lacks one, and
    record which crosswalk applies to each day. Returns the pairs written."""
    written = []
    for pins in registry["days"].values():
        pv = (pins.get("passio") or {}).get("version")
        tv = (pins.get("trillium") or {}).get("version")
        pins["crosswalk"] = f"{pv}__{tv}" if pv and tv else None
        if not pins["crosswalk"] or (out / pins["crosswalk"] / "summary.json").exists():
            continue
        passio, trillium = (_zip(registry, v, raw_dir, repo) for v in (pv, tv))
        built = crosswalk.build(passio, trillium)
        folder = out / pins["crosswalk"]
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("stops", "routes", "timepoint_stops", "schedule_agreement"):
            _write_csv(folder / f"{name}.csv", built[name])
        summary = {"passio_version": pv, "trillium_version": tv, **built["summary"]}
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
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    args = ap.parse_args()

    if not args.raw_manifest.exists():
        print("no raw manifest yet")
        return 0
    raw = json.loads(args.raw_manifest.read_text())
    registry = load(args.manifest)
    added = update(registry, raw, args.raw_dir, args.repo)
    written = write_crosswalks(registry, args.crosswalk_dir, args.raw_dir, args.repo)
    tps = write_timepoints(registry, args.timepoints_dir, args.raw_dir, args.repo)
    save(args.manifest, registry)
    print(f"{added} downloads added; {len(registry['versions'])} versions; ", end="")
    print(f"{len(registry['days'])} days pinned; crosswalks written: {written or 'none'}; ", end="")
    print(f"timepoints written: {tps or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
