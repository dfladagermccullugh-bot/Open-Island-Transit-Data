"""Static GTFS versions, identified by content, and one pinned per service day.

Passio regenerates its static GTFS every day, and its ``feed_info.txt`` dates
roll forward each time even when nothing else changes. Trillium's
``feed_version`` is the time the file was generated. A version is therefore
identified by the SHA-256 of every file except ``feed_info.txt``; the
``feed_info`` values are recorded alongside but do not define the version.

A download is *complete* when the zip is intact and has every required GTFS
file. Passio sometimes serves a zip without its calendar files and central
directory. Such a download does not define a version, but it is matched to a
complete version when every file it does contain is identical to that
version's.

Each service day is pinned, per source, to one version:

1. the first complete download made that day (Pacific date); or
2. if there is none, a version matched by an incomplete download that day; or
3. if there is none, the most recent complete download before that day.

The rule used is recorded as ``basis``. ``changed_during_day`` is true when
complete downloads on the same day disagree.

When they disagree, the schedule changed during that day, and the first
download (made at night) may still be the old one. The day is then pinned to
the version whose trips the live feed reported that day (``choose``), and
the figures compared are kept in ``candidates``. Until the day's parsed
tables exist the day has no pin and waits.

A pin, once made, is kept: figures published for a day were computed with
that version. The one exception is a reviewed row in
``reference/static-pin-overrides.csv``, which replaces the pin and is
recorded with what it replaced (``override``).
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pipeline.gtfs.zipread import ZipContents

IGNORED_FILES = ("feed_info.txt",)
PIN_VERSION = "pin/0.2.0"  # written on pins made by the live-feed rule or an override
OVERRIDES = Path(__file__).resolve().parents[2] / "reference" / "static-pin-overrides.csv"

# evidence(day, source, versions) -> one record per version (see ``choose``),
# an empty list if the source has no live feed to compare with, or None if
# the day's parsed tables are not available yet.
Evidence = Callable[[str, str, list[str]], list[dict[str, Any]] | None]


def fingerprint(source: str, contents: ZipContents) -> dict[str, Any]:
    """Describe one download: file hashes, completeness, version id."""
    files = {
        name: hashlib.sha256(body).hexdigest()
        for name, body in sorted(contents.files.items())
        if name not in IGNORED_FILES
    }
    content = hashlib.sha256(
        "\n".join(f"{name}:{digest}" for name, digest in files.items()).encode()
    ).hexdigest()
    feed_info = None
    if "feed_info.txt" in contents.files:
        text = contents.files["feed_info.txt"].decode("utf-8-sig", errors="replace")
        feed_info = next(csv.DictReader(io.StringIO(text)), None)
    return {
        "complete": contents.is_valid_gtfs,
        "problems": contents.problems,
        "missing": contents.missing_required,
        "content_sha256": content,
        "version": f"{source}-{content[:12]}" if contents.is_valid_gtfs else None,
        "files": files,
        "feed_info": feed_info,
    }


def match_partial(files: dict[str, str], versions: dict[str, Any], source: str) -> str | None:
    """The complete version whose files include all of ``files`` unchanged.
    If several do, the most recently seen one."""
    hits = [
        (v["last_seen"], vid)
        for vid, v in versions.items()
        if v["source"] == source
        and files
        and all(v["files"].get(name) == digest for name, digest in files.items())
    ]
    return max(hits)[1] if hits else None


def add_download(registry: dict[str, Any], download: dict[str, Any], fp: dict[str, Any]) -> None:
    """Record a download and, if complete, its version."""
    versions = registry.setdefault("versions", {})
    entry = {**download, **{k: fp[k] for k in ("complete", "problems", "missing", "version")}}
    entry["content_sha256"] = fp["content_sha256"]
    if fp["complete"]:
        v = versions.get(fp["version"])
        if v is None:
            v = versions[fp["version"]] = {
                "source": download["source"],
                "content_sha256": fp["content_sha256"],
                "files": fp["files"],
                "feed_info": fp["feed_info"],
                "first_seen": download["requested_at"],
                "last_seen": download["requested_at"],
                "first_day": download["day"],
                "example": {k: download[k] for k in ("release_tag", "file", "sha256")},
                "downloads": [],
            }
        v["first_seen"] = min(v["first_seen"], download["requested_at"])
        v["last_seen"] = max(v["last_seen"], download["requested_at"])
        v["first_day"] = min(v["first_day"], download["day"])
        v["downloads"] = sorted({*v["downloads"], download["static_fetch_id"]})
    else:
        entry["file_hashes"] = fp["files"]
    registry.setdefault("downloads", {})[str(download["static_fetch_id"])] = entry


def load_overrides(path: Path = OVERRIDES) -> dict[tuple[str, str], dict[str, str]]:
    """(service date, source) -> reviewed row of the overrides file."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {
            (r["service_date"].strip(), r["source"].strip()): {k: v.strip() for k, v in r.items()}
            for r in csv.DictReader(fh)
            if r.get("service_date", "").strip()
        }


def choose(candidates: list[dict[str, Any]]) -> tuple[str, str]:
    """The version a changed day ran on, and what decided it.

    ``candidates`` are in download order, one per version, each with
    ``scheduled_trips_seen`` (trips that version schedules for the day and
    the live feed reported) and ``live_trip_ids_in_version`` (trip ids the
    live feed reported that the version knows at all). The first decides; the
    second breaks a tie; if both tie, the first download stays."""
    for key, label in (
        ("scheduled_trips_seen", "scheduled trips seen in the live feed"),
        ("live_trip_ids_in_version", "live trip ids known to the version"),
    ):
        best = max(c[key] for c in candidates)
        leaders = [c for c in candidates if c[key] == best]
        if len(leaders) == 1:
            return leaders[0]["version"], label
        candidates = leaders
    return candidates[0]["version"], "tie: first complete download that day"


def _pin_today(
    today: list[dict[str, Any]], day: str, source: str, evidence: Evidence | None
) -> dict[str, Any]:
    """Pin a day that has complete downloads."""
    order = list(dict.fromkeys(d["version"] for d in today))
    plain = {
        "version": order[0],
        "basis": "complete download that day",
        "changed_during_day": len(order) > 1,
    }
    if len(order) == 1 or evidence is None:
        return plain
    found = evidence(day, source, order)
    if found is None:
        return {
            "version": None,
            "basis": "complete downloads that day disagree; waiting for the day's parsed tables",
            "changed_during_day": True,
        }
    if not found:
        return plain
    version, chosen_by = choose(found)
    return {
        "version": version,
        "basis": "complete downloads that day disagreed; chosen by match to the live feed",
        "changed_during_day": True,
        "chosen_by": chosen_by,
        "candidates": found,
        "pin_version": PIN_VERSION,
    }


def _overridden(
    pin: dict[str, Any], row: dict[str, str] | None, versions: dict[str, Any], source: str
) -> tuple[dict[str, Any], str | None]:
    """Apply a reviewed override row to ``pin``; return the pin and a problem."""
    if row is None or row["version"] == pin.get("version"):
        return pin, None
    known = versions.get(row["version"])
    if known is None or known["source"] != source:
        return pin, (
            f"override for {row['service_date']} {source} names {row['version']}, "
            "which is not a version in the registry"
        )
    return {
        "version": row["version"],
        "basis": f"override: {row['reason']}",
        "changed_during_day": pin.get("changed_during_day", False),
        "override": {
            "reason": row["reason"],
            "reviewed_on": row["reviewed_on"],
            "replaced_version": pin.get("version"),
            "replaced_basis": pin.get("basis"),
        },
        "pin_version": PIN_VERSION,
    }, None


def pin_days(
    registry: dict[str, Any],
    days: list[str],
    previous: dict[str, Any] | None = None,
    *,
    evidence: Evidence | None = None,
    overrides: dict[tuple[str, str], dict[str, str]] | None = None,
    problems: list[str] | None = None,
) -> dict[str, Any]:
    """Pin each day to one version per source (see module docstring).

    A pin already made (``previous``, the registry's earlier ``days``) is
    kept: figures published for a day were computed with that version, and a
    later download must not move the day to another one. Only a row of
    ``overrides`` moves it. An override that cannot be applied is described
    in ``problems`` and leaves the pin as it was."""
    previous = previous or {}
    overrides = overrides or {}
    versions = registry.get("versions", {})
    downloads = sorted(registry.get("downloads", {}).values(), key=lambda d: d["requested_at"])
    sources = sorted({d["source"] for d in downloads})
    out: dict[str, Any] = {}
    for day in sorted(days):
        out[day] = {}
        for source in sources:
            pin = _pin(day, source, previous, downloads, versions, evidence)
            pin, problem = _overridden(pin, overrides.get((day, source)), versions, source)
            if problem and problems is not None:
                problems.append(problem)
            out[day][source] = pin
    return out


def _pin(
    day: str,
    source: str,
    previous: dict[str, Any],
    downloads: list[dict[str, Any]],
    versions: dict[str, Any],
    evidence: Evidence | None,
) -> dict[str, Any]:
    kept = (previous.get(day) or {}).get(source)
    if kept and kept.get("version"):
        return kept
    mine = [d for d in downloads if d["source"] == source]
    today = [d for d in mine if d["day"] == day and d["complete"]]
    if today:
        return _pin_today(today, day, source, evidence)
    partial = [
        match_partial(d["file_hashes"], versions, source)
        for d in mine
        if d["day"] == day and not d["complete"]
    ]
    partial = [p for p in partial if p]
    if partial:
        return {
            "version": partial[0],
            "basis": "incomplete download that day, matched to a complete version",
            "changed_during_day": len(set(partial)) > 1,
        }
    before = [d for d in mine if d["day"] < day and d["complete"]]
    return {
        "version": before[-1]["version"] if before else None,
        "basis": f"carried forward from {before[-1]['day']}"
        if before
        else "no complete version yet",
        "changed_during_day": False,
    }
