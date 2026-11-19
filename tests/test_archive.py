import csv
import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq

from pipeline.archive.build import merge_rows, raw_digest, write_day
from pipeline.archive.days import complete_days, day_bounds, pacific_date

FIXTURES = Path(__file__).parent / "fixtures"


def test_day_bounds_standard_time():
    start, end = day_bounds(date(2026, 11, 19))
    assert start == datetime(2026, 11, 19, 8, 0, tzinfo=UTC)  # PST = UTC-8
    assert end - start == timedelta(hours=24)


def test_day_bounds_daylight_time():
    start, end = day_bounds(date(2027, 7, 1))
    assert start == datetime(2027, 7, 1, 7, 0, tzinfo=UTC)  # PDT = UTC-7
    assert end - start == timedelta(hours=24)


def test_day_bounds_spring_forward_is_23_hours():
    start, end = day_bounds(date(2027, 3, 14))
    assert end - start == timedelta(hours=23)


def test_day_bounds_fall_back_is_25_hours():
    start, end = day_bounds(date(2027, 11, 7))
    assert end - start == timedelta(hours=25)


def test_days_tile_the_timeline_across_dst():
    for first in (date(2027, 3, 12), date(2027, 11, 5)):
        for i in range(5):
            _, end = day_bounds(first + timedelta(days=i))
            next_start, _ = day_bounds(first + timedelta(days=i + 1))
            assert end == next_start


def test_pacific_date_just_before_and_after_midnight():
    assert pacific_date(datetime(2026, 11, 20, 7, 59, 59, tzinfo=UTC)) == date(2026, 11, 19)
    assert pacific_date(datetime(2026, 11, 20, 8, 0, 0, tzinfo=UTC)) == date(2026, 11, 20)


def test_complete_days_never_includes_today():
    now = datetime(2026, 11, 22, 6, 0, tzinfo=UTC)  # still Nov 21 in Pacific time
    assert complete_days(date(2026, 11, 19), now) == [date(2026, 11, 19), date(2026, 11, 20)]
    assert complete_days(date(2026, 11, 21), now) == []


def _row(fetch_id: int, feed: str, body: bytes | None, **extra):
    return {
        "fetch_id": fetch_id,
        "feed": feed,
        "scheduled_at": "2026-11-19T21:24:40+00:00",
        "requested_at": "2026-11-19T21:24:41.165+00:00" if body is not None else None,
        "responded_at": "2026-11-19T21:24:41.5+00:00" if body is not None else None,
        "latency_ms": 335 if body is not None else None,
        "http_status": 200 if body is not None else None,
        "bytes": len(body) if body is not None else None,
        "sha256": hashlib.sha256(body).hexdigest() if body is not None else None,
        "header_ts": "2026-11-19T21:24:41+00:00" if body is not None else None,
        "entity_count": 20 if body is not None else None,
        "newest_entity_ts": None,
        "error_class": None if body is not None else "timeout",
        "error_msg": None if body is not None else "TimeoutError: timed out",
        "collector_version": "collect/0.1.1",
        "logged_at": "2026-11-19T21:24:41.6+00:00",
        **extra,
    }


def _sample_rows():
    vp = (FIXTURES / "islandtransit_vehiclePositions_20261119T0731.pb").read_bytes()
    tu = (FIXTURES / "islandtransit_tripUpdates_20261119T0731.pb").read_bytes()
    log = [
        _row(1, "vehiclePositions", vp),
        _row(2, "tripUpdates", tu),
        _row(3, "tripUpdates", None),
    ]
    raw = [{**r, "body": b} for r, b in ((log[0], vp), (log[1], tu))]
    return log, raw, vp, tu


def test_raw_digest_matches_the_database_formula():
    log, raw, vp, tu = _sample_rows()
    rows = merge_rows(log, raw)
    expected = hashlib.md5(
        f"1:{hashlib.sha256(vp).hexdigest()},2:{hashlib.sha256(tu).hexdigest()}".encode()
    ).hexdigest()
    assert raw_digest(rows) == expected
    assert raw_digest(list(reversed(rows))) == expected  # order of input does not matter
    assert raw_digest([]) == hashlib.md5(b"").hexdigest()


def test_write_day_round_trips_exact_bytes(tmp_path):
    log, raw, vp, tu = _sample_rows()
    static = [
        {
            "static_fetch_id": 7,
            "source": "passio",
            "sha256": hashlib.sha256(b"zipbytes").hexdigest(),
            "body": b"zipbytes",
        },
        {"static_fetch_id": 8, "source": "trillium", "sha256": None, "body": None},
    ]
    entry = write_day(date(2026, 11, 19), merge_rows(log, raw), static, tmp_path)

    assert entry["fetch_rows"] == 3
    assert entry["raw_rows"] == 2
    assert entry["ok_rows"] == 2
    assert (entry["first_fetch_id"], entry["last_fetch_id"]) == (1, 3)
    names = [f["name"] for f in entry["files"]]
    assert names == [
        "raw-2026-11-19.parquet",
        "fetch-log-2026-11-19.csv",
        "static-2026-11-19-passio-7.zip",
    ]
    for f in entry["files"]:
        data = (tmp_path / f["name"]).read_bytes()
        assert len(data) == f["bytes"]
        assert hashlib.sha256(data).hexdigest() == f["sha256"]

    table = pq.read_table(tmp_path / "raw-2026-11-19.parquet").to_pylist()
    assert [r["fetch_id"] for r in table] == [1, 2, 3]
    assert table[0]["body"] == vp
    assert table[1]["body"] == tu
    assert table[2]["body"] is None and table[2]["error_class"] == "timeout"
    assert table[0]["requested_at"] == datetime(2026, 11, 19, 21, 24, 41, 165000, tzinfo=UTC)
    for r in table[:2]:
        assert hashlib.sha256(r["body"]).hexdigest() == r["sha256"]

    with (tmp_path / "fetch-log-2026-11-19.csv").open() as fh:
        csv_rows = list(csv.DictReader(fh))
    assert len(csv_rows) == 3
    assert "body" not in csv_rows[0]
    assert csv_rows[2]["error_class"] == "timeout"
    assert (tmp_path / "static-2026-11-19-passio-7.zip").read_bytes() == b"zipbytes"
    assert entry["static"][0]["file"] == "static-2026-11-19-passio-7.zip"
    assert "file" not in entry["static"][1]
