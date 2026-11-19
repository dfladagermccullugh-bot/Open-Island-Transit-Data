from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
from google.transit import gtfs_realtime_pb2 as rt

from pipeline.tables.__main__ import pending
from pipeline.tables.build import TABLES_VERSION, build_tables, write_tables

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 19, 20, 0, tzinfo=UTC)


def vehicle_feed(*vehicles: tuple[str, str, int, float]) -> bytes:
    """vehicles: (entity_id, vehicle_id, vehicle_ts, lat)"""
    msg = rt.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = 1
    for entity_id, vehicle_id, ts, lat in vehicles:
        e = msg.entity.add()
        e.id = entity_id
        e.vehicle.vehicle.id = vehicle_id
        e.vehicle.timestamp = ts
        e.vehicle.trip.trip_id = "T1"
        e.vehicle.position.latitude = lat
        e.vehicle.position.longitude = -122.6
    return msg.SerializeToString()


def fetch(fetch_id: int, body: bytes | None, feed="vehiclePositions", status=200, error=None):
    return {
        "fetch_id": fetch_id,
        "feed": feed,
        "requested_at": T0 + timedelta(seconds=20 * fetch_id),
        "http_status": status,
        "error_class": error,
        "body": body,
    }


def vehicles(rows):
    return build_tables(rows).tables["vehicles"].to_pylist()


def test_unchanged_position_is_one_row():
    body = vehicle_feed(("1", "V1", 1000, 48.2))
    out = vehicles([fetch(i, body) for i in (1, 2, 3)])
    assert len(out) == 1
    r = out[0]
    assert (r["first_fetch_id"], r["last_fetch_id"], r["times_seen"]) == (1, 3, 3)
    assert r["vehicle_ts"] == datetime.fromtimestamp(1000, UTC)
    assert r["first_requested_at"] == T0 + timedelta(seconds=20)


def test_new_vehicle_timestamp_starts_a_new_row():
    rows = [
        fetch(1, vehicle_feed(("1", "V1", 1000, 48.2))),
        fetch(2, vehicle_feed(("1", "V1", 1000, 48.2))),
        fetch(3, vehicle_feed(("1", "V1", 1015, 48.3))),
    ]
    out = vehicles(rows)
    assert [(r["vehicle_ts"].timestamp(), r["times_seen"]) for r in out] == [(1000, 2), (1015, 1)]


def test_entity_id_shift_does_not_split_a_run():
    rows = [
        fetch(1, vehicle_feed(("1", "V1", 1000, 48.2))),
        fetch(2, vehicle_feed(("1", "V0", 999, 48.0), ("2", "V1", 1000, 48.2))),
    ]
    out = {r["vehicle_key"]: r for r in vehicles(rows)}
    assert out["V1"]["times_seen"] == 2


def test_failed_fetch_ends_the_run():
    body = vehicle_feed(("1", "V1", 1000, 48.2))
    rows = [fetch(1, body), fetch(2, None, status=None, error="timeout"), fetch(3, body)]
    built = build_tables(rows)
    assert [r["times_seen"] for r in built.tables["vehicles"].to_pylist()] == [1, 1]
    assert built.fetches["vehiclePositions"] == {
        "attempted": 3,
        "parsed": 2,
        "failed": 1,
        "decode_errors": 0,
    }


def test_missing_item_ends_the_run():
    with_v1 = vehicle_feed(("1", "V1", 1000, 48.2))
    without = vehicle_feed(("1", "V2", 1000, 48.1))
    out = [
        r
        for r in vehicles([fetch(1, with_v1), fetch(2, without), fetch(3, with_v1)])
        if r["vehicle_key"] == "V1"
    ]
    assert [(r["first_fetch_id"], r["last_fetch_id"]) for r in out] == [(1, 1), (3, 3)]


def test_other_feeds_do_not_interrupt_a_run():
    body = vehicle_feed(("1", "V1", 1000, 48.2))
    rows = [
        fetch(1, body),
        fetch(2, None, feed="tripUpdates", status=500, error="http"),
        fetch(3, body),
    ]
    assert [r["times_seen"] for r in vehicles(rows)] == [2]


def test_decode_error_is_counted_and_breaks_the_run():
    body = vehicle_feed(("1", "V1", 1000, 48.2))
    built = build_tables([fetch(1, body), fetch(2, b"\xff\xff not protobuf"), fetch(3, body)])
    assert built.fetches["vehiclePositions"]["decode_errors"] == 1
    assert built.tables["vehicles"].num_rows == 2


def test_rows_in_any_order():
    a = vehicle_feed(("1", "V1", 1000, 48.2))
    b = vehicle_feed(("1", "V1", 1015, 48.3))
    rows = [fetch(1, a), fetch(2, a), fetch(3, b)]
    assert vehicles(rows[::-1]) == vehicles(rows)


def test_duplicate_item_in_one_message_is_kept():
    body = vehicle_feed(("1", "V1", 1000, 48.2), ("2", "V1", 1001, 48.25))
    built = build_tables([fetch(1, body)])
    assert built.tables["vehicles"].num_rows == 2
    assert built.duplicate_keys["vehicles"] == 1


def test_real_samples():
    trip = (FIXTURES / "islandtransit_tripUpdates_20261119T0731.pb").read_bytes()
    veh = (FIXTURES / "islandtransit_vehiclePositions_20261119T0731.pb").read_bytes()
    alerts = (FIXTURES / "islandtransit_serviceAlerts_20261119T0731.pb").read_bytes()
    rows = [
        fetch(1, veh),
        fetch(2, trip, feed="tripUpdates"),
        fetch(3, alerts, feed="serviceAlerts"),
        fetch(4, veh),
        fetch(5, trip, feed="tripUpdates"),
    ]
    built = build_tables(rows)
    t = built.tables
    assert (t["vehicles"].num_rows, t["trip-updates"].num_rows) == (20, 30)
    assert t["trip-update-stops"].num_rows == 544
    assert t["alerts"].num_rows == 0
    assert set(t["trip-update-stops"].column("times_seen").to_pylist()) == {2}
    stop = next(
        r
        for r in t["trip-update-stops"].to_pylist()
        if r["trip_id"] == "1155202" and r["stop_sequence"] == 1
    )
    assert stop["arrival_time"] == datetime.fromtimestamp(1795102601, UTC)


def test_write_tables(tmp_path):
    body = vehicle_feed(("1", "V1", 1000, 48.2))
    entry = write_tables(date(2026, 11, 19), build_tables([fetch(1, body)]), tmp_path)
    names = [f["name"] for f in entry["files"]]
    assert names == [
        "vehicles-2026-11-19.parquet",
        "trip-updates-2026-11-19.parquet",
        "trip-update-stops-2026-11-19.parquet",
        "alerts-2026-11-19.parquet",
    ]
    assert entry["rows"]["vehicles"] == 1
    table = pq.read_table(tmp_path / names[0])
    assert table.schema.metadata[b"tables_version"] == TABLES_VERSION.encode()
    assert table.column("lat").to_pylist() == [table.column("lat").to_pylist()[0]]
    assert pq.read_table(tmp_path / names[3]).num_rows == 0


def test_pending_rebuilds_old_versions_and_skips_unreleased():
    raw = {
        "days": {
            "2026-11-19": {"release_tag": "raw-2026-11"},
            "2026-11-20": {"release_tag": "raw-2026-11"},
            "2026-11-21": {"release_tag": "raw-2026-11"},
            "2026-11-22": {},
        }
    }
    parsed = {
        "days": {
            "2026-11-19": {"tables_version": TABLES_VERSION},
            "2026-11-20": {"tables_version": "tables/0.0.1"},
        }
    }
    assert pending(raw, parsed) == [date(2026, 11, 20), date(2026, 11, 21)]
