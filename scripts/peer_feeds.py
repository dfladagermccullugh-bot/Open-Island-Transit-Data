"""Find peers' open feeds in the Mobility Database catalog.

    python scripts/peer_feeds.py [--names "Skagit,Intercity,Kitsap"]

Prints, for each name, the catalog's schedule (GTFS) feeds and vehicle
position (GTFS-Realtime) feeds that need no key, with their download
addresses, so ``reference/peers/feeds.csv`` can be filled in by hand. The
catalog is MobilityData's open list of transit feeds
(https://mobilitydatabase.org), published as a CSV with no key.
Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request

CATALOG = "https://files.mobilitydatabase.org/feeds_v2.csv"
DEFAULT_NAMES = (
    "Skagit", "Intercity Transit", "Kitsap", "Mountain Line", "Cache Valley", "BC Transit",
    "Halifax", "Red Metropolitana", "TransMilenio", "Metrobús", "Southern Vectis", "Isle of Wight",
    "Connexxion", "Arriva", "Rapid Penang", "Prasarana", "Singapore", "Land Transport Authority",
    "Israel", "Moscow", "Mosgortrans",
)  # fmt: skip


def rows() -> list[dict[str, str]]:
    req = urllib.request.Request(CATALOG, headers={"User-Agent": "OpenIslandTransitData/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--names", default=",".join(DEFAULT_NAMES))
    args = ap.parse_args(argv)
    catalog = rows()
    for name in [n.strip() for n in args.names.split(",") if n.strip()]:
        hits = [r for r in catalog if name.lower() in (r.get("provider") or "").lower()]
        print(f"== {name}: {len(hits)} feeds")
        for r in hits:
            kind = r.get("data_type", "")
            what = r.get("entity_type", "")
            if kind == "gtfs_rt" and "vp" not in what:
                continue
            auth = r.get("urls.authentication_type", "")
            print(f"  {r.get('id')} {kind} {what} status={r.get('status')} auth={auth} "
                  f"{r.get('location.country_code')} {r.get('location.municipality')} | "
                  f"{r.get('provider')} | {r.get('urls.direct_download')} "
                  f"static_ref={r.get('static_reference')}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
