"""Upload files to GitHub Releases with the ``gh`` command, and confirm them."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

RETRY_WAITS_S = (5, 20, 60)


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


def upload(tag: str, repo: str, out: Path, files: list[dict[str, Any]]) -> None:
    """Upload ``files`` (manifest entries with name and bytes) from ``out``,
    then confirm every one is in the release at the right size."""
    gh(
        "release",
        "upload",
        tag,
        *[str(out / f["name"]) for f in files],
        "--repo",
        repo,
        "--clobber",
    )
    sizes = assets(tag, repo)
    for f in files:
        if sizes.get(f["name"]) != f["bytes"]:
            raise RuntimeError(f"{f['name']}: upload not confirmed ({sizes.get(f['name'])} bytes)")


def set_notes(tag: str, repo: str, notes: str) -> None:
    gh("release", "edit", tag, "--repo", repo, "--notes", notes)


def assets(tag: str, repo: str) -> dict[str, int]:
    """Name -> size in bytes of every file in a release."""
    out = json.loads(gh("release", "view", tag, "--repo", repo, "--json", "assets"))
    return {a["name"]: a["size"] for a in out["assets"]}


def fetch(tag: str, repo: str, name: str, dest: Path, sha256: str) -> Path:
    """Download one release asset into ``dest`` (unless a matching copy is
    already there) and check its SHA-256."""
    path = dest / name
    if not (path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256):
        dest.mkdir(parents=True, exist_ok=True)
        gh(
            "release",
            "download",
            tag,
            "--repo",
            repo,
            "--pattern",
            name,
            "--dir",
            str(dest),
            "--clobber",
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise RuntimeError(f"{name}: downloaded file does not match the manifest's SHA-256")
    return path
