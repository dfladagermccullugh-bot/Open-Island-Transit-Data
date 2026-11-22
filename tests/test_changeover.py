"""A schedule change, end to end through the static stage.

December 26 runs on the old schedule. On December 27 the night download is
still the old schedule and the day download is the new one, while the buses
already report the new trip ids. December 28 has only the new schedule. A
new printed timetable takes effect on December 27.
"""

import io
import json
import zipfile
from datetime import date
from functools import partial

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline import release
from pipeline.common import sha256_file
from pipeline.events.__main__ import pending as events_pending
from pipeline.events.infer import EVENTS_VERSION
from pipeline.events.ontime import ONTIME_VERSION
from pipeline.events.validate import VALIDATE_VERSION
from pipeline.gtfs import __main__ as static
from pipeline.gtfs import timepoints
from pipeline.gtfs.versions import fingerprint, load_overrides
from pipeline.gtfs.zipread import read_zip
from pipeline.quality.__main__ import pending as quality_pending
from pipeline.quality.metrics import QUALITY_VERSION

LAT, LON = 48.2, -122.70
DAYS = ("2026-12-26", "2026-12-27", "2026-12-28")
HEADER = "route_short_name,day_type,direction,order,printed_stop,source_pdf\n"


def passio_zip(trip_id: str, second_stop: str) -> bytes:
    names = ("Harbor Station", second_stop, "Midway Mall", "Ferry Terminal")
    stops = [(f"S{i}", name, LON + 0.03 * i) for i, name in enumerate(names)]
    files = {
        "agency.txt": "agency_id,agency_name\n1,IT\n",
        "routes.txt": "route_id,route_short_name\nR1,1\n",
        "calendar.txt": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\nALL,1,1,1,1,1,1,1,20260101,20271231\n"
        ),
        "trips.txt": f"route_id,service_id,trip_id,shape_id\nR1,ALL,{trip_id},SH\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        + "".join(f"SH,{LAT},{lon},{i}\n" for i, (_, _, lon) in enumerate(stops)),
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(f"{s},{name},{LAT},{lon}\n" for s, name, lon in stops),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        + "".join(
            f"{trip_id},06:{4 * i:02d}:00,06:{4 * i:02d}:00,{s},{i + 1}\n"
            for i, (s, _, _) in enumerate(stops)
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def printed(second_stop: str) -> str:
    names = ("Harbor Station", second_stop, "Ferry Terminal")
    return HEADER + "".join(
        f"1,weekday,eastbound,{i},{n},guide.pdf\n" for i, n in enumerate(names, 1)
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The archive after December 28: raw and parsed manifests, release files
    in a folder, and a reference folder with both timetables."""
    store, data, ref = tmp_path / "store", tmp_path / "data", tmp_path / "reference"
    for folder in (store, data / "manifest", ref):
        folder.mkdir(parents=True)
    (ref / "timetable-timepoints-2026-09-27.csv").write_text(printed("Old Depot"))
    (ref / "timetable-timepoints-2026-12-27.csv").write_text(printed("New Depot"))

    zips = {"old": passio_zip("T-old", "Old Depot"), "new": passio_zip("T-new", "New Depot")}
    downloads = (
        (1, "2026-12-26", "10:15", "old"),
        (2, "2026-12-27", "10:15", "old"),  # 2:15 AM Pacific: not switched yet
        (3, "2026-12-27", "16:30", "new"),
        (4, "2026-12-28", "10:15", "new"),
    )
    raw: dict = {
        "days": {d: {"release_tag": "raw-2026-12", "files": [], "static": []} for d in DAYS}
    }
    for fetch_id, day, clock, which in downloads:
        name = f"static-{day}-passio-{fetch_id}.zip"
        (store / name).write_bytes(zips[which])
        sha = sha256_file(store / name)
        raw["days"][day]["files"].append({"name": name, "sha256": sha})
        raw["days"][day]["static"].append(
            {"static_fetch_id": fetch_id, "source": "passio", "file": name, "sha256": sha,
             "requested_at": f"{day}T{clock}:00+00:00"}
        )  # fmt: skip

    parsed: dict = {"days": {}}
    for day, trip in zip(DAYS, ("T-old", "T-new", "T-new"), strict=True):
        name = f"vehicles-{day}.parquet"
        pq.write_table(pa.table({"trip_id": [trip, trip, None]}), store / name)
        files = [{"name": name, "sha256": sha256_file(store / name)}]
        parsed["days"][day] = {
            "release_tag": "parsed-2026-12",
            "files": files,
            "tables_version": "tables/1",
        }
    (data / "manifest" / "parsed.json").write_text(json.dumps(parsed))

    monkeypatch.setattr(release, "fetch", lambda tag, repo, name, dest, sha256: store / name)
    version = {k: fingerprint("passio", read_zip(z))["version"] for k, z in zips.items()}
    return {"tmp": tmp_path, "data": data, "ref": ref, "raw": raw, "parsed": parsed, "v": version}


def run_static(world, overrides=None, problems=None):
    data = world["data"]
    path = data / "manifest" / "static.json"
    registry = static.load(path)
    evidence = partial(
        static.live_match,
        registry=registry,
        parsed=world["parsed"],
        raw_dir=world["tmp"] / "raw",
        tables_dir=world["tmp"] / "tables",
        repo="x/y",
    )
    static.update(
        registry, world["raw"], world["tmp"] / "raw", "x/y",
        evidence=evidence, overrides=overrides, problems=problems,
    )  # fmt: skip
    static.write_crosswalks(registry, data / "crosswalk", world["tmp"] / "raw", "x/y")
    tps = static.write_timepoints(
        registry, data / "timepoints", world["tmp"] / "raw", "x/y", world["ref"]
    )
    maps = static.write_maps(
        registry, data / "timepoints", data / "maps", world["tmp"] / "raw", "x/y"
    )
    static.save(path, registry)
    return registry, tps, maps


def test_the_change_day_is_measured_against_the_new_schedule(world):
    registry, tps, maps = run_static(world)
    old, new = world["v"]["old"], world["v"]["new"]
    pins = {d: registry["days"][d]["passio"] for d in DAYS}
    assert [pins[d]["version"] for d in DAYS] == [old, new, new]
    change = pins["2026-12-27"]
    assert change["changed_during_day"] is True
    assert change["chosen_by"] == "scheduled trips seen in the live feed"
    assert [(c["version"], c["scheduled_trips_seen"]) for c in change["candidates"]] == [
        (old, 0),
        (new, 1),
    ]

    keys = [registry["days"][d]["timepoints"] for d in DAYS]
    assert keys[0].startswith(f"{old}__timetable-2026-09-27-")
    assert keys[1] == keys[2] and keys[1].startswith(f"{new}__timetable-2026-12-27-")
    assert sorted(tps) == sorted(set(keys)) == sorted(maps)
    for key, stop in ((keys[0], "Old Depot"), (keys[1], "New Depot")):
        folder = world["data"] / "timepoints" / key
        summary = json.loads((folder / "summary.json").read_text())
        assert summary["reference"] == key.split("__timetable-")[1][:10]
        assert stop in (folder / "timepoints.csv").read_text()
        assert (world["data"] / "maps" / f"{key}.json").exists()

    # A second run finds everything in place and keeps every pin.
    again, tps, maps = run_static(world)
    assert (tps, maps) == ([], [])
    assert again["days"] == registry["days"]


def _mark_built(world, registry, key_of):
    """Record every day as built by events and quality with ``key_of(day)``
    as its timepoints folder, as an earlier run would have left it."""
    data = world["data"]
    events: dict = {"version": 1, "days": {}}
    (data / "quality" / "days").mkdir(parents=True, exist_ok=True)
    for day in DAYS:
        key = key_of(day)
        vehicles = world["parsed"]["days"][day]["files"][0]["sha256"]
        events["days"][day] = {
            "release_tag": "parsed-2026-12",
            "files": [{"name": f"stop-events-{day}.parquet", "sha256": "e"}],
            "events_version": EVENTS_VERSION,
            "ontime_version": ONTIME_VERSION,
            "validate_version": VALIDATE_VERSION,
            "timepoints": key,
            "service_expected": True,
            "vehicles_sha256": vehicles,
        }
        (data / "quality" / "days" / f"{day}.json").write_text(
            json.dumps(
                {
                    "quality_version": QUALITY_VERSION,
                    "schedule": {"passio_version": key.split("__")[0], "service_expected": True},
                    "processing": {"timepoints": {"folder": key}},
                    "inputs": {
                        "stop_events_sha256": "e",
                        "vehicles_sha256": vehicles,
                        "tables_version": "tables/1",
                    },
                }
            )
        )
    (data / "manifest" / "events.json").write_text(json.dumps(events))
    return events


def _pending(world, events):
    data = world["data"]
    registry = json.loads((data / "manifest" / "static.json").read_text())
    return (
        [d.isoformat() for d in events_pending(data)],
        [
            d.isoformat()
            for d in quality_pending(world["parsed"], registry, data / "quality", events)
        ],
    )


def test_days_built_before_the_new_timetable_was_added_are_rebuilt(world):
    # First every day is built with only the September timetable on file ...
    december = world["ref"] / "timetable-timepoints-2026-12-27.csv"
    text = december.read_text()
    december.unlink()
    registry, _, _ = run_static(world)
    events = _mark_built(world, registry, lambda day: registry["days"][day]["timepoints"])
    assert _pending(world, events) == ([], [])
    # ... then the December list is committed: only days from its date on wait.
    december.write_text(text)
    run_static(world)
    assert _pending(world, events) == (["2026-12-27", "2026-12-28"],) * 2


def test_an_override_rebuilds_its_day(world, tmp_path):
    registry, _, _ = run_static(world)
    events = _mark_built(world, registry, lambda day: registry["days"][day]["timepoints"])
    assert _pending(world, events) == ([], [])
    path = tmp_path / "static-pin-overrides.csv"
    path.write_text(
        "service_date,source,version,reason,reviewed_on\n"
        f"2026-12-26,passio,{world['v']['new']},test,2026-12-29\n"
    )
    problems: list[str] = []
    moved, _, _ = run_static(world, load_overrides(path), problems)
    assert problems == []
    assert (
        moved["days"]["2026-12-26"]["passio"]["override"]["replaced_version"] == world["v"]["old"]
    )
    assert _pending(world, events) == (["2026-12-26"],) * 2


def test_reference_id_follows_its_own_files_only(world):
    ref = world["ref"]
    september, december = date(2026, 9, 27), date(2026, 12, 27)
    before = (static._reference_id(september, ref), static._reference_id(december, ref))
    (ref / "timetable-overrides-2026-12-27.csv").write_text(
        "route_short_name,printed_stop,passio_stop_ids,note\n1,New Depot,S1,test\n"
    )
    assert static._reference_id(december, ref) != before[1]
    assert static._reference_id(september, ref) == before[0]
    (ref / "timetable-timepoints-2027-03-01.csv").write_text(printed("Newer Depot"))
    assert static._reference_id(september, ref) == before[0]
    assert timepoints.reference_for(date(2026, 12, 26), ref) == september
    assert timepoints.reference_for(december, ref) == december


def test_the_published_september_folder_name_is_unchanged():
    # Days already published point at this folder; a different hash would
    # rebuild every one of them.
    assert static._reference_id(date(2026, 9, 27)) == "timetable-2026-09-27-970aa207"


def test_the_daily_table_is_rewritten_even_when_no_day_is_rebuilt(tmp_path, monkeypatch):
    """A change in how the table is written (true for True) must reach the
    published file on the next run, not wait for the next rebuild of a day."""
    import sys

    from pipeline.events import __main__ as events_main

    data = tmp_path / "data"
    (data / "manifest").mkdir(parents=True)
    for name in ("parsed", "static", "events"):
        (data / "manifest" / f"{name}.json").write_text(json.dumps({"days": {}}))
    (data / "ontime" / "days").mkdir(parents=True)
    day = {
        "service_date": "2026-11-26",
        "scheduled": 12,
        "observed": 0,
        "service_expected": False,
        "ontime_version": "ontime/0.3.0",
        "by_route": {},
    }
    (data / "ontime" / "days" / "2026-11-26.json").write_text(json.dumps(day))
    (data / "ontime" / "daily.csv").write_text("an older way of writing the table\n")
    monkeypatch.setattr(sys, "argv", ["events", "--pending", "--data", str(data)])
    assert events_main.main() == 0
    lines = (data / "ontime" / "daily.csv").read_text().splitlines()
    assert lines[0].startswith("service_date,scheduled,observed")
    assert lines[1] == "2026-11-26,12,0,,,,,,,,false,ontime/0.3.0"
