"""Run every nightly step locally, with GitHub Releases replaced by a folder.

    python scripts/rehearse_nightly.py 2026-11-19 /path/to/empty/folder

Publishes nothing. Pulls the day from the collector, then runs archive,
tables, static GTFS, events, quality, monthly reports and the final check in
the nightly order, writing the would-be data branch to <folder>/data-branch
and the would-be release files to <folder>/releases/<tag>/. Works on the
current, incomplete day too: the check that the collector's digest equals the
archive's is skipped, because new fetches keep arriving while the day is read.
"""

from __future__ import annotations

import hashlib
import runpy
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline.archive.build as archive_build  # noqa: E402
import pipeline.release as rel  # noqa: E402


def main() -> int:
    day, root = sys.argv[1], Path(sys.argv[2])
    store = root / "releases"

    def ensure_release(tag, repo, title, notes):
        (store / tag).mkdir(parents=True, exist_ok=True)

    def upload(tag, repo, out, files):
        for f in files:
            dst = store / tag / f["name"]
            shutil.copy(out / f["name"], dst)
            assert dst.stat().st_size == f["bytes"], f["name"]

    def fetch(tag, repo, name, dest, sha256):
        path = dest / name
        if not (path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256):
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy(store / tag / name, path)
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
    steps = [
        ("pipeline.archive", ["--date", day, "--release", "--manifest",
                              f"{data}/manifest/raw.json", "--out", f"{work}/files"]),
        ("pipeline.tables", ["--pending", "--release", "--raw-manifest",
                             f"{data}/manifest/raw.json", "--manifest",
                             f"{data}/manifest/parsed.json", "--raw-dir", f"{work}/files",
                             "--out", f"{work}/tables"]),
        ("pipeline.gtfs", ["--raw-manifest", f"{data}/manifest/raw.json", "--manifest",
                           f"{data}/manifest/static.json", "--raw-dir", f"{work}/files",
                           "--crosswalk-dir", f"{data}/crosswalk", "--timepoints-dir",
                           f"{data}/timepoints"]),
        ("pipeline.events", ["--pending", "--release", "--data", str(data), "--raw-dir",
                             f"{work}/files", "--tables-dir", f"{work}/tables", "--out",
                             f"{work}/events"]),
        ("pipeline.events.field", ["score", "--data", str(data), "--tables-dir",
                                   f"{work}/tables"]),
        ("pipeline.quality", ["--pending", "--data", str(data), "--raw-dir", f"{work}/files",
                              "--tables-dir", f"{work}/tables"]),
        ("pipeline.monthly", ["--data", str(data), "--release", "--work", f"{work}/monthly"]),
        ("pipeline.check", ["--data", str(data)]),
    ]  # fmt: skip
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
