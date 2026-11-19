import io
import zipfile
from pathlib import Path

from pipeline.gtfs.versions import add_download, fingerprint, pin_days
from pipeline.gtfs.zipread import read_zip

FIXTURES = Path(__file__).parent / "fixtures"


def gtfs_zip(feed_start="20261118", stop_name="Harbor", directory=True, calendar=True) -> bytes:
    files = {
        "agency.txt": "agency_id,agency_name\n1,Island Transit\n",
        "stops.txt": f"stop_id,stop_name\n1,{stop_name}\n",
        "routes.txt": "route_id\nR1\n",
        "trips.txt": "route_id,service_id,trip_id\nR1,S1,T1\n",
        "stop_times.txt": "trip_id,stop_sequence,stop_id\nT1,1,1\n",
        "feed_info.txt": f"feed_publisher_name,feed_start_date\nPassio,{feed_start}\n",
    }
    if calendar:
        files["calendar.txt"] = "service_id,monday\nS1,1\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    data = buf.getvalue()
    if not directory:  # cut at the central directory, as Passio sometimes serves it
        data = data[: data.index(b"PK\x01\x02")]
    return data


def fp(data: bytes, source="passio"):
    return fingerprint(source, read_zip(data))


def test_rolling_feed_info_dates_do_not_change_the_version():
    a, b = fp(gtfs_zip("20261118")), fp(gtfs_zip("20261119"))
    assert a["complete"] and b["complete"]
    assert a["version"] == b["version"]
    assert a["version"].startswith("passio-")
    assert b["feed_info"]["feed_start_date"] == "20261119"


def test_schedule_change_changes_the_version():
    assert fp(gtfs_zip(stop_name="Harbor"))["version"] != fp(gtfs_zip(stop_name="Port"))["version"]


def test_incomplete_zip_has_no_version():
    f = fp(gtfs_zip(directory=False, calendar=False))
    assert not f["complete"]
    assert f["version"] is None
    assert f["missing"] == ["calendar.txt or calendar_dates.txt"]


def test_fixture_zips():
    assert fp((FIXTURES / "gtfs_complete.zip").read_bytes())["complete"]
    assert not fp((FIXTURES / "gtfs_no_directory.zip").read_bytes())["complete"]


def _add(reg, fetch_id, day, hour, data, source="passio"):
    download = {
        "static_fetch_id": fetch_id,
        "source": source,
        "day": day,
        "requested_at": f"{day}T{hour:02d}:00:00+00:00",
        "release_tag": "raw-2026-11",
        "file": f"static-{day}-{source}-{fetch_id}.zip",
        "sha256": "x",
    }
    add_download(reg, download, fp(data, source))


def test_pinning_rules():
    reg: dict = {}
    v1, v2 = gtfs_zip(stop_name="Harbor"), gtfs_zip(stop_name="Port")
    night_partial = gtfs_zip(stop_name="Port", directory=False, calendar=False)
    _add(reg, 1, "2026-11-19", 21, v1)  # day 19: complete
    _add(reg, 2, "2026-11-20", 10, night_partial)  # day 20: partial at night ...
    _add(reg, 3, "2026-11-20", 16, v2)  # ... then complete, changed
    _add(reg, 4, "2026-11-21", 10, night_partial)  # day 21: only a partial
    _add(reg, 5, "2026-11-23", 10, b"not a zip")  # day 23: unreadable
    days = pin_days(reg, ["2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"])

    vid1, vid2 = fp(v1)["version"], fp(v2)["version"]
    p = {d: days[d]["passio"] for d in days}
    assert p["2026-11-19"]["version"] == vid1
    assert p["2026-11-20"] == {
        "version": vid2,
        "basis": "complete download that day",
        "changed_during_day": False,
    }
    assert p["2026-11-21"]["version"] == vid2
    assert p["2026-11-21"]["basis"].startswith("incomplete download that day")
    assert p["2026-11-22"] == {
        "version": vid2,
        "basis": "carried forward from 2026-11-20",
        "changed_during_day": False,
    }
    assert p["2026-11-23"]["basis"] == "carried forward from 2026-11-20"
    assert reg["versions"][vid2]["downloads"] == [3]
    assert reg["downloads"]["2"]["complete"] is False


def test_two_versions_in_one_day_is_flagged():
    reg: dict = {}
    _add(reg, 1, "2026-11-20", 10, gtfs_zip(stop_name="Harbor"))
    _add(reg, 2, "2026-11-20", 16, gtfs_zip(stop_name="Port"))
    pinned = pin_days(reg, ["2026-11-20"])["2026-11-20"]["passio"]
    assert pinned["changed_during_day"] is True
    assert pinned["version"] == fp(gtfs_zip(stop_name="Harbor"))["version"]


def test_sources_are_pinned_separately():
    reg: dict = {}
    _add(reg, 1, "2026-11-19", 21, gtfs_zip(), source="passio")
    _add(reg, 2, "2026-11-19", 21, gtfs_zip(), source="trillium")
    pinned = pin_days(reg, ["2026-11-19", "2026-11-20"])
    assert pinned["2026-11-19"]["trillium"]["version"].startswith("trillium-")
    assert pinned["2026-11-20"]["trillium"]["basis"] == "carried forward from 2026-11-19"


def test_no_version_yet():
    reg: dict = {}
    _add(reg, 1, "2026-11-20", 10, b"not a zip")
    pinned = pin_days(reg, ["2026-11-20"])["2026-11-20"]["passio"]
    assert pinned == {
        "version": None,
        "basis": "no complete version yet",
        "changed_during_day": False,
    }
