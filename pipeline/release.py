"""Upload files to GitHub Releases with the ``gh`` command, and confirm them."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True, env=os.environ
    ).stdout


def ensure_release(tag: str, repo: str, title: str, notes: str) -> None:
    exists = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo], capture_output=True, text=True
    )
    if exists.returncode != 0:
        gh(
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--title",
            title,
            "--notes",
            notes,
            "--latest=false",
        )


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
    assets = json.loads(gh("release", "view", tag, "--repo", repo, "--json", "assets"))["assets"]
    sizes = {a["name"]: a["size"] for a in assets}
    for f in files:
        if sizes.get(f["name"]) != f["bytes"]:
            raise RuntimeError(f"{f['name']}: upload not confirmed ({sizes.get(f['name'])} bytes)")


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
