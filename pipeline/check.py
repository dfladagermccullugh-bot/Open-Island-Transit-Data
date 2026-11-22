"""Check that the data branch and Releases agree, after a nightly run.

    python -m pipeline.check --data data-branch

For every day in ``manifest/raw.json``, ``parsed.json`` and ``events.json``
that has a release tag, each listed file must be in that release at the
recorded size. Every day pinned in ``static.json`` must have its crosswalk
and timepoints folders and its route map. Every day with parsed tables must have
``quality/days/<date>.json``, and every day with stop events
``ontime/days/<date>.json``. Every monthly report's checksum files must be
in their releases at the recorded size. Prints one line per problem; exits 1 if there
are any.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pipeline import release
from pipeline.common import load_json
from pipeline.config import REPO

Assets = Callable[[str], dict[str, int]]


def _load(path: Path) -> dict[str, Any]:
    return load_json(path, {"days": {}})


def gh_assets(repo: str) -> Assets:
    cache: dict[str, dict[str, int]] = {}

    def get(tag: str) -> dict[str, int]:
        if tag not in cache:
            cache[tag] = release.assets(tag, repo)
        return cache[tag]

    return get


def check(data: Path, assets: Assets) -> tuple[list[str], dict[str, int]]:
    problems: list[str] = []
    counts = {"days": 0, "files": 0}
    manifests = {m: _load(data / "manifest" / f"{m}.json") for m in ("raw", "parsed", "events")}
    days: set[str] = set()
    for name, manifest in manifests.items():
        for day, entry in manifest["days"].items():
            days.add(day)
            tag = entry.get("release_tag")
            if not tag:
                problems.append(f"{name} {day}: no release tag")
                continue
            try:
                have = assets(tag)
            except Exception as exc:  # report and keep checking the rest
                problems.append(f"{name} {day}: release {tag} not readable: {exc}")
                continue
            for f in entry["files"]:
                counts["files"] += 1
                size = have.get(f["name"])
                if size != f["bytes"]:
                    problems.append(
                        f"{name} {day}: {f['name']} in {tag} is {size} bytes, manifest {f['bytes']}"
                    )
    static = _load(data / "manifest" / "static.json")
    for day, pins in static["days"].items():
        for kind in ("crosswalk", "timepoints"):
            folder = pins.get(kind)
            if folder and not (data / kind / folder).is_dir():
                problems.append(f"static {day}: {kind} folder {folder} missing")
        key = pins.get("timepoints")
        if key and not (data / "maps" / f"{key}.json").exists():
            problems.append(f"static {day}: route map maps/{key}.json missing")
    for day in manifests["parsed"]["days"]:
        if not (data / "quality" / "days" / f"{day}.json").exists():
            problems.append(f"quality {day}: day file missing")
    for day in manifests["events"]["days"]:
        if not (data / "ontime" / "days" / f"{day}.json").exists():
            problems.append(f"ontime {day}: day file missing")
    for path in sorted((data / "reports").glob("????-??.json")):
        report = json.loads(path.read_text())
        for tag, rel in report.get("releases", {}).items():
            try:
                size = assets(tag).get(rel["checksums_file"])
            except Exception as exc:
                problems.append(f"report {report['month']}: release {tag} not readable: {exc}")
                continue
            if size != rel["checksums_bytes"]:
                problems.append(
                    f"report {report['month']}: {rel['checksums_file']} in {tag} is {size} "
                    f"bytes, report {rel['checksums_bytes']}"
                )
    counts["days"] = len(days)
    return problems, counts


def main() -> int:
    ap = argparse.ArgumentParser(description="Check the data branch against Releases.")
    ap.add_argument("--data", type=Path, default=Path("data-branch"))
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    args = ap.parse_args()
    problems, counts = check(args.data, gh_assets(args.repo))
    for p in problems:
        print(f"PROBLEM {p}")
    print(
        f"checked {counts['days']} days, {counts['files']} release files: {len(problems)} problems"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
