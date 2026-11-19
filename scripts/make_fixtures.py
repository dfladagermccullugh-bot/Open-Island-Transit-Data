"""Build the small synthetic fixtures used by the tests.

Real feed samples are added to tests/fixtures/ by scripts/verify_feed.py.
These synthetic ones exist so tests have known, hand-checkable answers.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as rt

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

HEADER_TS = 1_795_100_000
VEHICLE_TS = (1_795_099_990, 1_795_099_996)
TRIP_UPDATE_TS = 1_795_099_998


def synthetic_feed() -> bytes:
    msg = rt.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.incrementality = rt.FeedHeader.FULL_DATASET
    msg.header.timestamp = HEADER_TS

    for i, ts in enumerate(VEHICLE_TS, start=1):
        e = msg.entity.add()
        e.id = str(i)
        v = e.vehicle
        v.trip.trip_id = f"115521{i}"
        v.trip.route_id = "9404"
        v.position.latitude = 48.0 + i / 100
        v.position.longitude = -122.5 - i / 100
        v.position.bearing = 90.0 * i
        v.current_stop_sequence = i
        v.stop_id = f"17831{i}"
        v.timestamp = ts
        v.vehicle.id = f"2000{i}"
        v.vehicle.label = f"30{i}"
        v.occupancy_status = rt.VehiclePosition.MANY_SEATS_AVAILABLE

    e = msg.entity.add()
    e.id = "3"
    tu = e.trip_update
    tu.trip.trip_id = "1155211"
    tu.trip.route_id = "9404"
    tu.vehicle.id = "20001"
    tu.timestamp = TRIP_UPDATE_TS
    for seq in (1, 2, 3):
        stu = tu.stop_time_update.add()
        stu.stop_sequence = seq
        stu.stop_id = f"1783{seq}0"
        stu.arrival.time = HEADER_TS + 60 * seq
        stu.departure.time = HEADER_TS + 60 * seq + 5
    return msg.SerializeToString()


def empty_feed() -> bytes:
    """Byte-for-byte what Island Transit returned after hours on 2026-11-18."""
    return bytes.fromhex("0a0d0a03322e30100018a4b8fad706")


def gtfs_zip(complete: bool) -> bytes:
    """A tiny GTFS zip. With complete=False it is cut off after the last local
    entry, which reproduces the incomplete Passio zip seen on 2026-11-18:
    no calendar files and no central directory."""
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n1,Test,https://example.org,America/Los_Angeles\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nA,First,48.0,-122.5\nB,Second,48.1,-122.6\n",
        "routes.txt": "route_id,route_short_name,route_type\nR1,1,3\n",
        "trips.txt": "route_id,service_id,trip_id\nR1,WK,T1\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nT1,06:00:00,06:00:00,A,1\nT1,06:10:00,06:10:00,B,2\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nWK,1,1,1,1,1,0,0,20260101,20271231\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            if not complete and name == "calendar.txt":
                continue
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 18, 22, 50, 12))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    data = buf.getvalue()
    if complete:
        return data
    return data[: data.index(b"PK\x01\x02")]  # drop the central directory


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "synthetic_feed.pb").write_bytes(synthetic_feed())
    (FIXTURES / "empty_feed_20261118.pb").write_bytes(empty_feed())
    (FIXTURES / "gtfs_complete.zip").write_bytes(gtfs_zip(True))
    (FIXTURES / "gtfs_no_directory.zip").write_bytes(gtfs_zip(False))
    for p in sorted(FIXTURES.iterdir()):
        print(f"{p.name:28s} {p.stat().st_size:6d} bytes")


if __name__ == "__main__":
    main()
