"""The codebook in a form a program can read, and an index of every file.

    python -m pipeline.datapackage --data data-branch     # nightly: both files
    python -m pipeline.datapackage --write datapackage.json   # the copy kept in the repository

Two files go on the ``data`` branch.

``datapackage.json`` describes every table we publish: its columns, their
types and units, and what one row is. It follows the Data Package format
(https://datapackage.org), which R, Python and command-line tools can read.
The names and types of the columns come from the same definitions the
pipeline writes the files with (``RAW_SCHEMA``, ``SPECS``, ``EVENT_SCHEMA``,
``HEADLINE`` and the rest), and the description of each column is taken from
``docs/codebook.md``. So the codebook stays the one place a column is
explained, and ``tests/test_datapackage.py`` fails when the codebook and the
code disagree: a column with no explanation, or an explanation of a column
that no longer exists.

Tables at a fixed address on the ``data`` branch are listed under
``resources``, as the format expects. Tables with one file per service day
live in the Releases; they are listed under ``dailyTables`` with the pattern
of their file names, and ``files.csv`` lists every one of those files.

``files.csv`` has one row for every file in the Releases, built from the
manifests: service day, kind of table, release, name, size, SHA-256 and the
address to download it from. It answers "where is the file for this day?"
without a visit to the Releases pages, and gives a program the hash to check
a download against.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa

from pipeline.archive.build import FETCH_LOG_COLUMNS, RAW_SCHEMA
from pipeline.common import DATA_FORMAT_VERSION, load_json, write_csv
from pipeline.config import REPO
from pipeline.events.__main__ import DAILY, EVENT_SCHEMA, TRACK_SCHEMA
from pipeline.events.routes import ROUTE_COLUMNS
from pipeline.quality.__main__ import HEADLINE
from pipeline.tables.build import SPECS

DICTIONARY_VERSION = "dictionary/0.1.0"
CODEBOOK = Path(__file__).resolve().parents[1] / "docs" / "codebook.md"
SITE = "https://dfladagermccullugh-bot.github.io/Open-Island-Transit-Data/"

FILES_COLUMNS = ["service_date", "table", "release_tag", "name", "bytes", "sha256", "url"]
TIMEPOINT_COLUMNS = [
    "route_short_name", "day_type", "direction", "order", "printed_stop", "passio_route_id",
    "passio_stop_id", "passio_stop_name", "match",
]  # fmt: skip
CROSSWALK_COLUMNS = {
    "stops": [
        "passio_stop_id", "passio_stop_code", "passio_stop_name", "trillium_stop_id",
        "trillium_stop_code", "trillium_stop_name", "method", "distance_m",
        "nearest_trillium_stop_id", "nearest_trillium_stop_name", "nearest_distance_m",
    ],
    "routes": [
        "passio_route_id", "passio_route_short_name", "passio_route_long_name",
        "trillium_route_id", "trillium_route_long_name", "method",
    ],
    "timepoint_stops": [
        "passio_route_id", "passio_stop_id", "trillium_route_id", "trillium_stop_id",
        "passio_stop_times", "trillium_stop_times", "trillium_timepoint_stop_times",
        "trillium_timepoint",
    ],
    "schedule_agreement": [
        "passio_trip_id", "passio_route_id", "passio_stop_id", "stop_sequence", "passio_time",
        "nearest_trillium_minus_passio_s",
    ],
}  # fmt: skip

# Types of the columns of the CSV files. A column not named here is a number.
DATES = {"service_date", "timetable_reference"}
BOOLEANS = {"service_expected", "passio_changed_during_day", "trillium_timepoint"}
INTEGERS = {
    "scheduled_trips", "impossible_jumps", "unassignable_trip_ids", "vehicles_missing_vs_30d",
    "vehicles_seen", "canceled_trips", "scheduled", "observed", "on_time", "early", "late",
    "order", "bytes", "stop_sequence", "passio_stop_times", "trillium_stop_times",
    "trillium_timepoint_stop_times", "nearest_trillium_minus_passio_s", "fetch_id",
    "latency_ms", "http_status", "entity_count",
}  # fmt: skip
STRINGS = {
    "passio_version", "passio_basis", "quality_version", "ontime_version", "route_id",
    "route_name", "table", "release_tag", "name", "sha256", "url", "route_short_name",
    "day_type", "direction", "printed_stop", "match", "method", "passio_time",
}  # fmt: skip

# Units a column's name does not spell out.
UNITS = {
    "lat": "degrees north",
    "lon": "degrees east",
    "bearing": "degrees clockwise from north",
    "speed": "meters per second",
    "odometer": "meters",
    "arrival_delay": "seconds",
    "departure_delay": "seconds",
    "trip_delay": "seconds",
    "arrival_uncertainty": "seconds",
    "departure_uncertainty": "seconds",
    "bytes": "bytes",
    "occupancy_percentage": "percent",
    "prediction_within_60s_5min": "fraction from 0 to 1",
}
SUFFIX_UNITS = {"s": "seconds", "ms": "milliseconds", "m": "meters", "km": "kilometers"}
FRACTION = re.compile(r"(_share|_rate|coverage|uptime)")


@dataclass(frozen=True)
class Table:
    """One published table."""

    name: str
    title: str
    path: str  # the file's address on the data branch, or the pattern of its name in a Release
    release: str | None  # "raw-YYYY-MM" or "parsed-YYYY-MM"; None for a file on the data branch
    one_row: str
    fields: list[tuple[str, str]]  # (column, type)
    sections: tuple[str, ...]  # where docs/codebook.md explains its columns, most specific first
    primary_key: tuple[str, ...] = ()

    @property
    def format(self) -> str:
        return self.path.rsplit(".", 1)[-1]


def arrow_type(t: pa.DataType) -> str:
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_integer(t):
        return "integer"
    if pa.types.is_floating(t):
        return "number"
    if pa.types.is_timestamp(t):
        return "datetime"
    if pa.types.is_list(t):
        return "array"
    if pa.types.is_struct(t):
        return "object"
    return "string"


def csv_type(column: str) -> str:
    if column in DATES:
        return "date"
    if column in BOOLEANS:
        return "boolean"
    if column in INTEGERS:
        return "integer"
    if column in STRINGS or column.endswith(("_id", "_name", "_code")):
        return "string"
    return "number"


def _arrow(schema: pa.Schema) -> list[tuple[str, str]]:
    return [(f.name, arrow_type(f.type)) for f in schema]


def _csv(columns: Iterable[str]) -> list[tuple[str, str]]:
    return [(c, csv_type(c)) for c in columns]


def tables() -> list[Table]:
    """Every table we publish, in the order the codebook describes them."""
    raw = dict(_arrow(RAW_SCHEMA))
    out = [
        Table("raw", "Raw archive", "raw-YYYY-MM-DD.parquet", "raw-YYYY-MM",
              "one attempt to fetch a feed, with the exact response",
              _arrow(RAW_SCHEMA), ("raw-YYYY-MM-DD.parquet",), ("fetch_id",)),
        Table("fetch-log", "Fetch log", "fetch-log-YYYY-MM-DD.csv", "raw-YYYY-MM",
              "one attempt to fetch a feed, without the response body",
              [(c, raw[c]) for c in FETCH_LOG_COLUMNS], ("raw-YYYY-MM-DD.parquet",),
              ("fetch_id",)),
    ]  # fmt: skip
    rows = {
        "vehicles": "one run of fetches in which a vehicle reported the same values",
        "trip-updates": "one run of fetches in which a trip's update stayed the same",
        "trip-update-stops": "one run of fetches in which a stop's prediction stayed the same",
        "alerts": "one run of fetches in which an alert stayed the same",
    }
    # The codebook explains the trip columns once, under vehicles, and the
    # columns every parsed table ends with once, under "Parsed tables".
    shared = ("trip-updates-YYYY-MM-DD.parquet", "vehicles-YYYY-MM-DD.parquet", "Parsed tables")
    for spec in SPECS:
        own = f"{spec.name}-YYYY-MM-DD.parquet"
        out.append(
            Table(spec.name, f"Parsed {spec.name.replace('-', ' ')}", own, "parsed-YYYY-MM",
                  rows[spec.name], _arrow(spec.schema),
                  (own, *(s for s in shared if s != own)))
        )  # fmt: skip
    out += [
        Table("stop-events", "Stop events", "stop-events-YYYY-MM-DD.parquet", "parsed-YYYY-MM",
              "one scheduled stop of one scheduled trip, observed or not",
              _arrow(EVENT_SCHEMA), ("stop-events-YYYY-MM-DD.parquet",),
              ("trip_id", "stop_sequence")),
        Table("trip-tracks", "Trip tracks", "trip-tracks-YYYY-MM-DD.parquet", "parsed-YYYY-MM",
              "one scheduled trip, with how its bus was followed",
              _arrow(TRACK_SCHEMA), ("trip-tracks-YYYY-MM-DD.parquet",), ("trip_id",)),
        Table("quality-daily", "Daily feed-quality figures", "quality/daily.csv", None,
              "one service day", _csv(name for name, _ in HEADLINE), ("quality/daily.csv",),
              ("service_date",)),
        Table("ontime-daily", "Daily on-time figures", "ontime/daily.csv", None,
              "one service day", _csv(DAILY), ("ontime/daily.csv",), ("service_date",)),
        Table("ontime-routes", "Daily on-time figures by route", "ontime/routes.csv", None,
              "one route on one service day", _csv(ROUTE_COLUMNS), ("ontime/routes.csv",),
              ("service_date", "route_id")),
        Table("files", "Index of the files in the Releases", "files.csv", None,
              "one file in a Release", _csv(FILES_COLUMNS), ("files.csv",), ("name",)),
    ]  # fmt: skip
    return out


def versioned_tables() -> list[Table]:
    """Tables written once for each schedule version, in a folder named for
    it. Their addresses change with the version, so they are described but
    not listed as fixed resources."""
    out = [
        Table("timepoints", "Printed timepoints matched to Passio stops",
              "timepoints/<passio version>__timetable-<date>-<hash>/timepoints.csv", None,
              "one printed timetable stop and the Passio stop it was matched to",
              _csv(TIMEPOINT_COLUMNS), ("timepoints/timepoints.csv",))
    ]  # fmt: skip
    one_row = {
        "stops": "one Passio stop",
        "routes": "one Passio route",
        "timepoint_stops": "one Passio route and stop",
        "schedule_agreement": "one Passio stop time at a Trillium timepoint",
    }
    for name, columns in CROSSWALK_COLUMNS.items():
        out.append(
            Table(f"crosswalk-{name.replace('_', '-')}", f"Passio to Trillium crosswalk: {name}",
                  f"crosswalk/<passio version>__<trillium version>/{name}.csv", None,
                  one_row[name], _csv(columns), (f"crosswalk/{name}.csv",))
        )  # fmt: skip
    return out


# --- the codebook's explanations ------------------------------------------------

_HEADING = re.compile(r"^(#{2,3})\s+(.*?)\s*$")
_COLUMN_OF = re.compile(r"^`([\w./-]+)` column$")
_NAME = re.compile(r"`([A-Za-z_][\w.]*)`")


def codebook_columns(text: str) -> dict[str, dict[str, str]]:
    """Section -> column -> explanation, from the tables in the codebook.

    A section is a heading without its backticks (``vehicles-YYYY-MM-DD.parquet``).
    A table whose first header reads ```daily.csv` column`` belongs to that file
    in the folder its heading names (``quality/daily.csv``). One row can explain
    several columns at once: every name in its first cell gets the same text."""
    out: dict[str, dict[str, str]] = {}
    heading, section, in_table = "", "", False
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            heading, section, in_table = m.group(2).replace("`", ""), "", False
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not in_table:  # the header row
            in_table = True
            named = _COLUMN_OF.match(cells[0])
            folder = heading.split("/")[0] + "/" if "/" in heading else ""
            section = folder + named.group(1) if named else heading
            continue
        if set(cells[0]) <= {"-", ":", " "}:
            continue
        for name in _NAME.findall(cells[0]):
            out.setdefault(section, {})[name] = cells[-1]
    return out


_AS_IN = re.compile(r"^As in `([\w-]+)`$")


def describe(table: Table, book: dict[str, dict[str, str]]) -> dict[str, str | None]:
    """Each column's explanation, from the first of the table's sections that
    has one; None where the codebook has none. Where the codebook says only
    "As in `vehicles`", the explanation is fetched from that table."""
    out: dict[str, str | None] = {}
    for name, _ in table.fields:
        text = next((book[s][name] for s in table.sections if name in book.get(s, {})), None)
        while text and (ref := _AS_IN.match(text)):
            text = book.get(f"{ref.group(1)}-YYYY-MM-DD.parquet", {}).get(name)
        out[name] = text
    return out


def _files_section() -> dict[str, str]:
    return {
        "service_date": "The service day the file belongs to",
        "table": "The kind of table, as named in this dictionary (`vehicles`, `stop-events`, "
        "`raw`, `fetch-log`, `static-passio`, `static-trillium` and so on)",
        "release_tag": "The Release the file is in",
        "name": "The file's name",
        "bytes": "Its size",
        "sha256": "SHA-256 of the file, to check a download against",
        "url": "Where to download it",
    }


def unit(column: str, kind: str) -> str | None:
    if column in UNITS:
        return UNITS[column]
    if kind not in ("number", "integer"):
        return None
    if FRACTION.search(column):
        return "fraction from 0 to 1"
    suffix = re.search(r"_(ms|km|s|m)(?:_|$)", column)
    return SUFFIX_UNITS[suffix.group(1)] if suffix else None


def schema(table: Table, book: dict[str, dict[str, str]]) -> dict[str, Any]:
    explained = describe(table, book)
    fields = []
    for name, kind in table.fields:
        field: dict[str, Any] = {"name": name, "type": kind}
        if explained[name]:
            field["description"] = explained[name]
        if (u := unit(name, kind)) is not None:
            field["unit"] = u
        if kind == "datetime":
            field["timezone"] = "UTC"
        fields.append(field)
    out: dict[str, Any] = {"fields": fields, "missingValues": [""]}
    if table.primary_key:
        out["primaryKey"] = list(table.primary_key)
    return out


MEDIA = {"csv": "text/csv", "parquet": "application/vnd.apache.parquet"}


def build(repo: str = REPO, codebook: Path = CODEBOOK) -> dict[str, Any]:
    """The whole dictionary. It depends only on the code and the codebook, so
    it is the same every night until one of them changes."""
    book = codebook_columns(codebook.read_text(encoding="utf-8"))
    book["files.csv"] = _files_section()

    def entry(t: Table) -> dict[str, Any]:
        out: dict[str, Any] = {"name": t.name, "title": t.title}
        if t.release is None and "<" not in t.path:
            out["path"] = t.path
        else:
            out["pathPattern"] = t.path
        if t.release:
            out["release"] = t.release
            out["urlPattern"] = f"https://github.com/{repo}/releases/download/{t.release}/{t.path}"
        out |= {"format": t.format, "mediatype": MEDIA[t.format], "oneRowIs": t.one_row,
                "schema": schema(t, book)}  # fmt: skip
        if t.format == "csv":
            out["encoding"] = "utf-8"
            out["dialect"] = {"delimiter": ",", "header": True}
        return out

    every = tables()
    return {
        "profile": "data-package",
        "name": "open-island-transit-data",
        "title": "Open Island Transit Data",
        "description": (
            "Island Transit's (Whidbey and Camano Islands, Washington) GTFS-Realtime feeds, "
            "saved every 20 seconds since November 19, 2026, with daily measures of the feed's "
            "quality and on-time figures inferred from vehicle positions."
        ),
        "homepage": SITE,
        "repository": f"https://github.com/{repo}",
        "version": DICTIONARY_VERSION,
        "dataFormat": DATA_FORMAT_VERSION,
        "licenses": [
            {
                "name": "see-LICENSE-DATA",
                "title": "Terms for the archived data",
                "path": f"https://github.com/{repo}/blob/main/LICENSE-DATA.md",
            }
        ],
        "documentation": {
            "codebook": f"{SITE}codebook.html",
            "methods": f"{SITE}methods.html",
        },
        "conventions": {
            "timestamps": "UTC",
            "serviceDay": "a calendar date in America/Los_Angeles",
            "pacificFields": "hours of the day (by_hour, start_hour), day_type and passio_time",
            "shares": "fractions from 0 to 1",
            "missing": "an empty cell in CSV files, null in Parquet and JSON files",
        },
        "resources": [entry(t) for t in every if t.release is None],
        "dailyTables": [entry(t) for t in every if t.release is not None],
        "versionedTables": [entry(t) for t in versioned_tables()],
    }


def dumps(package: dict[str, Any]) -> str:
    return json.dumps(package, indent=1, ensure_ascii=False) + "\n"


# --- the index of files -----------------------------------------------------------

_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}")


def table_of(name: str) -> str:
    """The kind of table a Release file holds, from its name:
    ``vehicles-2026-11-20.parquet`` is ``vehicles``, and
    ``static-2026-11-20-passio-3.zip`` is ``static-passio``."""
    stem = _DATED.sub("", name.rsplit(".", 1)[0])
    return re.sub(r"-\d+$", "", stem)


def files_index(data: Path, repo: str = REPO) -> list[list[Any]]:
    """One row for every file the manifests record in a Release, by day,
    release and name. A file listed by two manifests appears once."""
    seen: dict[tuple[str, str], list[Any]] = {}
    for manifest in ("raw", "parsed", "events"):
        days = (load_json(data / "manifest" / f"{manifest}.json") or {}).get("days", {})
        for day, entry in days.items():
            tag = entry.get("release_tag")
            if not tag:
                continue
            for f in entry.get("files", []):
                url = f"https://github.com/{repo}/releases/download/{tag}/{f['name']}"
                seen[(tag, f["name"])] = [
                    day, table_of(f["name"]), tag, f["name"], f.get("bytes"), f["sha256"], url,
                ]  # fmt: skip
    return sorted(seen.values(), key=lambda r: (r[0], r[2], r[3]))


def write(data: Path, repo: str = REPO) -> int:
    """Write both files at the top of the data branch; return the files indexed."""
    rows = files_index(data, repo)
    write_csv(data / "files.csv", FILES_COLUMNS, rows)
    (data / "datapackage.json").write_text(dumps(build(repo)), encoding="utf-8")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Write the data dictionary and the file index.")
    ap.add_argument("--data", type=Path, help="data branch checkout: write both files there")
    ap.add_argument("--write", type=Path, help="write only the dictionary to this file")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args()
    if args.write:
        args.write.write_text(dumps(build(args.repo)), encoding="utf-8")
        print(f"{args.write}: written")
    if args.data:
        print(f"files.csv: {write(args.data, args.repo)} files; datapackage.json written")
    if not (args.write or args.data):
        ap.error("give --data or --write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
