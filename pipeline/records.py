"""The shapes of the records the stages hand to each other.

Each stage keeps a manifest on the ``data`` branch: one entry per service day
that says which files it published, from what, with which version of the
code. The next stage reads that entry to find its input and to decide whether
its own output is out of date. These are the fields of those entries, written
down once so a reader, and the type checker, can see what each stage promises
the next. At run time they are plain dictionaries, exactly as stored in the
JSON files; ``docs/codebook.md`` describes each field for people who use the
data.

``total=False`` marks records whose fields are added step by step: an entry
exists in memory before its files are uploaded and gains ``release_tag`` and
its timestamp once they are.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class FileEntry(TypedDict):
    """One published file. ``name`` is the file's plain name, the one every
    stage uses on disk (``stop-events-2026-11-20.parquet``). ``asset`` is the
    name of the file in its Release when that differs: files uploaded since
    November 28, 2026 carry the first 8 characters of their SHA-256
    (``stop-events-2026-11-20-1a2b3c4d.parquet``), so a rebuild never
    overwrites a file a published manifest still names."""

    name: str
    bytes: int
    sha256: str
    asset: NotRequired[str]


class RawDay(TypedDict, total=False):
    """``manifest/raw.json``: one archived service day."""

    service_date: str
    fetch_rows: int
    raw_rows: int
    digest: str
    ok_rows: int
    first_fetch_id: int | None
    last_fetch_id: int | None
    static: list[dict[str, Any]]
    files: list[FileEntry]
    release_tag: str
    archived_at: str
    code_sha: str | None


class ParsedDay(TypedDict, total=False):
    """``manifest/parsed.json``: the tables parsed from one day's archive."""

    service_date: str
    tables_version: str
    rows: dict[str, int]
    fetches: dict[str, dict[str, int]]
    duplicate_keys: dict[str, int]
    files: list[FileEntry]
    raw_sha256: str
    release_tag: str
    built_at: str
    code_sha: str | None


class EventsDay(TypedDict, total=False):
    """``manifest/events.json``: the stop events inferred for one day."""

    service_date: str
    events_version: str
    ontime_version: str
    validate_version: str
    service_expected: bool
    passio_version: str
    timepoints: str
    vehicles_sha256: str
    rows: dict[str, int]
    files: list[FileEntry]
    release_tag: str
    built_at: str
    code_sha: str | None


class Pin(TypedDict, total=False):
    """The schedule version one source is held to on one day."""

    version: str | None
    basis: str
    changed_during_day: bool
    chosen_by: str
    candidates: list[dict[str, Any]]
    pin_version: str
    override: dict[str, str]


class DayPins(TypedDict, total=False):
    """``manifest/static.json`` ``days``: everything fixed for one day. The
    two pins are set when the day's schedule downloads are read; the folder
    names are added as the crosswalk and the timepoint list are written."""

    passio: Pin
    trillium: Pin
    crosswalk: str | None
    timepoints: str | None


class StaticDownload(TypedDict, total=False):
    """One download of a static GTFS zip."""

    static_fetch_id: int
    source: str
    day: str
    requested_at: str
    release_tag: str
    file: str
    sha256: str
    complete: bool
    problems: list[str]
    missing: list[str]
    version: str | None
    content_sha256: str | None


class Manifest(TypedDict, total=False):
    """A stage's manifest file."""

    version: int
    repo: str
    days: dict[str, Any]


class StaticRegistry(TypedDict, total=False):
    """``manifest/static.json``."""

    version: int
    repo: str
    versions: dict[str, dict[str, Any]]
    downloads: dict[str, StaticDownload]
    days: dict[str, DayPins]
