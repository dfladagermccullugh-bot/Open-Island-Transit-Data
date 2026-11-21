"""Build one service day's archive files from the collector.

Files written for day D:
  raw-D.parquet        every fetch attempt, with the exact response bytes
  fetch-log-D.csv      the same rows without the bytes (the paradata)
  static-D-<source>-<id>.zip   each static GTFS download, as received

The manifest entry records row counts, a digest of what was archived and the
SHA-256 of every file. The collector deletes its copy of a day only when its
own count and digest equal the manifest's.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.archive.store import Collector, Row
from pipeline.common import file_entry as _file_entry

FETCH_LOG_COLUMNS = [
    "fetch_id",
    "feed",
    "scheduled_at",
    "requested_at",
    "responded_at",
    "latency_ms",
    "http_status",
    "bytes",
    "sha256",
    "header_ts",
    "entity_count",
    "newest_entity_ts",
    "error_class",
    "error_msg",
    "collector_version",
    "logged_at",
]
TIMESTAMP_COLUMNS = (
    "scheduled_at",
    "requested_at",
    "responded_at",
    "header_ts",
    "newest_entity_ts",
    "logged_at",
)

RAW_SCHEMA = pa.schema(
    [
        ("fetch_id", pa.int64()),
        ("feed", pa.string()),
        ("scheduled_at", pa.timestamp("us", tz="UTC")),
        ("requested_at", pa.timestamp("us", tz="UTC")),
        ("responded_at", pa.timestamp("us", tz="UTC")),
        ("latency_ms", pa.int32()),
        ("http_status", pa.int32()),
        ("bytes", pa.int32()),
        ("sha256", pa.string()),
        ("header_ts", pa.timestamp("us", tz="UTC")),
        ("entity_count", pa.int32()),
        ("newest_entity_ts", pa.timestamp("us", tz="UTC")),
        ("error_class", pa.string()),
        ("error_msg", pa.string()),
        ("collector_version", pa.string()),
        ("logged_at", pa.timestamp("us", tz="UTC")),
        ("body", pa.binary()),
    ]
)


def raw_digest(rows: list[Row]) -> str:
    """md5 of "fetch_id:sha256" for every row with a body, comma-joined in
    fetch_id order. Must equal the collector's ``day_digest`` for the day."""
    parts = [
        f"{r['fetch_id']}:{r['sha256']}"
        for r in sorted(rows, key=lambda r: r["fetch_id"])
        if r.get("body") is not None
    ]
    return hashlib.md5(",".join(parts).encode()).hexdigest()  # noqa: S324 (not for security)


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if value else None


def merge_rows(log_rows: list[Row], raw_rows: list[Row]) -> list[Row]:
    """All fetch-log rows, each with its body if one was stored."""
    bodies = {r["fetch_id"]: r["body"] for r in raw_rows}
    return [{**r, "body": bodies.get(r["fetch_id"])} for r in log_rows]


def write_day(day: date, rows: list[Row], static_rows: list[Row], out: Path) -> dict[str, Any]:
    """Write the day's files into ``out`` and return its manifest entry."""
    out.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: r["fetch_id"])
    files = []

    columns: dict[str, list[Any]] = {name: [] for name in RAW_SCHEMA.names}
    for r in rows:
        for name in RAW_SCHEMA.names:
            value = r.get(name)
            columns[name].append(_ts(value) if name in TIMESTAMP_COLUMNS else value)
    raw_path = out / f"raw-{day.isoformat()}.parquet"
    pq.write_table(
        pa.table(columns, schema=RAW_SCHEMA),
        raw_path,
        compression="zstd",
        compression_level=12,
        data_page_size=8 * 1024 * 1024,
    )
    files.append(_file_entry(raw_path))

    log_path = out / f"fetch-log-{day.isoformat()}.csv"
    with log_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FETCH_LOG_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    files.append(_file_entry(log_path))

    static_meta = []
    for s in static_rows:
        meta = {k: v for k, v in s.items() if k != "body"}
        if s["body"] is not None:
            path = out / f"static-{day.isoformat()}-{s['source']}-{s['static_fetch_id']}.zip"
            path.write_bytes(s["body"])
            files.append(_file_entry(path))
            meta["file"] = path.name
        static_meta.append(meta)

    with_body = [r for r in rows if r.get("body") is not None]
    return {
        "service_date": day.isoformat(),
        "fetch_rows": len(rows),
        "raw_rows": len(with_body),
        "digest": raw_digest(rows),
        "ok_rows": sum(1 for r in rows if r["http_status"] == 200 and not r["error_class"]),
        "first_fetch_id": rows[0]["fetch_id"] if rows else None,
        "last_fetch_id": rows[-1]["fetch_id"] if rows else None,
        "static": static_meta,
        "files": files,
    }


def build_day(collector: Collector, day: date, out: Path) -> dict[str, Any]:
    """Pull day ``day`` from the collector, check it, write files."""
    log_rows = collector.fetch_log(day)
    raw_rows = list(collector.raw(day))
    entry = write_day(day, merge_rows(log_rows, raw_rows), collector.static(day), out)

    # The collector must agree with what was just archived.
    remote = collector.day_digest(day)
    if (remote["fetch_rows"], remote["raw_rows"], remote["digest"]) != (
        entry["fetch_rows"],
        entry["raw_rows"],
        entry["digest"],
    ):
        raise RuntimeError(f"{day}: archive does not match the collector: {remote} vs {entry}")
    return entry
