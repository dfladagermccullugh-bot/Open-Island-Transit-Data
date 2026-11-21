"""Days on which Island Transit runs no service.

Passio's static GTFS has no holiday exceptions: on Thanksgiving it lists the
same trips as on any Thursday. Island Transit's website says "Service is not
provided on the following holidays or the days of observance" and names New
Year's Day, Memorial Day, Independence Day, Labor Day, Thanksgiving Day and
Christmas Day. Those dates are kept in ``reference/no-service-days.csv``,
each with the page it was read from and when.

A listed day is still archived and measured exactly like any other; its day
files carry ``service_expected: false``. Figures that pool several days
(the 28-day route tables, the randomness-of-gaps model, monthly reports)
leave it out, so that a holiday does not read as a day of lost buses.

The file lists the holidays themselves. When one falls on a weekend and
Island Transit announces a different day of observance, add that date too.
"""

from __future__ import annotations

import csv
from datetime import date
from functools import cache
from pathlib import Path

REFERENCE = Path(__file__).resolve().parents[2] / "reference" / "no-service-days.csv"


@cache
def _table(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["service_date"].strip(): r for r in csv.DictReader(fh)}


def no_service(day: date | str, path: Path = REFERENCE) -> dict[str, str] | None:
    """The reference row for ``day`` if no service runs then, else None."""
    key = day if isinstance(day, str) else day.isoformat()
    row = _table(path).get(key)
    return {k: row[k] for k in ("holiday", "source", "read_on")} if row else None


def service_expected(day: date | str, path: Path = REFERENCE) -> bool:
    return no_service(day, path) is None
