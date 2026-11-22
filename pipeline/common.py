"""Helpers shared by every stage: file hashes, manifest entries, JSON files,
and the order in which a stage takes its days."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Container, Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


def utcnow() -> datetime:
    """The current time in UTC.

    Every stage stamps what it writes (``computed_at``, ``built_at``,
    ``archived_at``) through this one function. Setting ``OITD_NOW`` to an ISO
    time fixes the clock, so two runs over the same inputs write the same
    bytes and a change in the code can be measured with ``diff``."""
    fixed = os.environ.get("OITD_NOW")
    return datetime.fromisoformat(fixed).astimezone(UTC) if fixed else datetime.now(UTC)


def stamp() -> str:
    """``utcnow`` as the text the manifests and day files carry."""
    return utcnow().isoformat(timespec="seconds")


def code_sha() -> str | None:
    """The commit a GitHub run was started from; nothing on a local run."""
    return os.environ.get("GITHUB_SHA")


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


def run_order(days: Iterable[date], processed: Container[str], limit: int) -> list[date]:
    """The days a stage handles in one run, in the order it handles them.

    Days it has never processed come first, oldest first, because a day's
    figures can read the files of the days before it. Days it has processed
    before and must rebuild follow, newest first. So after a version change
    with months of data behind it, yesterday is still published the same
    night, and the backlog is worked off ``limit`` days at a time."""
    unique = set(days)
    new = sorted(day for day in unique if day.isoformat() not in processed)
    again = sorted((day for day in unique if day.isoformat() in processed), reverse=True)
    return (new + again)[:limit]
