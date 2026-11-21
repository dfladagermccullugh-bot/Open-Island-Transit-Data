"""Helpers shared by every stage: file hashes, manifest entries, JSON files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, read in pieces so a large file is not held in memory."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_entry(path: Path) -> dict[str, Any]:
    """The manifest record of one published file."""
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def sha_of(entry: dict[str, Any], name: str) -> str:
    """The recorded SHA-256 of file ``name`` in a manifest entry."""
    return next(f["sha256"] for f in entry["files"] if f["name"] == name)


def load_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
