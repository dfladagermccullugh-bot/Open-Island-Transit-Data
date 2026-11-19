"""Update the static GTFS version registry from the raw archive.

    python -m pipeline.gtfs --raw-manifest work/manifest/raw.json \
        --manifest work/manifest/static.json --raw-dir work/files

Every static download listed in the raw manifest that is not yet in the
registry is read (from ``--raw-dir`` if a copy with the right SHA-256 is
there, otherwise from its release), fingerprinted and added. Then every
archived day is pinned to one version per source. See
``pipeline/gtfs/versions.py`` for the rules.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from pipeline import release
from pipeline.config import REPO
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Update the static GTFS version registry.")
    ap.add_argument("--raw-manifest", type=Path, default=Path("work/manifest/raw.json"))
    ap.add_argument("--manifest", type=Path, default=Path("work/manifest/static.json"))
    ap.add_argument("--raw-dir", type=Path, default=Path("work/files"))
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    args = ap.parse_args()

    if not args.raw_manifest.exists():
        print("no raw manifest yet")
        return 0
    raw = json.loads(args.raw_manifest.read_text())
    registry = load(args.manifest)
    added = update(registry, raw, args.raw_dir, args.repo)
    save(args.manifest, registry)
    print(f"{added} downloads added; {len(registry['versions'])} versions; ", end="")
    print(f"{len(registry['days'])} days pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
