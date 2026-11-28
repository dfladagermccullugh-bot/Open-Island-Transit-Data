"""Run every nightly step locally, with GitHub Releases replaced by a folder.

    python scripts/rehearse_nightly.py 2026-11-19 /path/to/empty/folder
    python scripts/rehearse_nightly.py --replay /path/to/folder [--from-raw]

Publishes nothing. Pulls the day from the collector, then runs archive,
tables, static GTFS, events, quality, monthly reports, the final check and
the publishing gate (``pipeline/invariants.py``, over every day) in the
nightly order, writing the would-be data branch to <folder>/data-branch
and the would-be release files to <folder>/releases/<tag>/. Works on the
current, incomplete day too: the check that the collector's digest equals the
archive's is skipped, because new fetches keep arriving while the day is read.

``--replay`` skips the collector and the archive step. The folder must already
hold ``data-branch/`` (a copy of the published data branch) and
``releases/<tag>/`` (the published release files). With ``--from-raw``
everything derived from the raw archive is removed first, so every later
stage is rebuilt from the raw files; comparing the result with the published
copy (``diff -r``) measures what a code change does to the figures.

``--now`` fixes the clock every stage stamps its output with (``OITD_NOW``,
see ``pipeline.common.utcnow``). Two replays with the same ``--now`` over the
same inputs write the same bytes, so ``scripts/replay_diff.py`` shows exactly
what a change in the code did, and nothing else.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import runpy
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline.archive.build as archive_build
import pipeline.release as rel
from pipeline.common import local_release_file

RAW_KEPT = ("manifest/raw.json", "README.md")


def forget_derived(data: Path, store: Path) -> None:
    """Keep only the raw manifest, the README and the raw release files."""
    for path in sorted(data.rglob("*"), reverse=True):
        if path.is_file() and path.relative_to(data).as_posix() not in RAW_KEPT:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for tag in store.iterdir():
        if not tag.name.startswith("raw-"):
            shutil.rmtree(tag) if tag.is_dir() else tag.unlink()
        else:
            for f in tag.glob("SHA256SUMS-*"):
                f.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the nightly chain locally.")
    ap.add_argument("day", nargs="?", help="service day to pull from the collector")
    ap.add_argument("root", nargs="?", type=Path, help="empty folder to write into")
    ap.add_argument("--replay", type=Path, help="folder with data-branch/ and releases/")
    ap.add_argument("--from-raw", action="store_true", help="with --replay: rebuild all stages")
    ap.add_argument("--max-days", type=int, help="days per stage and run (stage default: 7)")
    ap.add_argument("--reference-dir", help="timetable reference folder for the static stage")
    ap.add_argument("--pin-overrides", help="schedule pin overrides file for the static stage")
    ap.add_argument("--observations", help="field observations file for the field check")
    ap.add_argument("--now", help="fix the clock at this ISO time, e.g. 2026-12-02T09:00:00+00:00")
    args = ap.parse_args(argv)
    if bool(args.replay) == bool(args.day and args.root):
        ap.error("give either a day and a folder, or --replay <folder>")
    day, root = args.day, args.replay or args.root
    if args.now:
        os.environ["OITD_NOW"] = args.now
    store = root / "releases"
    if args.replay and args.from_raw:
        forget_derived(root / "data-branch", store)

    def ensure_release(tag, repo, title, notes):
        (store / tag).mkdir(parents=True, exist_ok=True)

    def upload(tag, repo, out, files, content_names=True):
        for f in files:
            dst = store / tag / f["name"]
            shutil.copy(out / f["name"], dst)
            assert dst.stat().st_size == f["bytes"], f["name"]

    def fetch(tag, repo, name, dest, sha256):
        path = dest / name
        if not (path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256):
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy(local_release_file(store, tag, name, sha256), path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256, name
        return path

    def build_day(collector, d, out):  # without the digest check (see docstring)
        rows = archive_build.merge_rows(collector.fetch_log(d), list(collector.raw(d)))
        return archive_build.write_day(d, rows, collector.static(d), out)

    def assets(tag, repo):
        return {f.name: f.stat().st_size for f in (store / tag).glob("*")}

    def set_notes(tag, repo, notes):
        (store / f"{tag}.notes.md").write_text(notes)

    rel.ensure_release, rel.upload, rel.fetch, rel.assets = ensure_release, upload, fetch, assets
    rel.set_notes = set_notes
    archive_build.build_day = build_day

    data, work = root / "data-branch", root / "work"
    static_args = [
        x
        for flag, value in (("--reference-dir", args.reference_dir),
                            ("--pin-overrides", args.pin_overrides))
        if value
        for x in (flag, value)
    ]  # fmt: skip
    field_args = ["--observations", args.observations] if args.observations else []
    steps = [
        ("pipeline.archive", ["--date", day, "--release", "--manifest",
                              f"{data}/manifest/raw.json", "--out", f"{work}/files"]),
        ("pipeline.tables", ["--pending", "--release", "--raw-manifest",
                             f"{data}/manifest/raw.json", "--manifest",
                             f"{data}/manifest/parsed.json", "--raw-dir", f"{work}/files",
                             "--out", f"{work}/tables"]),
        ("pipeline.gtfs", ["--raw-manifest", f"{data}/manifest/raw.json", "--manifest",
                           f"{data}/manifest/static.json", "--raw-dir", f"{work}/files",
                           "--tables-dir", f"{work}/tables", *static_args,
                           "--crosswalk-dir", f"{data}/crosswalk", "--timepoints-dir",
                           f"{data}/timepoints"]),
        ("pipeline.events", ["--pending", "--release", "--data", str(data), "--raw-dir",
                             f"{work}/files", "--tables-dir", f"{work}/tables", "--out",
                             f"{work}/events"]),
        ("pipeline.events.field", ["score", "--data", str(data), "--tables-dir",
                                   f"{work}/events", *field_args]),
        ("pipeline.quality", ["--pending", "--data", str(data), "--raw-dir", f"{work}/files",
                              "--tables-dir", f"{work}/tables", "--events-dir",
                              f"{work}/events"]),
        ("pipeline.periods", ["--data", str(data)]),
        ("pipeline.compare", ["--data", str(data)]),
        ("pipeline.monthly", ["--data", str(data), "--release", "--work", f"{work}/monthly"]),
        ("pipeline.datapackage", ["--data", str(data)]),
        ("pipeline.check", ["--data", str(data)]),
        ("pipeline.invariants", ["--data", str(data), "--releases", str(store),
                                 "--days", "0"]),
    ]  # fmt: skip
    if args.replay:
        steps = steps[1:]
    if args.max_days:
        for module, argv in steps:
            if module in ("pipeline.tables", "pipeline.events", "pipeline.quality"):
                argv += ["--max-days", str(args.max_days)]
    for module, argv in steps:
        print(f"--- {module}")
        sys.argv = [module, *argv]
        try:
            runpy.run_module(module, run_name="__main__")
        except SystemExit as exc:
            if exc.code:
                print(f"{module} exited with {exc.code}")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
