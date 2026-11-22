"""Helpers shared by every stage: file hashes, manifest entries, JSON files,
and the order in which a stage takes its days."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Container, Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pipeline.config import REPO
from pipeline.records import FileEntry, Manifest

# One row of a table read from a file or about to be written to one.
Row = dict[str, Any]


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


def file_entry(path: Path) -> FileEntry:
    """The manifest record of one published file."""
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def sha_of(entry: Mapping[str, Any], name: str) -> str:
    """The recorded SHA-256 of file ``name`` in a manifest entry."""
    return next(f["sha256"] for f in entry["files"] if f["name"] == name)


def load_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def empty_manifest() -> Manifest:
    """A manifest before its first day."""
    return {"version": 1, "repo": REPO, "days": {}}


def save_manifest(path: Path, manifest: Manifest | dict[str, Any]) -> None:
    """Write a manifest with its days in date order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["days"] = dict(sorted(manifest["days"].items()))
    path.write_text(json.dumps(manifest, indent=1, default=str) + "\n")


class DayFiles:
    """The day files of one folder (``<folder>/<date>.json``), each read and
    parsed at most once in a run.

    A stage decides which days to rebuild, writes those days, then builds its
    daily table and its pooled figures; every one of those steps reads the
    day files. Passing one ``DayFiles`` along lets them share a single read,
    so the work stays level as the archive grows by a file a day. What a
    stage writes through ``write`` is kept as it would be read back, so a
    later step sees exactly the published text. Callers must not change the
    dictionaries they are given."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._parsed: dict[str, dict[str, Any]] = {}

    def days(self) -> list[str]:
        """Dates that have a file, oldest first."""
        return sorted(p.stem for p in self.folder.glob("*.json"))

    def read(self, day: str) -> dict[str, Any] | None:
        """The day's file, or None if there is none."""
        if day not in self._parsed:
            path = self.folder / f"{day}.json"
            if not path.exists():
                return None
            self._parsed[day] = json.loads(path.read_text(encoding="utf-8"))
        return self._parsed[day]

    def all(self) -> list[dict[str, Any]]:
        """Every day's file, oldest first."""
        return [m for m in map(self.read, self.days()) if m is not None]

    def write(self, day: str, text: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{day}.json").write_text(text, encoding="utf-8")
        self._parsed[day] = json.loads(text)


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
