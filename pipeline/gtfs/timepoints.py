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

Since ``timepoints/0.2.0`` each printed timetable is first tied to the Passio
direction (``direction_id``) whose trips run in the printed order, and names
are matched only among the stops of that direction, so a stop across the road
used by buses going the other way is never chosen. A timepoint is then a
(Passio route, direction, stop) triple (``timepoint_keys``).

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

TIMEPOINTS_VERSION = "timepoints/0.2.0"  # 0.2.0: matched per Passio direction
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


def _order(seq: dict[str, int], printed_ids: list[list[str]]) -> tuple[int, int]:
    """How well one trip runs the way a printed timetable reads: (consecutive
    printed stops it serves in the printed order minus those it serves in
    the opposite order, printed stops it serves)."""
    seqs = [min(seq[s] for s in ids if s in seq) for ids in printed_ids if set(ids) & seq.keys()]
    pairs = list(itertools.pairwise(seqs))
    return sum(a < b for a, b in pairs) - sum(a > b for a, b in pairs), len(seqs)


def _candidates(
    stops: list[Row], route: str, served: set[str], name: dict[str, str], overrides: dict
) -> dict[str, tuple[list[str], str]]:
    """Printed name -> (Passio stops it may be, how they were found), among
    the stops in ``served``. An override lists stops on both sides of the
    road; only those in ``served`` are kept, unless none is."""
    cands: dict[str, tuple[list[str], str]] = {}
    for p in stops:
        o = overrides.get((route, p["printed_stop"]))
        if o is not None:
            listed = [x for x in o["passio_stop_ids"].split(";") if x]
            ids = [x for x in listed if x in name]
            if ids:
                how = "override"
                ids = [x for x in ids if x in served] or ids
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
    return cands


def match(passio: ZipContents, printed: list[Row], overrides: dict) -> list[Row]:
    """One row per printed stop and Passio stop it was matched to (one row
    with empty ids if unresolved).

    Since ``timepoints/0.2.0`` each printed timetable (a route, kind of day
    and printed direction) is first given the Passio direction whose trips
    run the way it reads, and its names are matched only among the stops
    those trips serve. Before, names were matched among the stops of both
    directions, and on route 60 northbound, for example, Bayview P&R was
    matched to the southbound stop across the road, where northbound buses
    never stop."""
    short = {r["route_id"]: r.get("route_short_name", "") for r in _rows(passio, "routes.txt")}
    trip_rows = _rows(passio, "trips.txt")
    trip_route = {t["trip_id"]: t["route_id"] for t in trip_rows}
    trip_dir = {t["trip_id"]: t.get("direction_id") or "" for t in trip_rows}
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
        all_trips = by_short.get(route, [])
        # For each Passio route with this short name, the direction whose
        # best trip runs the way the timetable reads; on a tie (a loop, or a
        # route without directions) all of them. Directions are chosen per
        # route because routes sharing a timetable (NAS Whidbey) number
        # their directions independently.
        trips = []
        for rid in sorted({trip_route[t] for t in all_trips}):
            fits = {}
            for d in sorted({trip_dir[t] for t in all_trips if trip_route[t] == rid}):
                mine = [t for t in all_trips if trip_route[t] == rid and trip_dir[t] == d]
                c = _candidates(stops, route, set().union(*(trip_stops[t] for t in mine)), name,
                                overrides)  # fmt: skip
                found = [c[p["printed_stop"]][0] for p in stops]
                fits[d] = max(_order(trip_seq[t], found) for t in mine)
            best = max(fits.values())
            trips += [t for t in all_trips if trip_route[t] == rid and fits[trip_dir[t]] == best]
        served = set().union(*(trip_stops[t] for t in trips))
        cands = _candidates(stops, route, served, name, overrides)

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
                # Every Passio route and direction with this short name whose
                # chosen trips serve the stop.
                keys = sorted({(trip_route[t], trip_dir[t]) for t in trips if sid in trip_stops[t]})
                for rid, did in keys or [("", "")]:
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
                            "passio_direction_id": did,
                            "passio_stop_id": sid,
                            "passio_stop_name": name.get(sid, ""),
                            "match": how,
                        }
                    )
    return out


def _applying(matched: list[Row], day: date | None) -> list[Row]:
    """The matched rows with a stop that apply on ``day``: a route that has a
    printed timetable for that kind of day (weekday, Saturday, Sunday) uses
    only that timetable's stops; a route with none for it falls back to all
    of its printed stops. Without ``day``, every row."""
    rows = [r for r in matched if r["passio_stop_id"]]
    if day is None:
        return rows
    by_route: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_route[r["passio_route_id"]].append(r)
    out = []
    for mine in by_route.values():
        todays = [r for r in mine if day_type_applies(r["day_type"], day)]
        out += todays or mine
    return out


def timepoint_pairs(matched: list[Row], day: date | None = None) -> set[tuple[str, str]]:
    """(Passio route id, Passio stop id) pairs that are true timepoints. A
    printed stop counts on every Passio route with the timetable's short name
    that serves it (the three NAS Whidbey routes share one timetable).

    With ``day``, a route that has a printed timetable for that kind of day
    (weekday, Saturday, Sunday) uses only that timetable's stops; a route
    with none for it falls back to all of its printed stops. Without
    ``day``, every printed stop counts."""
    return {(r["passio_route_id"], r["passio_stop_id"]) for r in _applying(matched, day)}


ANY_DIRECTION = "*"


def timepoint_keys(matched: list[Row], day: date | None = None) -> set[tuple[str, str, str]]:
    """(Passio route id, Passio direction id, Passio stop id) of the true
    timepoints, chosen as ``timepoint_pairs`` chooses them. A list made
    before ``timepoints/0.2.0`` has no direction column; its stops count in
    either direction (``ANY_DIRECTION``)."""
    return {
        (r["passio_route_id"], r.get("passio_direction_id", ANY_DIRECTION), r["passio_stop_id"])
        for r in _applying(matched, day)
    }


def is_timepoint(keys: set[tuple[str, str, str]], route: str, direction: str, stop: str) -> bool:
    """Whether a stop of a trip on ``route`` in ``direction`` is a timepoint."""
    return (route, direction, stop) in keys or (route, ANY_DIRECTION, stop) in keys


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
        "timepoint_route_direction_stops": len(timepoint_keys(matched)),
    }


def build(passio: ZipContents, effective: date, folder: Path = REFERENCE_DIR) -> dict[str, Any]:
    printed, overrides = load_reference(effective, folder)
    matched = match(passio, printed, overrides)
    return {"matched": matched, "summary": {"timepoints_version": TIMEPOINTS_VERSION,
                                            "reference": effective.isoformat(),
                                            **summarize(matched)}}  # fmt: skip
