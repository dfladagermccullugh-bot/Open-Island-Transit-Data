"""Compare the printed-timetable timepoints with the Trillium feed's.

    python scripts/check_timepoints.py PASSIO_ZIP TRILLIUM_ZIP [EFFECTIVE_DATE]

Matches the printed timetable stops (``pipeline/gtfs/timepoints.py``; the
latest reference file unless a date is given) to the Passio zip, writes
``reference/timetable-timepoints-matched.csv`` with a ``trillium_timepoint``
column (True, False, or "no Trillium stop"), and prints a comparison.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.gtfs import crosswalk, timepoints
from pipeline.gtfs.zipread import read_zip


def main() -> int:
    passio, trillium = (read_zip(Path(p).read_bytes()) for p in sys.argv[1:3])
    refs = timepoints.references()
    effective = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else max(refs)
    built = timepoints.build(passio, effective)
    xw = crosswalk.build(passio, trillium)
    trillium_tp = {
        (r["passio_route_id"], r["passio_stop_id"]): r["trillium_timepoint"]
        for r in xw["timepoint_stops"]
    }
    in_trillium = {r["passio_stop_id"] for r in xw["stops"] if r["trillium_stop_id"]}

    def status(pair: tuple[str, str]) -> str | bool:
        if not pair[1]:
            return ""
        return "no Trillium stop" if pair[1] not in in_trillium else trillium_tp.get(pair, False)

    rows = [
        {**r, "trillium_timepoint": status((r["passio_route_id"], r["passio_stop_id"]))}
        for r in built["matched"]
    ]
    out = timepoints.REFERENCE_DIR / "timetable-timepoints-matched.csv"
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    pairs = timepoints.timepoint_pairs(built["matched"])
    agree = Counter(status(p) for p in pairs)
    routes = {p[0] for p in pairs}
    extra = {k for k, v in trillium_tp.items() if v and k[0] in routes and k not in pairs}
    s = built["summary"]
    print(f"reference {s['reference']}: {s['printed_names']} printed names, "
          f"{s['resolved_names']} resolved, {len(s['unresolved'])} unresolved")  # fmt: skip
    print(f"timepoint route-stops: {len(pairs)}")
    print(f"  also a Trillium timepoint: {agree[True]}")
    print(f"  Trillium stop, not a timepoint: {agree[False]}")
    print(f"  stop missing from Trillium: {agree['no Trillium stop']}")
    print(f"Trillium timepoints on those routes that are not printed: {len(extra)}")
    by_route: dict[str, Counter] = defaultdict(Counter)
    for p in pairs:
        by_route[p[0]]["printed"] += 1
        by_route[p[0]]["trillium_agrees"] += status(p) is True
    for k in extra:
        by_route[k[0]]["trillium_only"] += 1
    for r, c in sorted(by_route.items()):
        print(f"  route {r:6} printed {c['printed']:3}  Trillium agrees "
              f"{c['trillium_agrees']:3}  Trillium-only {c['trillium_only']:3}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
