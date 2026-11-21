"""Read the text tables of a GTFS feed, each one parsed once.

Every stage reads ``stop_times.txt`` (about 10,000 rows) and its neighbours;
parsing them again for every use took most of a nightly run's time on the
schedule. The parsed rows are kept on the ``ZipContents`` they came from.
Callers must not change the rows they are given.
"""

from __future__ import annotations

import csv
import io

from pipeline.gtfs.zipread import ZipContents

Row = dict[str, str]


def rows(contents: ZipContents, name: str) -> list[Row]:
    """Rows of ``name`` (for example ``"stops.txt"``); empty if the file is
    absent. A byte-order mark is dropped and undecodable bytes replaced."""
    cache = contents.tables
    if name not in cache:
        body = contents.files.get(name)
        text = body.decode("utf-8-sig", errors="replace") if body else ""
        cache[name] = list(csv.DictReader(io.StringIO(text)))
    return cache[name]


def seconds(hms: str | None) -> int | None:
    """Seconds since GTFS 00:00:00 for ``"HH:MM:SS"`` (hours may pass 24);
    None for an empty time."""
    if not hms or not hms.strip():
        return None
    h, m, s = (int(x) for x in hms.strip().split(":"))
    return h * 3600 + m * 60 + s
