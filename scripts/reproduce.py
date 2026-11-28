"""Rebuild published service days from their raw files and compare.

    python scripts/reproduce.py --date 2026-11-20
    python scripts/reproduce.py --date 2026-11-20 --date 2026-11-24 --report out.json

Anyone can run this: it needs Python, this repository (``pip install -e .``)
and an internet connection, and no account or key. For each day it

1. copies the published ``data`` branch (``--data``, or a fresh shallow clone
   of it),
2. downloads that day's raw files and the schedule file its pin names from
   the Releases, each checked against its published SHA-256,
3. rebuilds the day's parsed tables, stop events, on-time summary and
   quality figures with the code in this checkout (the stages of the nightly
   job, with Releases replaced by a folder),
4. compares the SHA-256 of every rebuilt Release file with the published
   manifests, and the rebuilt day files with the published ones (leaving
   out only the time stamps and commit ids),

and prints a table of the day's headline figures, published and rebuilt.

A day made by an older version of a stage than this code has (see
``pipeline/versions.lock.json``) cannot be compared; it is reported as such.
Exit 0: every compared day identical; 1: something differs; 2: nothing could
be compared. ``--report`` writes the result as JSON (``checks/reproduce.json``
on the data branch, written weekly by ``.github/workflows/reproduce.yml`` on a
fresh GitHub runner). ``--pick N --seed S`` chooses N comparable days at
random, the same N for the same seed (the workflow seeds it with the ISO
week).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import random
import runpy
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pipeline.release as rel  # noqa: E402
from pipeline.common import asset_name, load_json, sha256_file  # noqa: E402
from pipeline.config import REPO  # noqa: E402
from pipeline.events.infer import EVENTS_VERSION  # noqa: E402
from pipeline.events.ontime import ONTIME_VERSION  # noqa: E402
from pipeline.events.validate import VALIDATE_VERSION  # noqa: E402
from pipeline.quality.metrics import QUALITY_VERSION  # noqa: E402
from pipeline.tables.build import TABLES_VERSION  # noqa: E402

REPRODUCE_VERSION = "reproduce/0.1.0"
STAMPS = {"computed_at", "built_at", "archived_at", "code_sha"}
CURRENT = {
    ("parsed", "tables_version"): TABLES_VERSION,
    ("events", "events_version"): EVENTS_VERSION,
    ("events", "ontime_version"): ONTIME_VERSION,
    ("events", "validate_version"): VALIDATE_VERSION,
}
FIGURES = (
    ("quality", ("coverage", "trip_coverage")),
    ("quality", ("events", "stop_coverage", "timepoints", "coverage")),
    ("ontime", ("scheduled",)),
    ("ontime", ("observed",)),
    ("ontime", ("on_time",)),
    ("ontime", ("on_time_share_of_observed",)),
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clone_data(dest: Path, repo: str) -> Path:
    url = f"https://github.com/{repo}.git"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--branch", "data", url, str(dest)], check=True
    )
    return dest


def release_names(data: Path) -> dict[tuple[str, str], str]:
    """(tag, plain name) -> name in the Release, from ``files.csv``."""
    path = data / "files.csv"
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            (r["release_tag"], r["name"]): r.get("asset") or r["name"] for r in csv.DictReader(fh)
        }


class Store:
    """GitHub Releases replaced by a folder: what a stage uploads is kept
    there, and what it fetches is taken from there or downloaded from the
    published Releases and checked by SHA-256."""

    def __init__(self, folder: Path, names: dict[tuple[str, str], str], repo: str) -> None:
        self.folder, self.names, self.repo = folder, names, repo
        self.downloaded: list[str] = []

    def install(self) -> None:
        rel.ensure_release = lambda tag, repo, title, notes: (self.folder / tag).mkdir(
            parents=True, exist_ok=True
        )
        rel.upload = self.upload
        rel.fetch = self.fetch
        rel.assets = lambda tag, repo: {
            f.name: f.stat().st_size for f in (self.folder / tag).glob("*")
        }
        rel.set_notes = lambda tag, repo, notes: None

    def upload(self, tag: str, repo: str, out: Path, files: list[dict[str, Any]], **_: Any) -> None:
        (self.folder / tag).mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy(out / f["name"], self.folder / tag / f["name"])

    def fetch(self, tag: str, repo: str, name: str, dest: Path, sha256: str) -> Path:
        path = dest / name
        if path.exists() and sha256_file(path) == sha256:
            return path
        kept = self.folder / tag / name
        if not (kept.exists() and sha256_file(kept) == sha256):
            remote = self.names.get((tag, name)) or asset_name(name, sha256)
            url = f"https://github.com/{self.repo}/releases/download/{tag}/{remote}"
            with urllib.request.urlopen(url, timeout=300) as resp:
                body = resp.read()
            if _sha(body) != sha256:
                raise RuntimeError(f"{tag}/{remote}: not the published file (SHA-256 differs)")
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_bytes(body)
            self.downloaded.append(f"{tag}/{remote}")
        dest.mkdir(parents=True, exist_ok=True)
        if kept.resolve() != path.resolve():
            shutil.copy(kept, path)
        return path


def comparable(data: Path, day: str) -> list[str]:
    """Why a published day cannot be compared with this code (empty: it can)."""
    why = []
    for (manifest, key), current in CURRENT.items():
        entry = (load_json(data / "manifest" / f"{manifest}.json") or {}).get("days", {}).get(day)
        if entry is None:
            why.append(f"{day} is not in manifest/{manifest}.json")
        elif entry.get(key) != current:
            why.append(f"{key} {entry.get(key)} published, {current} here")
    q = load_json(data / "quality" / "days" / f"{day}.json")
    if q is None:
        why.append(f"quality/days/{day}.json is missing")
    elif q.get("quality_version") != QUALITY_VERSION:
        why.append(f"quality_version {q.get('quality_version')} published, {QUALITY_VERSION} here")
    return sorted(set(why))


def without_stamps(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: without_stamps(v) for k, v in obj.items() if k not in STAMPS}
    if isinstance(obj, list):
        return [without_stamps(v) for v in obj]
    return obj


def _get(obj: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def run(module: str, argv: list[str]) -> None:
    sys.argv = [module, *argv]
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        if exc.code:
            raise RuntimeError(f"{module} exited with {exc.code}") from None


def rebuild(published: Path, day: str, work: Path, store: Store) -> Path:
    """The data branch after rebuilding ``day`` from its raw files."""
    data = work / "data-branch"
    shutil.copytree(published, data, ignore=shutil.ignore_patterns(".git"))
    for manifest in ("parsed", "events"):
        path = data / "manifest" / f"{manifest}.json"
        m = load_json(path)
        m["days"].pop(day, None)
        path.write_text(json.dumps(m, indent=1, default=str) + "\n")
    for folder in ("ontime", "quality"):
        (data / folder / "days" / f"{day}.json").unlink(missing_ok=True)
    raw, tables, events = work / "raw", work / "tables", work / "events"
    run(
        "pipeline.tables",
        [
            "--date",
            day,
            "--release",
            "--raw-manifest",
            f"{data}/manifest/raw.json",
            "--manifest",
            f"{data}/manifest/parsed.json",
            "--raw-dir",
            str(raw),
            "--out",
            str(tables),
            "--repo",
            store.repo,
        ],
    )
    run(
        "pipeline.events",
        [
            "--date",
            day,
            "--release",
            "--data",
            str(data),
            "--raw-dir",
            str(raw),
            "--tables-dir",
            str(tables),
            "--out",
            str(events),
            "--repo",
            store.repo,
        ],
    )
    run(
        "pipeline.quality",
        [
            "--date",
            day,
            "--data",
            str(data),
            "--raw-dir",
            str(raw),
            "--tables-dir",
            str(tables),
            "--events-dir",
            str(events),
            "--repo",
            store.repo,
        ],
    )
    return data


def compare_day(published: Path, rebuilt: Path, day: str) -> dict[str, Any]:
    files = []
    for manifest in ("parsed", "events"):
        pub = load_json(published / "manifest" / f"{manifest}.json")["days"][day]
        new = load_json(rebuilt / "manifest" / f"{manifest}.json")["days"].get(day, {})
        new_sha = {f["name"]: f["sha256"] for f in new.get("files", [])}
        for f in pub["files"]:
            files.append(
                {
                    "name": f["name"],
                    "published": f["sha256"],
                    "rebuilt": new_sha.get(f["name"]),
                    "same": new_sha.get(f["name"]) == f["sha256"],
                }
            )
    day_files, figures = [], []
    docs = {}
    for folder in ("ontime", "quality"):
        rel_path = f"{folder}/days/{day}.json"
        pub = load_json(published / rel_path)
        new = load_json(rebuilt / rel_path)
        docs[folder] = (pub, new)
        day_files.append(
            {
                "name": rel_path,
                "same": new is not None and without_stamps(pub) == without_stamps(new),
            }
        )
    for folder, path in FIGURES:
        pub, new = docs[folder]
        figures.append(
            {
                "figure": f"{folder}: {'.'.join(path)}",
                "published": _get(pub, path),
                "rebuilt": _get(new, path),
            }
        )
    same = all(f["same"] for f in files) and all(f["same"] for f in day_files)
    return {
        "service_date": day,
        "identical": same,
        "files": files,
        "day_files": day_files,
        "figures": figures,
    }


def pick_days(data: Path, n: int, seed: str) -> list[str]:
    days = sorted(load_json(data / "manifest" / "events.json")["days"])
    ok = [d for d in days if not comparable(data, d)]
    return sorted(random.Random(seed).sample(ok, min(n, len(ok))))


def table(result: dict[str, Any]) -> str:
    lines = [f"{result['service_date']}: " + ("identical" if result["identical"] else "DIFFERS")]
    for f in result["files"] + result["day_files"]:
        mark = "same" if f["same"] else "DIFFERENT"
        lines.append(f"  {mark:9}  {f['name']}")
    for f in result["figures"]:
        lines.append(f"  {f['figure']}: published {f['published']}, rebuilt {f['rebuilt']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild published days from raw and compare.")
    ap.add_argument("--date", action="append", default=[], help="a service day (YYYY-MM-DD)")
    ap.add_argument("--pick", type=int, help="choose this many comparable days at random")
    ap.add_argument("--seed", default="", help="seed for --pick (the workflow uses the ISO week)")
    ap.add_argument("--data", type=Path, help="published data branch checkout (default: clone it)")
    ap.add_argument("--work", type=Path, help="working folder (default: a temporary one)")
    ap.add_argument("--report", type=Path, help="write the result as JSON")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    args = ap.parse_args(argv)
    work = args.work or Path(tempfile.mkdtemp(prefix="reproduce-"))
    work.mkdir(parents=True, exist_ok=True)
    published = args.data or clone_data(work / "published", args.repo)
    days = list(args.date)
    if args.pick:
        days += pick_days(published, args.pick, args.seed)
    if not days:
        ap.error("give --date or --pick")

    ran_at = datetime.now(UTC).isoformat(timespec="seconds")
    store = Store(work / "releases", release_names(published), args.repo)
    store.install()
    os.environ.setdefault("OITD_NOW", "2000-01-01T00:00:00+00:00")  # stamps are not compared
    results, skipped = [], []
    for day in sorted(set(days)):
        why = comparable(published, day)
        if why:
            skipped.append({"service_date": day, "why": why})
            print(f"{day}: not compared: {'; '.join(why)}")
            continue
        rebuilt = rebuild(published, day, work / day, store)
        result = compare_day(published, rebuilt, day)
        results.append(result)
        print(table(result))
    report = {
        "reproduce_version": REPRODUCE_VERSION,
        "ran_at": ran_at,
        "days": results,
        "skipped": skipped,
        "days_compared": len(results),
        "all_identical": bool(results) and all(r["identical"] for r in results),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "code_commit": os.environ.get("GITHUB_SHA"),
        "run_url": (
            f"https://github.com/{args.repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            if "GITHUB_RUN_ID" in os.environ
            else None
        ),
        "command": "python scripts/reproduce.py "
        + " ".join(f"--date {r['service_date']}" for r in results),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(
        f"{len(results)} days compared, {len(skipped)} not comparable: "
        + (
            "every file identical"
            if report["all_identical"]
            else "differences found"
            if results
            else "nothing compared"
        )
    )
    if not results:
        return 2
    return 0 if report["all_identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
