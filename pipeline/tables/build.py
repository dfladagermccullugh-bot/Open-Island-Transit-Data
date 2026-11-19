"""Parsed daily tables, built from one day's archived raw Parquet file.

The feeds are polled every 20 seconds, but most of what they report does not
change that often: a vehicle position changes when the bus sends a new one,
and trip-update predictions about once a minute. Each table therefore holds
one row per *run*: a stretch of consecutive successful fetches of the same
feed in which an item (a vehicle, a trip, a stop prediction, an alert)
reported exactly the same values. Each row records the first and last fetch
of its run and how many fetches saw it.

A run ends when a value changes or when the item is missing from a fetch. A
failed fetch also ends every open run for that feed, because nothing is known
about what the feed held at that moment.

Files written for day D:
  vehicles-D.parquet           one row per vehicle per run (in practice, per
                               vehicle timestamp)
  trip-updates-D.parquet       one row per trip per run
  trip-update-stops-D.parquet  one row per predicted stop per run
  alerts-D.parquet             one row per alert per run
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from google.protobuf.message import DecodeError

from pipeline.parse.realtime import Row, parse_feed

TABLES_VERSION = "tables/0.1.0"

TS = pa.timestamp("s", tz="UTC")
FETCH_TS = pa.timestamp("us", tz="UTC")

# Columns added to every table to describe the run.
RUN_FIELDS = [
    ("first_fetch_id", pa.int64()),
    ("last_fetch_id", pa.int64()),
    ("first_requested_at", FETCH_TS),
    ("last_requested_at", FETCH_TS),
    ("times_seen", pa.int32()),
]

TRIP_FIELDS = [
    ("trip_id", pa.string()),
    ("route_id", pa.string()),
    ("direction_id", pa.int32()),
    ("start_date", pa.string()),
    ("start_time", pa.string()),
    ("trip_schedule_relationship", pa.string()),
]


@dataclass(frozen=True)
class Spec:
    """One output table: its source rows, how items are identified, columns."""

    name: str
    feed: str
    source: str  # attribute of ParsedFeed
    key: tuple[str, ...]
    fields: list[tuple[str, pa.DataType]]
    timestamps: tuple[str, ...] = ()  # epoch-second columns stored as timestamps

    @property
    def schema(self) -> pa.Schema:
        return pa.schema(self.fields + RUN_FIELDS)


SPECS = [
    Spec(
        name="vehicles",
        feed="vehiclePositions",
        source="vehicles",
        key=("vehicle_key",),
        fields=[
            ("vehicle_key", pa.string()),
            ("vehicle_id", pa.string()),
            ("vehicle_label", pa.string()),
            ("vehicle_ts", TS),
            *TRIP_FIELDS,
            ("lat", pa.float64()),
            ("lon", pa.float64()),
            ("bearing", pa.float32()),
            ("speed", pa.float32()),
            ("odometer", pa.float64()),
            ("current_stop_sequence", pa.int32()),
            ("stop_id", pa.string()),
            ("current_status", pa.string()),
            ("congestion_level", pa.string()),
            ("occupancy_status", pa.string()),
            ("occupancy_percentage", pa.int32()),
        ],
        timestamps=("vehicle_ts",),
    ),
    Spec(
        name="trip-updates",
        feed="tripUpdates",
        source="trip_updates",
        key=("trip_key",),
        fields=[
            ("trip_key", pa.string()),
            *TRIP_FIELDS,
            ("vehicle_id", pa.string()),
            ("trip_update_ts", TS),
            ("trip_delay", pa.int32()),
            ("stop_time_update_count", pa.int32()),
        ],
        timestamps=("trip_update_ts",),
    ),
    Spec(
        name="trip-update-stops",
        feed="tripUpdates",
        source="trip_update_stops",
        key=("trip_key", "stop_sequence", "stop_id"),
        fields=[
            ("trip_key", pa.string()),
            *TRIP_FIELDS,
            ("vehicle_id", pa.string()),
            ("trip_update_ts", TS),
            ("trip_delay", pa.int32()),
            ("stop_sequence", pa.int32()),
            ("stop_id", pa.string()),
            ("arrival_time", TS),
            ("arrival_delay", pa.int32()),
            ("arrival_uncertainty", pa.int32()),
            ("departure_time", TS),
            ("departure_delay", pa.int32()),
            ("departure_uncertainty", pa.int32()),
            ("stop_schedule_relationship", pa.string()),
        ],
        timestamps=("trip_update_ts", "arrival_time", "departure_time"),
    ),
    Spec(
        name="alerts",
        feed="serviceAlerts",
        source="alerts",
        key=("entity_id",),
        fields=[
            ("entity_id", pa.string()),
            (
                "active_periods",
                pa.list_(pa.struct([("start", TS), ("end", TS)])),
            ),
            ("cause", pa.string()),
            ("effect", pa.string()),
            ("header", pa.string()),
            ("description", pa.string()),
            ("url", pa.string()),
            ("route_ids", pa.list_(pa.string())),
            ("stop_ids", pa.list_(pa.string())),
            ("trip_ids", pa.list_(pa.string())),
        ],
    ),
]
FEEDS = sorted({s.feed for s in SPECS})


def _epoch(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value is not None else None


def _trip_key(row: Row) -> str:
    # Trip IDs are unique within a service day; fall back to the entity id
    # so that an update with no trip id is still kept, and visibly so.
    return row["trip_id"] if row["trip_id"] is not None else f"entity:{row['entity_id']}"


def _prepare(spec: Spec, row: Row) -> Row:
    """Add the key column and convert epoch seconds to datetimes."""
    out = dict(row)
    if spec.name == "vehicles":
        vid = row["vehicle_id"]
        out["vehicle_key"] = vid if vid is not None else f"entity:{row['entity_id']}"
    elif spec.feed == "tripUpdates":
        out["trip_key"] = _trip_key(row)
    for name in spec.timestamps:
        out[name] = _epoch(row[name])
    if spec.name == "alerts":
        out["active_periods"] = [
            {"start": _epoch(s), "end": _epoch(e)} for s, e in row["active_periods"]
        ]
    return {name: out.get(name) for name, _ in spec.fields}


def _freeze(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    return value


@dataclass
class _Run:
    values: Row
    frozen: tuple[Any, ...]
    first_fetch_id: int
    first_requested_at: datetime
    last_fetch_id: int
    last_requested_at: datetime
    times_seen: int = 1

    def row(self) -> Row:
        return {
            **self.values,
            "first_fetch_id": self.first_fetch_id,
            "last_fetch_id": self.last_fetch_id,
            "first_requested_at": self.first_requested_at,
            "last_requested_at": self.last_requested_at,
            "times_seen": self.times_seen,
        }


@dataclass
class _Collapser:
    """Turns a stream of per-fetch rows into runs, for one table."""

    spec: Spec
    open: dict[tuple[Any, ...], _Run] = field(default_factory=dict)
    done: list[Row] = field(default_factory=list)
    duplicate_keys: int = 0  # items that appeared twice in one fetch

    def observe(self, fetch_id: int, requested_at: datetime, rows: Iterable[Row]) -> None:
        still_open: dict[tuple[Any, ...], _Run] = {}
        for raw in rows:
            values = _prepare(self.spec, raw)
            key = tuple(values[k] for k in self.spec.key)
            frozen = tuple(_freeze(v) for v in values.values())
            if key in still_open:
                # The same item twice in one message. Keep both, as separate
                # runs, rather than silently dropping one.
                self.duplicate_keys += 1
                self.done.append(
                    _Run(values, frozen, fetch_id, requested_at, fetch_id, requested_at).row()
                )
                continue
            run = self.open.pop(key, None)
            if run is not None and run.frozen == frozen:
                run.last_fetch_id = fetch_id
                run.last_requested_at = requested_at
                run.times_seen += 1
            else:
                if run is not None:
                    self.done.append(run.row())
                run = _Run(values, frozen, fetch_id, requested_at, fetch_id, requested_at)
            still_open[key] = run
        self.close()  # items missing from this fetch end their runs
        self.open = still_open

    def close(self) -> None:
        self.done.extend(run.row() for run in self.open.values())
        self.open = {}

    def table(self) -> pa.Table:
        self.close()
        rows = sorted(
            self.done, key=lambda r: (r["first_fetch_id"], *map(str, (r[k] for k in self.spec.key)))
        )
        columns = {name: [r[name] for r in rows] for name in self.spec.schema.names}
        return pa.table(columns, schema=self.spec.schema)


@dataclass
class DayTables:
    tables: dict[str, pa.Table]
    fetches: dict[str, dict[str, int]]  # per feed: attempted, parsed, failed, decode_errors
    duplicate_keys: dict[str, int]


def _ok(row: Row) -> bool:
    return (
        row.get("body") is not None and row.get("http_status") == 200 and not row.get("error_class")
    )


def build_tables(rows: Iterable[Row]) -> DayTables:
    """Collapse one day's raw rows (in any order) into the parsed tables."""
    collapsers = {s.name: _Collapser(s) for s in SPECS}
    by_feed: dict[str, list[_Collapser]] = {f: [] for f in FEEDS}
    for s in SPECS:
        by_feed[s.feed].append(collapsers[s.name])
    counts = {f: {"attempted": 0, "parsed": 0, "failed": 0, "decode_errors": 0} for f in FEEDS}

    for row in sorted(rows, key=lambda r: r["fetch_id"]):
        feed = row["feed"]
        if feed not in counts:
            continue
        counts[feed]["attempted"] += 1
        parsed = None
        if _ok(row):
            try:
                parsed = parse_feed(row["body"])
            except DecodeError:
                counts[feed]["decode_errors"] += 1
        if parsed is None:
            counts[feed]["failed"] += 1
            for c in by_feed[feed]:
                c.close()  # nothing is known about this moment
            continue
        counts[feed]["parsed"] += 1
        for c in by_feed[feed]:
            c.observe(row["fetch_id"], row["requested_at"], getattr(parsed, c.spec.source))

    return DayTables(
        tables={name: c.table() for name, c in collapsers.items()},
        fetches=counts,
        duplicate_keys={name: c.duplicate_keys for name, c in collapsers.items()},
    )


def read_raw(path: Path) -> Iterator[Row]:
    """Rows of an archived raw Parquet file, a batch at a time."""
    columns = ["fetch_id", "feed", "requested_at", "http_status", "error_class", "body"]
    for batch in pq.ParquetFile(path).iter_batches(batch_size=500, columns=columns):
        yield from batch.to_pylist()


def _file_entry(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"name": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def write_tables(day: date, built: DayTables, out: Path) -> dict[str, Any]:
    """Write the day's tables into ``out`` and return its manifest entry."""
    out.mkdir(parents=True, exist_ok=True)
    files = []
    rows = {}
    for name, table in built.tables.items():
        table = table.replace_schema_metadata(
            {"service_date": day.isoformat(), "tables_version": TABLES_VERSION}
        )
        path = out / f"{name}-{day.isoformat()}.parquet"
        pq.write_table(table, path, compression="zstd", compression_level=9)
        files.append(_file_entry(path))
        rows[name] = table.num_rows
    return {
        "service_date": day.isoformat(),
        "tables_version": TABLES_VERSION,
        "rows": rows,
        "fetches": built.fetches,
        "duplicate_keys": built.duplicate_keys,
        "files": files,
    }
