"""Upload files to GitHub Releases with the ``gh`` command, and confirm them.

Since November 28, 2026 every parsed table and stop-event file is uploaded
under a content-addressed name, its plain name with the first 8 characters
of its SHA-256 before the extension (``stop-events-2026-11-20-1a2b3c4d.parquet``),
and the manifest entry records it as ``asset``. The raw archive keeps plain
names: a day's raw files are uploaded before the manifest lists the day and
never rebuilt after, and the collector's prune confirms them by plain name.
A rebuild therefore adds a new file instead of overwriting the one a
published manifest names, and a run that fails halfway cannot break a
published file. Files uploaded before then keep
their plain names. ``fetch`` finds either form.

Files that no published manifest names any more are deleted only after the
data branch has moved to the new manifests:

    python -m pipeline.release prune --data data-branch --also <previous data>

``prune`` keeps every file named by any of the data folders it is given (the
one just promoted and the one before it, which the live site was built
from), never touches a ``raw-*`` release (the only copy of the collector's
responses once it prunes them) and leaves alone any file it cannot tie to a
manifest entry.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from pipeline.common import asset_name, load_json, sha256_file
from pipeline.config import REPO

RETRY_WAITS_S = (5, 20, 60)
# Release tags whose files are never deleted by ``prune``.
PROTECTED_TAGS = ("raw-",)
_CONTENT_SUFFIX = re.compile(r"-[0-9a-f]{8}(?=\.[^.]+$|$)")

_listing: dict[tuple[str, str], dict[str, int]] = {}


def gh(*args: str) -> str:
    """Run ``gh``; retry after a failure (GitHub can briefly report a release
    it has just created as missing); raise with gh's own error message."""
    for attempt in range(len(RETRY_WAITS_S) + 1):
        run = subprocess.run(["gh", *args], capture_output=True, text=True, env=os.environ)
        if run.returncode == 0:
            return run.stdout
        if attempt < len(RETRY_WAITS_S):
            time.sleep(RETRY_WAITS_S[attempt])
    raise RuntimeError(f"gh {' '.join(args[:3])} failed: {run.stderr.strip()[-2000:]}")


def _exists(tag: str, repo: str) -> bool:
    view = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo], capture_output=True, text=True
    )
    return view.returncode == 0


def ensure_release(tag: str, repo: str, title: str, notes: str) -> None:
    if _exists(tag, repo):
        return
    try:
        gh("release", "create", tag, "--repo", repo, "--title", title, "--notes", notes,
           "--latest=false")  # fmt: skip
    except RuntimeError:
        # A create that succeeded but reported an error is retried as
        # "already exists"; only fail if the release is really missing.
        if not _exists(tag, repo):
            raise


def upload(
    tag: str, repo: str, out: Path, files: list[dict[str, Any]], *, content_names: bool = True
) -> None:
    """Upload ``files`` (manifest entries with name, bytes and sha256) from
    ``out``, then confirm every one is in the release at the right size.

    With ``content_names`` (the default) each file goes up under its
    content-addressed name, which is added to its entry as ``asset``; a file
    already in the release under that name and size is not sent again. The
    raw archive and the monthly checksum files keep their plain names
    (``content_names=False``)."""
    have = assets(tag, repo)
    with tempfile.TemporaryDirectory() as tmp:
        send = []
        for f in files:
            if content_names:
                f["asset"] = asset_name(f["name"], f["sha256"])
                if have.get(f["asset"]) == f["bytes"]:
                    continue
                path = Path(tmp) / f["asset"]
                try:
                    os.link(out / f["name"], path)
                except OSError:
                    shutil.copy(out / f["name"], path)
                send.append(str(path))
            else:
                send.append(str(out / f["name"]))
        if send:
            gh("release", "upload", tag, *send, "--repo", repo, "--clobber")
            _listing.pop((tag, repo), None)
    sizes = assets(tag, repo)
    for f in files:
        name = f.get("asset", f["name"]) if content_names else f["name"]
        if sizes.get(name) != f["bytes"]:
            raise RuntimeError(f"{name}: upload not confirmed ({sizes.get(name)} bytes)")


def set_notes(tag: str, repo: str, notes: str) -> None:
    gh("release", "edit", tag, "--repo", repo, "--notes", notes)


def assets(tag: str, repo: str) -> dict[str, int]:
    """Name -> size in bytes of every file in a release (none if it has no
    files yet)."""
    out = json.loads(gh("release", "view", tag, "--repo", repo, "--json", "assets"))
    sizes = {a["name"]: a["size"] for a in out["assets"]}
    _listing[(tag, repo)] = sizes
    return sizes


def _listed(tag: str, repo: str) -> dict[str, int]:
    """``assets``, read once per release and process."""
    return _listing.get((tag, repo)) or assets(tag, repo)


def fetch(tag: str, repo: str, name: str, dest: Path, sha256: str) -> Path:
    """Download one release file into ``dest`` as ``name`` (unless a matching
    copy is already there) and check its SHA-256. The file is looked for
    under its content-addressed name first, then under its plain name."""
    path = dest / name
    if path.exists() and sha256_file(path) == sha256:
        return path
    dest.mkdir(parents=True, exist_ok=True)
    named = asset_name(name, sha256)
    remote = named if named in _listed(tag, repo) else name
    gh("release", "download", tag, "--repo", repo, "--pattern", remote, "--dir", str(dest),
       "--clobber")  # fmt: skip
    if remote != name:
        os.replace(dest / remote, path)
    if sha256_file(path) != sha256:
        raise RuntimeError(f"{name}: downloaded file does not match the manifest's SHA-256")
    return path


# --- removing files no published manifest names -------------------------------


def plain_name(asset: str) -> str:
    """The plain name of a Release file: its name without the SHA-256 suffix."""
    return _CONTENT_SUFFIX.sub("", asset, count=1)


def named_files(data: Path) -> dict[str, dict[str, set[str]]]:
    """Release tag -> plain name -> the names in the release that the data
    folder's manifests and monthly reports point to."""
    from pipeline.common import asset_of

    out: dict[str, dict[str, set[str]]] = {}
    for manifest in ("raw", "parsed", "events"):
        days = (load_json(data / "manifest" / f"{manifest}.json") or {}).get("days", {})
        for entry in days.values():
            tag = entry.get("release_tag")
            for f in entry.get("files", []) if tag else []:
                out.setdefault(tag, {}).setdefault(f["name"], set()).add(asset_of(f))
    for path in sorted((data / "reports").glob("????-??.json")):
        for tag, rel in (load_json(path) or {}).get("releases", {}).items():
            name = rel.get("checksums_file")
            if name:
                out.setdefault(tag, {}).setdefault(name, set()).add(name)
    return out


def superseded(
    keep: list[dict[str, dict[str, set[str]]]], listing: dict[str, dict[str, int]]
) -> dict[str, list[str]]:
    """Release tag -> files that a manifest entry once pointed to and no data
    folder in ``keep`` points to now. A file whose plain name no manifest
    entry has is not ours to judge and stays."""
    out: dict[str, list[str]] = {}
    for tag, files in sorted(listing.items()):
        if tag.startswith(PROTECTED_TAGS):
            continue
        plain: dict[str, set[str]] = {}
        for k in keep:
            for name, named in k.get(tag, {}).items():
                plain.setdefault(name, set()).update(named)
        gone = [
            a
            for a in sorted(files)
            if (a in plain or plain_name(a) in plain)
            and a not in plain.get(a, set()) | plain.get(plain_name(a), set())
        ]
        if gone:
            out[tag] = gone
    return out


def prune_superseded(
    data: Path, also: list[Path], repo: str, *, dry_run: bool = False
) -> dict[str, list[str]]:
    """Delete the files ``superseded`` finds in the releases ``data`` uses."""
    keep = [named_files(d) for d in [data, *also]]
    tags = sorted(set(keep[0]))
    listing = {tag: assets(tag, repo) for tag in tags if not tag.startswith(PROTECTED_TAGS)}
    gone = superseded(keep, listing)
    for tag, names in gone.items():
        for name in names:
            print(f"{'would delete' if dry_run else 'deleting'} {tag}/{name}")
            if not dry_run:
                gh("release", "delete-asset", tag, name, "--repo", repo, "--yes")
    if not gone:
        print("no superseded Release files")
    return gone


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Release housekeeping.")
    sub = ap.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("prune", help="delete Release files no published manifest names")
    pr.add_argument("--data", type=Path, default=Path("data-branch"))
    pr.add_argument(
        "--also",
        type=Path,
        action="append",
        default=[],
        help="another data folder whose files are kept (the previous data commit)",
    )
    pr.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    pr.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    prune_superseded(args.data, args.also, args.repo, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
