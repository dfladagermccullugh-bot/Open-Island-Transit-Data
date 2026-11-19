"""Check timepoints against Island Transit's printed timetables.

    python scripts/check_timepoints.py PASSIO_ZIP TRILLIUM_ZIP

The stops printed as columns or rows in the public timetables
(reference/timetable-timepoints-*.csv, read from the service guide PDFs) are
the published timepoints. Each printed name is matched to Passio stops on the
same route: by name similarity (score >= 0.6), or by a reviewed entry in
reference/timetable-overrides.csv, which also lists names left unresolved.
Where two Passio stops share a name (one per direction), the one served by
the Passio trips that best fit the printed list is used.

Writes reference/timetable-timepoints-matched.csv (``trillium_timepoint``:
True, False, or "no Trillium stop") and prints how the
printed timepoints compare with the timepoints taken from Trillium.
"""

from __future__ import annotations

import csv
import difflib
import io
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.gtfs import crosswalk  # noqa: E402
from pipeline.gtfs.zipread import read_zip  # noqa: E402

REF = Path(__file__).resolve().parents[1] / "reference"
THRESHOLD = 0.6
WORDS = {"st": "street", "rd": "road", "ave": "avenue", "dr": "drive", "blvd": "boulevard",
         "elem": "elementary", "pr": "park ride", "p": "park", "r": "ride", "hs": "high school",
         "ln": "lane"}  # fmt: skip
DROP = {"sw", "se", "nw", "ne", "n", "s", "e", "w", "at", "the"}


def norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and ").replace("'", ""))
    return " ".join(WORDS.get(w, w) for w in s.split() if w not in DROP)


def score(a: str, b: str) -> float:
    x, y = set(norm(a).split()), set(norm(b).split())
    jaccard = len(x & y) / len(x | y) if x | y else 0.0
    return 0.6 * jaccard + 0.4 * difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def rows(contents, name):
    return list(csv.DictReader(io.StringIO(contents.files[name].decode("utf-8-sig"))))


def main() -> int:
    passio_zip, trillium_zip = (read_zip(Path(p).read_bytes()) for p in sys.argv[1:3])
    printed = list(csv.DictReader((REF / "timetable-timepoints-2026-09-27.csv").open()))
    overrides = {
        (o["route_short_name"], o["printed_stop"]): o
        for o in csv.DictReader((REF / "timetable-overrides.csv").open())
    }

    short = {r["route_id"]: r["route_short_name"] for r in rows(passio_zip, "routes.txt")}
    trip_route = {t["trip_id"]: t["route_id"] for t in rows(passio_zip, "trips.txt")}
    name = {s["stop_id"]: s["stop_name"] for s in rows(passio_zip, "stops.txt")}
    trip_stops: dict[str, set[str]] = defaultdict(set)
    for st in rows(passio_zip, "stop_times.txt"):
        trip_stops[st["trip_id"]].add(st["stop_id"])
    by_short: dict[str, list[str]] = defaultdict(list)  # short name -> trip ids
    for tid, rid in trip_route.items():
        by_short[short[rid]].append(tid)

    xw = crosswalk.build(passio_zip, trillium_zip)
    trillium_tp = {
        (r["passio_route_id"], r["passio_stop_id"]): r["is_timepoint"]
        for r in xw["timepoint_stops"]
    }
    in_trillium = {r["passio_stop_id"] for r in xw["stops"] if r["trillium_stop_id"]}

    blocks: dict[tuple, list[dict]] = defaultdict(list)
    for p in printed:
        blocks[(p["route_short_name"], p["day_type"], p["direction"])].append(p)

    out = []
    for (route, _day, _dirn), stops in blocks.items():
        trips = by_short[route]
        served = set().union(*(trip_stops[t] for t in trips)) if trips else set()
        cands = {}
        for p in stops:
            o = overrides.get((route, p["printed_stop"]))
            if o is not None:
                ids = [x for x in o["passio_stop_ids"].split(";") if x]
                cands[p["printed_stop"]] = (ids, "override" if ids else o["note"])
                continue
            scored = sorted(((score(p["printed_stop"], name[s]), s) for s in served), reverse=True)
            if scored and scored[0][0] >= THRESHOLD:
                best = name[scored[0][1]]
                ids = sorted(s for sc, s in scored if name[s] == best)
                cands[p["printed_stop"]] = (ids, f"name {scored[0][0]:.2f}")
            else:
                cands[p["printed_stop"]] = ([], "unresolved: no name match")
        # Trips that contain the most printed stops decide between same-named stops.
        fit = {t: sum(bool(set(ids) & trip_stops[t]) for ids, _ in cands.values()) for t in trips}
        top = max(fit.values(), default=0)
        best = [trip_stops[t] for t, n in fit.items() if n == top] if top else []
        allowed = set().union(*best)
        for p in stops:
            ids, how = cands[p["printed_stop"]]
            chosen = [s for s in ids if s in allowed] or ids
            for sid in chosen or [""]:
                rid = next((trip_route[t] for t in trips if sid in trip_stops[t]), "")
                out.append({**{k: p[k] for k in ("route_short_name", "day_type", "direction",
                                                 "order", "printed_stop")},
                            "passio_route_id": rid, "passio_stop_id": sid,
                            "passio_stop_name": name.get(sid, ""), "match": how,
                            "trillium_timepoint": (
                                "" if not sid else "no Trillium stop" if sid not in in_trillium
                                else trillium_tp.get((rid, sid), False))})  # fmt: skip

    with (REF / "timetable-timepoints-matched.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)

    names = {(r["route_short_name"], r["printed_stop"]) for r in out}
    matched = [r for r in out if r["passio_stop_id"]]
    printed_pairs = {(r["passio_route_id"], r["passio_stop_id"]) for r in matched}
    unresolved = names - {(r["route_short_name"], r["printed_stop"]) for r in out
                          if r["passio_stop_id"]}  # fmt: skip
    agree = Counter(
        "no Trillium stop" if p[1] not in in_trillium else bool(trillium_tp.get(p))
        for p in printed_pairs
    )
    printed_routes = {p[0] for p in printed_pairs}
    extra = [k for k, v in trillium_tp.items() if v and k[0] in printed_routes
             and k not in printed_pairs]  # fmt: skip
    print(f"printed timepoint names: {len(names)}")
    print(f"  unresolved: {len(unresolved)}")
    print(f"printed timepoints matched to Passio route-stops: {len(printed_pairs)}")
    print(f"  also a Trillium timepoint: {agree[True]}")
    print(f"  Trillium stop, not a timepoint: {agree[False]}")
    print(f"  stop missing from Trillium: {agree['no Trillium stop']}")
    print(f"Trillium timepoints on those routes that are not printed: {len(extra)}")
    by_route = defaultdict(Counter)
    for p in printed_pairs:
        by_route[short[p[0]]]["printed"] += 1
        by_route[short[p[0]]]["trillium_agrees"] += bool(trillium_tp.get(p))
    for k in extra:
        by_route[short[k[0]]]["trillium_only"] += 1
    for r, c in sorted(by_route.items()):
        print(f"  route {r or 'NAS':5} printed {c['printed']:3}"
              f"  Trillium agrees {c['trillium_agrees']:3}"
              f"  Trillium-only {c['trillium_only']:3}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
