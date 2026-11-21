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
"""

from __future__ import annotations

import csv
import hashlib
import io
from typing import Any

from pipeline.gtfs.zipread import ZipContents

IGNORED_FILES = ("feed_info.txt",)


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


def pin_days(
    registry: dict[str, Any], days: list[str], previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Pin each day to one version per source (see module docstring).

    A pin already made (``previous``, the registry's earlier ``days``) is
    kept: figures published for a day were computed with that version, and a
    later download must not move the day to another one."""
    previous = previous or {}
    versions = registry.get("versions", {})
    downloads = sorted(registry.get("downloads", {}).values(), key=lambda d: d["requested_at"])
    sources = sorted({d["source"] for d in downloads})
    out: dict[str, Any] = {}
    for day in sorted(days):
        out[day] = {}
        for source in sources:
            kept = (previous.get(day) or {}).get(source)
            if kept and kept.get("version"):
                out[day][source] = kept
                continue
            mine = [d for d in downloads if d["source"] == source]
            today = [d for d in mine if d["day"] == day and d["complete"]]
            if today:
                pinned = today[0]["version"]
                out[day][source] = {
                    "version": pinned,
                    "basis": "complete download that day",
                    "changed_during_day": len({d["version"] for d in today}) > 1,
                }
                continue
            partial = [
                match_partial(d["file_hashes"], versions, source)
                for d in mine
                if d["day"] == day and not d["complete"]
            ]
            partial = [p for p in partial if p]
            if partial:
                out[day][source] = {
                    "version": partial[0],
                    "basis": "incomplete download that day, matched to a complete version",
                    "changed_during_day": len(set(partial)) > 1,
                }
                continue
            before = [d for d in mine if d["day"] < day and d["complete"]]
            out[day][source] = {
                "version": before[-1]["version"] if before else None,
                "basis": (
                    f"carried forward from {before[-1]['day']}"
                    if before
                    else "no complete version yet"
                ),
                "changed_during_day": False,
            }
    return out
