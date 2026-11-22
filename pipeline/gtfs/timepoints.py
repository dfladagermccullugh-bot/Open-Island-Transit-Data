"""True timepoints, from Island Transit's printed timetables.

The stops printed as rows or columns in the agency's public timetables are its
published timepoints. They are kept in ``reference/``, one file per service
change (``timetable-timepoints-<effective date>.csv``), read from the service
guide PDFs, with reviewed name links in ``timetable-overrides-<date>.csv``.

Each printed stop name is matched to Passio stops on the same route:

1. a reviewed override, which may also mark the name as unresolved; else
2. the Passio stop on that route with the most similar name, if the
   similarity score is at least ``THRESHOLD``; else unresolved.

When several Passio stops share the best name (usually one per direction),
the ones served by the Passio trips that contain the most printed stops of the
same timetable, in the printed order, are kept. A timepoint is then a (Passio route, Passio stop)
pair. Unresolved names are reported, never guessed.

The Trillium feed's own timepoint flags are kept only for comparison
(``pipeline/gtfs/crosswalk.py``); that feed's schedule is out of date.
"""

from __future__ import annotations

import csv
import difflib
import itertools
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from pipeline.common import Row
from pipeline.gtfs.tables import rows as _rows
from pipeline.gtfs.zipread import ZipContents

TIMEPOINTS_VERSION = "timepoints/0.1.0"  # matching; day-type selection is applied by events
REFERENCE_DIR = Path(__file__).resolve().parents[2] / "reference"
THRESHOLD = 0.6


_WORDS = {"st": "street", "rd": "road", "ave": "avenue", "dr": "drive", "blvd": "boulevard",
          "elem": "elementary", "pr": "park ride", "p": "park", "r": "ride", "hs": "high school",
          "ln": "lane"}  # fmt: skip
_DROP = {"sw", "se", "nw", "ne", "n", "s", "e", "w", "at", "the"}


def norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and ").replace("'", ""))
    return " ".join(_WORDS.get(w, w) for w in s.split() if w not in _DROP)


def score(a: str, b: str) -> float:
    """Name similarity in [0, 1]: word overlap and character similarity."""
    x, y = set(norm(a).split()), set(norm(b).split())
    jaccard = len(x & y) / len(x | y) if x | y else 0.0
    return 0.6 * jaccard + 0.4 * difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


# --- reference files ------------------------------------------------------------


def references(folder: Path = REFERENCE_DIR) -> dict[date, tuple[Path, Path]]:
    """Effective date -> (printed timepoints file, overrides file)."""
    out = {}
    for f in folder.glob("timetable-timepoints-????-??-??.csv"):
        effective = date.fromisoformat(f.stem.removeprefix("timetable-timepoints-"))
        out[effective] = (f, folder / f"timetable-overrides-{effective.isoformat()}.csv")
    return dict(sorted(out.items()))


def reference_for(day: date, folder: Path = REFERENCE_DIR) -> date | None:
    """The latest timetable in effect on ``day``, or None."""
    eligible = [d for d in references(folder) if d <= day]
    return eligible[-1] if eligible else None


def reference_of(key: str | None) -> str | None:
    """The timetable date in a timepoints folder name
    (``<passio version>__timetable-<date>-<hash>``), or None."""
    m = re.search(r"__timetable-(\d{4}-\d{2}-\d{2})-", key or "")
    return m.group(1) if m else None


def load_reference(effective: date, folder: Path = REFERENCE_DIR) -> tuple[list[Row], dict]:
    printed_path, overrides_path = references(folder)[effective]
    with printed_path.open(encoding="utf-8", newline="") as fh:
        printed = list(csv.DictReader(fh))
    overrides = {}
    if overrides_path.exists():
        with overrides_path.open(encoding="utf-8", newline="") as fh:
            overrides = {(o["route_short_name"], o["printed_stop"]): o for o in csv.DictReader(fh)}
    return printed, overrides


# --- matching -----------------------------------------------------------------------


def _fit(seq: dict[str, int], printed_ids: list[list[str]]) -> tuple[int, int]:
    """How well one trip fits a printed timetable: (printed stops it serves,
    consecutive pairs of them in the printed order)."""
    seqs = [min(seq[s] for s in ids if s in seq) for ids in printed_ids if set(ids) & seq.keys()]
    return len(seqs), sum(a < b for a, b in itertools.pairwise(seqs))


def match(passio: ZipContents, printed: list[Row], overrides: dict) -> list[Row]:
    """One row per printed stop and Passio stop it was matched to (one row
    with empty ids if unresolved)."""
    short = {r["route_id"]: r.get("route_short_name", "") for r in _rows(passio, "routes.txt")}
    trip_route = {t["trip_id"]: t["route_id"] for t in _rows(passio, "trips.txt")}
    name = {s["stop_id"]: s.get("stop_name", "") for s in _rows(passio, "stops.txt")}
    trip_stops: dict[str, set[str]] = defaultdict(set)
    trip_seq: dict[str, dict[str, int]] = defaultdict(dict)
    for st in _rows(passio, "stop_times.txt"):
        trip_stops[st["trip_id"]].add(st["stop_id"])
        seq = int(st.get("stop_sequence") or 0)
        first = trip_seq[st["trip_id"]].get(st["stop_id"])
        trip_seq[st["trip_id"]][st["stop_id"]] = seq if first is None else min(first, seq)
    by_short: dict[str, list[str]] = defaultdict(list)
    for tid, rid in trip_route.items():
        by_short[short.get(rid, "")].append(tid)

    blocks: dict[tuple[str, str, str], list[Row]] = defaultdict(list)
    for p in printed:
        blocks[(p["route_short_name"], p["day_type"], p["direction"])].append(p)

    out = []
    for (route, _day, _direction), stops in blocks.items():
        trips = by_short.get(route, [])
        served = set().union(*(trip_stops[t] for t in trips))
        cands: dict[str, tuple[list[str], str]] = {}
        for p in stops:
            o = overrides.get((route, p["printed_stop"]))
            if o is not None:
                listed = [x for x in o["passio_stop_ids"].split(";") if x]
                ids = [x for x in listed if x in name]
                if ids:
                    how = "override"
                elif listed:
                    how = "unresolved: override stop ids are not in this Passio version"
                else:
                    how = o["note"] or "unresolved"
                cands[p["printed_stop"]] = (ids, how)
                continue
            scored = sorted(((score(p["printed_stop"], name[s]), s) for s in served), reverse=True)
            if scored and scored[0][0] >= THRESHOLD:
                best = name[scored[0][1]]
                ids = sorted(s for _, s in scored if name[s] == best)
                cands[p["printed_stop"]] = (ids, f"name {scored[0][0]:.2f}")
            else:
                cands[p["printed_stop"]] = ([], "unresolved: no name match")

        # The trips containing the most printed stops, in the printed order,
        # decide between same-named stops.
        printed_ids = [cands[p["printed_stop"]][0] for p in stops]
        scores = {t: _fit(trip_seq[t], printed_ids) for t in trips}
        top = max(scores.values(), default=(0, 0))
        allowed = set().union(*(trip_stops[t] for t, f in scores.items() if top[0] and f == top))
        for p in stops:
            ids, how = cands[p["printed_stop"]]
            chosen = [s for s in ids if s in allowed] or ids
            for sid in chosen or [""]:
                # Every Passio route with this short name that serves the stop.
                rids = sorted({trip_route[t] for t in trips if sid in trip_stops[t]}) or [""]
                for rid in rids:
                    out.append(
                        {
                            **{
                                k: p[k]
                                for k in (
                                    "route_short_name",
                                    "day_type",
                                    "direction",
                                    "order",
                                    "printed_stop",
                                )
                            },
                            "passio_route_id": rid,
                            "passio_stop_id": sid,
                            "passio_stop_name": name.get(sid, ""),
                            "match": how,
                        }
                    )
    return out


def timepoint_pairs(matched: list[Row], day: date | None = None) -> set[tuple[str, str]]:
    """(Passio route id, Passio stop id) pairs that are true timepoints. A
    printed stop counts on every Passio route with the timetable's short name
    that serves it (the three NAS Whidbey routes share one timetable).

    With ``day``, a route that has a printed timetable for that kind of day
    (weekday, Saturday, Sunday) uses only that timetable's stops; a route
    with none for it falls back to all of its printed stops. Without
    ``day``, every printed stop counts."""
    rows = [r for r in matched if r["passio_stop_id"]]
    if day is None:
        return {(r["passio_route_id"], r["passio_stop_id"]) for r in rows}
    by_route: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_route[r["passio_route_id"]].append(r)
    out = set()
    for route, mine in by_route.items():
        todays = [r for r in mine if day_type_applies(r["day_type"], day)]
        out |= {(route, r["passio_stop_id"]) for r in (todays or mine)}
    return out


def day_type_applies(day_type: str, day: date) -> bool:
    """Whether a printed timetable's day label ("weekday", "weekday AM",
    "saturday", "weekend", ...) covers ``day``."""
    label = day_type.strip().lower()
    weekday = day.weekday()
    if weekday < 5:
        return label.startswith("weekday") or label in ("daily", "every day")
    name = "saturday" if weekday == 5 else "sunday"
    return label.startswith((name, "weekend")) or label in ("daily", "every day")


def summarize(matched: list[Row]) -> dict[str, Any]:
    names = {(r["route_short_name"], r["printed_stop"]) for r in matched}
    resolved = {(r["route_short_name"], r["printed_stop"]) for r in matched if r["passio_stop_id"]}
    return {
        "printed_names": len(names),
        "resolved_names": len(resolved),
        "unresolved": sorted(
            {
                (r["route_short_name"], r["printed_stop"], r["match"])
                for r in matched
                if (r["route_short_name"], r["printed_stop"]) not in resolved
            }
        ),
        "timepoint_route_stops": len(timepoint_pairs(matched)),
    }


def build(passio: ZipContents, effective: date, folder: Path = REFERENCE_DIR) -> dict[str, Any]:
    printed, overrides = load_reference(effective, folder)
    matched = match(passio, printed, overrides)
    return {"matched": matched, "summary": {"timepoints_version": TIMEPOINTS_VERSION,
                                            "reference": effective.isoformat(),
                                            **summarize(matched)}}  # fmt: skip
