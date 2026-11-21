"""Nightly archive: copy complete service days from the collector to GitHub.

    python -m pipeline.archive --pending --manifest work/manifest/raw.json \
        --out work/files --release

Without --release the files are only written locally (useful for checking).
With it, each day's files are uploaded to the monthly release ``raw-YYYY-MM``
using the ``gh`` command, the upload is checked, and only then is the day
added to the manifest. The manifest is what allows the collector to delete
its own copy, so a day is never listed before its files are confirmed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pipeline import release
from pipeline.archive.build import build_day
from pipeline.archive.days import complete_days
from pipeline.archive.store import Collector
from pipeline.config import REPO


def load_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {"version": 1, "repo": REPO, "days": {}}


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["days"] = dict(sorted(manifest["days"].items()))
    path.write_text(json.dumps(manifest, indent=1, sort_keys=False, default=str) + "\n")


def release_tag(day: date) -> str:
    return f"raw-{day:%Y-%m}"


def upload(day: date, entry: dict[str, Any], out: Path, repo: str) -> str:
    tag = release_tag(day)
    release.ensure_release(
        tag,
        repo,
        title=f"Raw archive, {day:%B %Y}",
        notes=(
            "Island Transit GTFS-Realtime responses exactly as received, one "
            "Parquet file per service day, plus the fetch log (CSV) and each "
            "static GTFS download. See docs/codebook.md and LICENSE-DATA.md."
        ),
    )
    release.upload(tag, repo, out, entry["files"])
    return tag


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive complete service days.")
    ap.add_argument("--date", action="append", type=date.fromisoformat, help="a specific day")
    ap.add_argument("--pending", action="store_true", help="all complete days not yet archived")
    ap.add_argument("--manifest", type=Path, default=Path("work/manifest/raw.json"))
    ap.add_argument("--out", type=Path, default=Path("work/files"))
    ap.add_argument("--release", action="store_true", help="upload to GitHub Releases")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    ap.add_argument("--max-days", type=int, default=7, help="most days to archive in one run")
    args = ap.parse_args()

    collector = Collector()
    manifest = load_manifest(args.manifest)

    days = list(args.date or [])
    if args.pending:
        first = collector.first_day()
        if first is not None:
            days += [
                d
                for d in complete_days(first, datetime.now(UTC))
                if d.isoformat() not in manifest["days"]
            ]
    days = sorted(set(days))[: args.max_days]
    if not days:
        print("nothing to archive")
        return 0

    failed = False
    for day in days:
        try:
            entry = build_day(collector, day, args.out)
            if args.release:
                entry["release_tag"] = upload(day, entry, args.out, args.repo)
                entry["archived_at"] = datetime.now(UTC).isoformat(timespec="seconds")
                entry["code_sha"] = os.environ.get("GITHUB_SHA")
                manifest["days"][day.isoformat()] = entry
                save_manifest(args.manifest, manifest)
            size = sum(f["bytes"] for f in entry["files"])
            print(
                f"{day}: {entry['fetch_rows']} fetches, {entry['raw_rows']} bodies, "
                f"{len(entry['files'])} files, {size / 1e6:.1f} MB"
            )
        except Exception as exc:  # keep going; one bad day must not block the rest
            failed = True
            print(f"{day}: FAILED: {exc!r}", file=sys.stderr)
            traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
