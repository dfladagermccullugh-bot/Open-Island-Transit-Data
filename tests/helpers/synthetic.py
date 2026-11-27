"""A small made-up archive with known contents, for tests that run whole stages.

One route runs east along a straight line through four stops, 2.2 km (about
1.4 miles) apart, four minutes from each to the next. Four trips are
scheduled every day:

    T1 06:00   T2 06:20   T3 06:40   T4 07:00   (Pacific time)

Three service days are archived, in the layout ``scripts/rehearse_nightly.py
--replay`` reads (``data-branch/manifest/raw.json`` and ``releases/<tag>/``):

2026-11-24  T1 on time, T2 a minute and a half early, T3 six minutes late,
            T4 never reported. One fetch fails and one times out.
2026-11-25  All four trips reported; T2 loses three minutes of reports in the
            middle of the trip.
2026-11-26  Thanksgiving: no buses, every feed empty. ``reference/
            no-service-days.csv`` lists the day, so ``service_expected`` is
            false.

Nothing here is random and nothing reads the clock, so the files are the same
bytes on every run.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from google.transit import gtfs_realtime_pb2 as rt

from pipeline.archive.build import write_day

REPO = "example/synthetic"
DAYS = (date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 26))
LAT, LON, STEP = 48.2, -122.70, 0.03
STOPS = ("Harbor Station", "Old Depot", "Midway Mall", "Ferry Terminal")
PRINTED = ("Harbor Station", "Midway Mall", "Ferry Terminal")
TRIPS = {"T1": (6, 0), "T2": (6, 20), "T3": (6, 40), "T4": (7, 0)}
LEG_S = 240
RUN_S = LEG_S * (len(STOPS) - 1)
COLLECTOR = "collect/0.3.0"

# (trip, vehicle, seconds late at every stop); a trip left out was not reported.
RUNS: dict[date, tuple[tuple[str, str, int], ...]] = {
    DAYS[0]: (("T1", "B1", 0), ("T2", "B2", -90), ("T3", "B1", 360)),
    DAYS[1]: (("T1", "B1", 20), ("T2", "B2", 0), ("T3", "B1", 150), ("T4", "B2", 0)),
    DAYS[2]: (),
}
# Seconds after T2's real departure during which B2 sends nothing, on day two.
SILENCE = {DAYS[1]: ("T2", 300, 480)}
# Seconds after T1's real departure during which B1's GPS puts it about 5.5 km
# north of the road and back, on day two: a GPS glitch (pipeline/glitch.py).
GLITCH = {DAYS[1]: ("T1", 200, 250, 0.05)}


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), text)
    return buf.getvalue()


def _clock(hour: int, minute: int, plus_s: int) -> str:
    total = hour * 3600 + minute * 60 + plus_s
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
    "ALL,1,1,1,1,1,1,1,20260101,20271231\n"
)


def passio_zip() -> bytes:
    stops = [(f"S{i}", f"{100 + i}", name, LON + STEP * i) for i, name in enumerate(STOPS)]
    return _zip(
        {
            "agency.txt": "agency_id,agency_name\n1,Made-up Transit\n",
            "feed_info.txt": "feed_publisher_name,feed_version\nMade-up Transit,1\n",
            "routes.txt": "route_id,route_short_name,route_long_name\nR1,1,Harbor - Ferry\n",
            "calendar.txt": CALENDAR,
            "trips.txt": "route_id,service_id,trip_id,shape_id\n"
            + "".join(f"R1,ALL,{t},SH\n" for t in TRIPS),
            "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            + "".join(f"SH,{LAT},{lon},{i}\n" for i, (_, _, _, lon) in enumerate(stops)),
            "stops.txt": "stop_id,stop_code,stop_name,stop_lat,stop_lon\n"
            + "".join(f"{s},{code},{name},{LAT},{lon}\n" for s, code, name, lon in stops),
            "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            + "".join(
                f"{t},{_clock(h, m, LEG_S * i)},{_clock(h, m, LEG_S * i)},S{i},{i + 1}\n"
                for t, (h, m) in TRIPS.items()
                for i in range(len(STOPS))
            ),
        }
    )


def trillium_zip() -> bytes:
    """The comparison schedule: other ids, the same stop codes, T2 five minutes later."""
    stops = [(f"X{i}", f"{100 + i}", name, LON + STEP * i) for i, name in enumerate(STOPS)]
    times = {"Q1": (6, 0), "Q2": (6, 25), "Q3": (6, 40), "Q4": (7, 0)}
    return _zip(
        {
            "agency.txt": "agency_id,agency_name\n9,Made-up Transit\n",
            "routes.txt": "route_id,route_short_name,route_long_name\nX1,1,Harbor to Ferry\n",
            "calendar.txt": CALENDAR,
            "trips.txt": "route_id,service_id,trip_id\n" + "".join(f"X1,ALL,{t}\n" for t in times),
            "stops.txt": "stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type\n"
            + "".join(f"{s},{code},{name},{LAT},{lon},\n" for s, code, name, lon in stops),
            "stop_times.txt": (
                "trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint\n"
            )
            + "".join(
                f"{t},{_clock(h, m, LEG_S * i)},{_clock(h, m, LEG_S * i)},X{i},{i + 1},"
                f"{int(STOPS[i] in PRINTED)}\n"
                for t, (h, m) in times.items()
                for i in range(len(STOPS))
            ),
        }
    )


def _pacific(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour + 8, minute, tzinfo=UTC)  # PST


def _feed(now: datetime) -> rt.FeedMessage:
    msg = rt.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.incrementality = rt.FeedHeader.FULL_DATASET
    msg.header.timestamp = int(now.timestamp())
    return msg


def _departures(day: date) -> list[tuple[str, str, datetime, datetime]]:
    """(trip, vehicle, scheduled start, real start) for the day's reported trips."""
    out = []
    for trip, vehicle, late in RUNS[day]:
        start = _pacific(day, *TRIPS[trip])
        out.append((trip, vehicle, start, start + timedelta(seconds=late)))
    return out


def vehicle_feed(day: date, now: datetime) -> bytes:
    msg = _feed(now)
    for trip, vehicle, _, real in _departures(day):
        since = (now - real).total_seconds()
        if not -120 <= since <= RUN_S + 60:
            continue
        quiet = SILENCE.get(day)
        if quiet and quiet[0] == trip and quiet[1] <= since < quiet[2]:
            continue
        done = min(max(since, 0), RUN_S) / RUN_S
        moving = 0 < since < RUN_S
        e = msg.entity.add()
        e.id = vehicle
        v = e.vehicle
        v.trip.trip_id, v.trip.route_id = trip, "R1"
        jump = GLITCH.get(day)
        glitching = jump and jump[0] == trip and jump[1] <= since < jump[2]
        v.position.latitude = LAT + (jump[3] if jump and glitching else 0.0)
        v.position.longitude = LON + STEP * (len(STOPS) - 1) * done
        v.position.bearing = 90.0
        v.position.speed = 9.3 if moving else 0.0
        nxt = min(int(done * (len(STOPS) - 1)) + 1, len(STOPS) - 1) if since > 0 else 0
        v.current_stop_sequence = nxt + 1
        v.stop_id = f"S{nxt}"
        v.timestamp = int(now.timestamp()) - 3
        v.vehicle.id, v.vehicle.label = vehicle, f"Bus {vehicle}"
        v.occupancy_status = rt.VehiclePosition.MANY_SEATS_AVAILABLE
    return msg.SerializeToString()


def trip_update_feed(day: date, now: datetime) -> bytes:
    """Predictions for trips under way or starting within half an hour,
    refreshed on the minute."""
    msg = _feed(now)
    refreshed = int(now.timestamp()) // 60 * 60
    reported = {t: (vehicle, real) for t, vehicle, _, real in _departures(day)}
    for trip, (h, m) in TRIPS.items():
        if not RUNS[day]:
            break
        start = _pacific(day, h, m)
        vehicle, real = reported.get(trip, (None, start))
        if not timedelta(minutes=-30) <= now - real <= timedelta(seconds=RUN_S):
            continue
        e = msg.entity.add()
        e.id = f"tu-{trip}"
        tu = e.trip_update
        tu.trip.trip_id, tu.trip.route_id = trip, "R1"
        if vehicle:
            tu.vehicle.id = vehicle
        tu.timestamp = refreshed
        for i in range(len(STOPS)):
            due = real + timedelta(seconds=LEG_S * i)
            if due.timestamp() < refreshed:
                continue
            stu = tu.stop_time_update.add()
            stu.stop_sequence, stu.stop_id = i + 1, f"S{i}"
            stu.arrival.time = stu.departure.time = int(due.timestamp())
    return msg.SerializeToString()


def _iso(t: datetime) -> str:
    return t.isoformat()


def _log_row(fetch_id: int, feed: str, tick: datetime, body: bytes | None, **extra: Any) -> dict:
    row: dict[str, Any] = {
        "fetch_id": fetch_id,
        "feed": feed,
        "scheduled_at": _iso(tick),
        "requested_at": _iso(tick + timedelta(milliseconds=50)),
        "responded_at": _iso(tick + timedelta(milliseconds=400)),
        "latency_ms": 350,
        "http_status": 200,
        "bytes": len(body) if body is not None else None,
        "sha256": hashlib.sha256(body).hexdigest() if body is not None else None,
        "header_ts": None,
        "entity_count": None,
        "newest_entity_ts": None,
        "error_class": None,
        "error_msg": None,
        "collector_version": COLLECTOR,
        "logged_at": _iso(tick + timedelta(milliseconds=500)),
        "body": body,
    }
    if body is not None:
        msg = rt.FeedMessage.FromString(body)
        stamps = [e.vehicle.timestamp or e.trip_update.timestamp for e in msg.entity]
        row["header_ts"] = _iso(datetime.fromtimestamp(msg.header.timestamp, UTC))
        row["entity_count"] = len(msg.entity)
        row["newest_entity_ts"] = _iso(datetime.fromtimestamp(max(stamps), UTC)) if stamps else None
    return {**row, **extra}


def day_rows(day: date, first_id: int) -> list[dict[str, Any]]:
    """The collector's fetch log for one day: both vehicle feeds every 20
    seconds from 5:50 to 7:40 AM Pacific, alerts every five minutes."""
    rows: list[dict[str, Any]] = []
    tick, end = _pacific(day, 5, 50), _pacific(day, 7, 40)
    n = 0
    while tick < end:
        failed = day == DAYS[0] and n in (30, 31)
        for feed, make in (("vehiclePositions", vehicle_feed), ("tripUpdates", trip_update_feed)):
            fetch_id = first_id + len(rows)
            if failed and feed == "vehiclePositions" and n == 30:
                rows.append(_log_row(fetch_id, feed, tick, None, http_status=503,
                                     error_class="http", error_msg="HTTP 503"))  # fmt: skip
            elif failed and feed == "tripUpdates" and n == 31:
                rows.append(_log_row(fetch_id, feed, tick, None, http_status=None,
                                     responded_at=None, latency_ms=None,
                                     error_class="timeout", error_msg="timed out"))  # fmt: skip
            else:
                rows.append(_log_row(fetch_id, feed, tick, make(day, tick)))
        if n % 15 == 0:
            body = _feed(tick).SerializeToString()
            rows.append(_log_row(first_id + len(rows), "serviceAlerts", tick, body))
        tick += timedelta(seconds=20)
        n += 1
    return rows


def static_rows(day: date, first_id: int) -> list[dict[str, Any]]:
    sources = [("passio", passio_zip())]
    if day == DAYS[0]:
        sources.append(("trillium", trillium_zip()))
    out = []
    for i, (source, body) in enumerate(sources):
        at = _pacific(day, 2, 15 + 5 * i)
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            names = z.namelist()
        out.append(
            {
                "static_fetch_id": first_id + i,
                "source": source,
                "url": f"https://example.org/{source}.zip",
                "requested_at": _iso(at),
                "responded_at": _iso(at + timedelta(milliseconds=600)),
                "latency_ms": 600,
                "http_status": 200,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "zip_has_directory": True,
                "files": names,
                "error_class": None,
                "error_msg": None,
                "collector_version": COLLECTOR,
                "logged_at": _iso(at + timedelta(milliseconds=800)),
                "body": body,
            }
        )
    return out


def reference_files(folder: Path) -> None:
    """The printed timetable (three of the four stops are timepoints) and one
    field timing: an observer saw T1 pass Midway Mall four seconds late on the
    first day."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "timetable-timepoints-2026-09-27.csv").write_text(
        "route_short_name,day_type,direction,order,printed_stop,source_pdf\n"
        + "".join(f"1,weekday,eastbound,{i},{n},guide.pdf\n" for i, n in enumerate(PRINTED, 1))
    )
    (folder / "field-observations.csv").write_text(
        "service_date,stop_id,stop_name,route,trip_id,scheduled,time_this,arrive_40m,"
        "depart_40m,pass_stop,note\n"
        "2026-11-24,S2,Midway Mall,1,T1,06:08,both,,,06:08:04,made-up timing\n"
    )


def build_world(root: Path) -> dict[str, Path]:
    """Write the archive under ``root`` and return its folders."""
    data, store, reference = root / "data-branch", root / "releases", root / "reference"
    (data / "manifest").mkdir(parents=True)
    reference_files(reference)
    manifest: dict[str, Any] = {"version": 1, "repo": REPO, "days": {}}
    fetch_id, static_id = 1, 1
    for day in DAYS:
        tag = f"raw-{day:%Y-%m}"
        rows, statics = day_rows(day, fetch_id), static_rows(day, static_id)
        fetch_id, static_id = fetch_id + len(rows), static_id + len(statics)
        entry = write_day(day, rows, statics, store / tag)
        entry["release_tag"] = tag
        entry["archived_at"] = _iso(_pacific(day + timedelta(days=1), 0, 40))
        entry["code_sha"] = None
        manifest["days"][day.isoformat()] = entry
    (data / "manifest" / "raw.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return {"root": root, "data": data, "releases": store, "reference": reference}
