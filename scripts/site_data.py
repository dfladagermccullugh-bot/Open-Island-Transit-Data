"""Copy what the site reads from a data branch checkout into ``site/data``.

    python scripts/site_data.py data-branch site/data

Only the files the pages load are copied (the data branch also holds one
file per day and per schedule version, which the site does not need), and a
small ``build.json`` says which service day the figures run through and, from
``checks/gate.json``, how many rules of the publishing gate they passed and
when (shown in every page's footer). Exits 1
if a file the home page depends on is missing, so a broken checkout cannot
replace the live site with an empty one.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

REQUIRED = ("ontime/recent.json", "ontime/daily.csv", "quality/daily.csv", "manifest/raw.json")
OPTIONAL = (
    "ontime/field-check.json",
    "quality/gaps.json",
    "reports/index.json",
    "files.csv",
    "datapackage.json",
    "ontime/headline.json",
    "ontime/periods.json",
    "quality/periods.json",
    "quality/notes.json",
    "compare/summary.json",
)


def latest_service_day(daily: Path) -> str | None:
    """The last day in a daily table on which buses were scheduled."""
    with daily.open(encoding="utf-8", newline="") as fh:
        days = [
            r["service_date"]
            for r in csv.DictReader(fh)
            # "False" in the first files, "false" since the tables write lowercase.
            if r.get("service_expected", "true").strip().lower() != "false"
        ]
    return days[-1] if days else None


def wanted(src: Path) -> tuple[list[str], list[str]]:
    """(files to copy, required files that are missing), relative to ``src``."""
    missing = [f for f in REQUIRED if not (src / f).exists()]
    files = [f for f in (*REQUIRED, *OPTIONAL) if (src / f).exists()]
    for folder in ("ontime", "quality"):
        daily = src / folder / "daily.csv"
        day = latest_service_day(daily) if daily.exists() else None
        if day and (src / folder / "days" / f"{day}.json").exists():
            files.append(f"{folder}/days/{day}.json")
    recent = src / "ontime" / "recent.json"
    if recent.exists():
        key = json.loads(recent.read_text(encoding="utf-8")).get("timepoints")
        if key and (src / "maps" / f"{key}.json").exists():
            files.append(f"maps/{key}.json")
        elif key:
            missing.append(f"maps/{key}.json")
    return files, missing


def gate_summary(src: Path) -> dict[str, object] | None:
    """The full publishing gate's report, as the footer shows it: only a
    passed gate is ever on the data branch, but a report that says otherwise
    is not shown."""
    path = src / "checks" / "gate.json"
    try:
        gate = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except ValueError:
        return None
    if not gate or gate.get("passed") is not True:
        return None
    return {"rules": gate.get("rules_count"), "ran_at": gate.get("ran_at"),
            "latest_service_date": gate.get("latest_service_date")}  # fmt: skip


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    files, missing = wanted(src)
    if missing:
        print("missing from the data branch: " + ", ".join(missing), file=sys.stderr)
        return 1
    if dst.exists():
        shutil.rmtree(dst)
    for f in files:
        (dst / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / f, dst / f)
    reports = dst / "reports" / "index.json"
    if not reports.exists():  # no month is complete yet: an empty list, not a missing file
        reports.parent.mkdir(parents=True, exist_ok=True)
        reports.write_text("[]\n", encoding="utf-8")
    recent = json.loads((src / "ontime" / "recent.json").read_text(encoding="utf-8"))
    build = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_through": recent.get("last_service_date"),
        "gate": gate_summary(src),
    }
    (dst / "build.json").write_text(json.dumps(build) + "\n", encoding="utf-8")
    size = sum((dst / f).stat().st_size for f in files)
    print(f"{len(files)} files, {size / 1e6:.2f} MB, data through {build['data_through']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
