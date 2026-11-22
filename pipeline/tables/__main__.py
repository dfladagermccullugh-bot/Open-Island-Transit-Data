"""Build the parsed daily tables from archived raw files.

    python -m pipeline.tables --pending --raw-manifest work/manifest/raw.json \
        --manifest work/manifest/parsed.json --raw-dir work/files \
        --out work/tables --release

A day is pending when it is in the raw manifest and either missing from the
parsed manifest or built with an older ``TABLES_VERSION``. The raw file is
taken from ``--raw-dir`` if a copy with the right SHA-256 is there, otherwise
downloaded from its release. With --release, the tables go to the monthly
release ``parsed-YYYY-MM`` and the day is added to the parsed manifest once
the upload is confirmed. Without it, the files are only written locally.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Any

from pipeline import release
from pipeline.common import code_sha, run_order, stamp
from pipeline.config import REPO
from pipeline.tables.build import TABLES_VERSION, build_tables, read_raw, write_tables


def load(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {"version": 1, "repo": REPO, "days": {}}


def save(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["days"] = dict(sorted(manifest["days"].items()))
    path.write_text(json.dumps(manifest, indent=1, default=str) + "\n")


def release_tag(day: date) -> str:
    return f"parsed-{day:%Y-%m}"


def pending(raw: dict[str, Any], parsed: dict[str, Any]) -> list[date]:
    out = []
    for day, entry in raw["days"].items():
        if not entry.get("release_tag"):
            continue
        done = parsed["days"].get(day)
        if done is None or done.get("tables_version") != TABLES_VERSION:
            out.append(date.fromisoformat(day))
    return sorted(out)


def raw_file(day: date, raw_entry: dict[str, Any], raw_dir: Path, repo: str) -> Path:
    name = f"raw-{day.isoformat()}.parquet"
    info = next(f for f in raw_entry["files"] if f["name"] == name)
    return release.fetch(raw_entry["release_tag"], repo, name, raw_dir, info["sha256"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Build parsed daily tables.")
    ap.add_argument("--date", action="append", type=date.fromisoformat, help="a specific day")
    ap.add_argument("--pending", action="store_true", help="all archived days not yet parsed")
    ap.add_argument("--raw-manifest", type=Path, default=Path("work/manifest/raw.json"))
    ap.add_argument("--manifest", type=Path, default=Path("work/manifest/parsed.json"))
    ap.add_argument("--raw-dir", type=Path, default=Path("work/files"))
    ap.add_argument("--raw-file", type=Path, help="build from this file instead (no upload)")
    ap.add_argument("--out", type=Path, default=Path("work/tables"))
    ap.add_argument("--release", action="store_true", help="upload to GitHub Releases")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    ap.add_argument("--max-days", type=int, default=7, help="most days to build in one run")
    args = ap.parse_args()

    if args.raw_file:
        if args.release or not args.date or len(args.date) != 1:
            ap.error("--raw-file needs exactly one --date and no --release")
        entry = write_tables(args.date[0], build_tables(read_raw(args.raw_file)), args.out)
        print(json.dumps(entry["rows"]))
        return 0

    raw = load(args.raw_manifest)
    manifest = load(args.manifest)
    days = list(args.date or [])
    if args.pending:
        days += pending(raw, manifest)
    days = run_order(days, manifest["days"], args.max_days)
    if not days:
        print("nothing to parse")
        return 0

    failed = False
    for day in days:
        try:
            raw_entry = raw["days"][day.isoformat()]
            path = raw_file(day, raw_entry, args.raw_dir, args.repo)
            entry = write_tables(day, build_tables(read_raw(path), presorted=True), args.out)
            entry["raw_sha256"] = next(
                f["sha256"] for f in raw_entry["files"] if f["name"] == path.name
            )
            if args.release:
                tag = release_tag(day)
                release.ensure_release(
                    tag,
                    args.repo,
                    title=f"Parsed tables, {day:%B %Y}",
                    notes=(
                        "Vehicle positions, trip updates, trip-update stop predictions "
                        "and alerts, one Parquet file per table per service day, decoded "
                        "from the raw archive (raw-YYYY-MM releases). See docs/codebook.md."
                    ),
                )
                release.upload(tag, args.repo, args.out, entry["files"])
                entry["release_tag"] = tag
                entry["built_at"] = stamp()
                entry["code_sha"] = code_sha()
                manifest["days"][day.isoformat()] = entry
                save(args.manifest, manifest)
            print(f"{day}: {entry['rows']}")
        except Exception as exc:  # keep going; one bad day must not block the rest
            failed = True
            print(f"{day}: FAILED: {exc!r}", file=sys.stderr)
            traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
