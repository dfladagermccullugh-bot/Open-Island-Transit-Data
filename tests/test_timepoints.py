import csv
import io
import zipfile
from datetime import date

from pipeline.gtfs import timepoints
from pipeline.gtfs.zipread import read_zip


def passio_zip() -> bytes:
    files = {
        "routes.txt": "route_id,route_short_name\nR1,1\nNA,\nNB,\n",
        "stops.txt": (
            "stop_id,stop_name\n"
            "A1,Harbor Station Transfer Center Bayshore Dr\n"
            "B1,Walmart at Erie St\nB2,Walmart at Erie St\n"  # one per direction
            "C1,SR 525 at Greenbank\n"
            "N1,Midway St at Hangar 6\n"
        ),
        "trips.txt": "route_id,service_id,trip_id\nR1,S,south\nR1,S,north\nNA,S,am\nNB,S,pm\n",
        "stop_times.txt": (
            "trip_id,stop_id,stop_sequence\n"
            "south,A1,1\nsouth,B1,2\nsouth,C1,3\n"
            "north,C1,1\nnorth,B2,2\nnorth,A1,3\n"
            "am,N1,1\npm,N1,1\n"
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def printed(route, direction, *names):
    return [
        {
            "route_short_name": route,
            "day_type": "weekday",
            "direction": direction,
            "order": str(i),
            "printed_stop": n,
        }
        for i, n in enumerate(names, 1)
    ]


def ids(rows, printed_stop):
    return sorted({r["passio_stop_id"] for r in rows if r["printed_stop"] == printed_stop})


def test_name_matching_and_direction():
    rows = timepoints.match(
        read_zip(passio_zip()),
        printed(
            "1",
            "southbound",
            "Harbor Station at SE Bayshore Dr",
            "SW Erie St at Walmart",
            "SR 525 at Greenbank",
        ),
        {},
    )
    assert ids(rows, "Harbor Station at SE Bayshore Dr") == ["A1"]
    # Two stops share the name; the southbound trip serves B1.
    assert ids(rows, "SW Erie St at Walmart") == ["B1"]
    assert all(r["passio_route_id"] == "R1" for r in rows)


def test_overrides_and_unresolved():
    overrides = {
        ("1", "Walmart"): {"passio_stop_ids": "B1;B2", "note": "both"},
        ("1", "CVS"): {"passio_stop_ids": "", "note": "unresolved: landmark"},
        ("1", "Old stop"): {"passio_stop_ids": "Z9", "note": "removed since"},
    }
    rows = timepoints.match(
        read_zip(passio_zip()), printed("1", "loop", "Walmart", "CVS", "Old stop", "Nowhere"),
        overrides,
    )  # fmt: skip
    assert ids(rows, "Walmart") == ["B1", "B2"]
    how = {r["printed_stop"]: r["match"] for r in rows if not r["passio_stop_id"]}
    assert how == {
        "CVS": "unresolved: landmark",
        "Old stop": "unresolved: override stop ids are not in this Passio version",
        "Nowhere": "unresolved: no name match",
    }
    s = timepoints.summarize(rows)
    assert (s["printed_names"], s["resolved_names"]) == (4, 1)


def test_shared_timetable_counts_on_every_route():
    nas = printed("", "loop", "Midway St at Hangar 6")
    rows = timepoints.match(read_zip(passio_zip()), nas, {})
    assert timepoints.timepoint_pairs(rows) == {("NA", "N1"), ("NB", "N1")}


def test_reference_for_picks_latest_in_effect(tmp_path):
    for d in ("2026-09-27", "2026-12-27"):
        (tmp_path / f"timetable-timepoints-{d}.csv").write_text("x\n")
    assert timepoints.reference_for(date(2026, 9, 26), tmp_path) is None
    assert timepoints.reference_for(date(2026, 12, 26), tmp_path) == date(2026, 9, 27)
    assert timepoints.reference_for(date(2026, 12, 27), tmp_path) == date(2026, 12, 27)


def test_repository_reference_files_are_consistent():
    refs = timepoints.references()
    assert date(2026, 9, 27) in refs
    for effective, (printed_path, overrides_path) in refs.items():
        rows = list(csv.DictReader(printed_path.open()))
        assert rows and {"route_short_name", "day_type", "direction", "order",
                         "printed_stop"} <= set(rows[0])  # fmt: skip
        names = {(r["route_short_name"], r["printed_stop"]) for r in rows}
        assert overrides_path.exists(), f"no overrides file for {effective}"
        for o in csv.DictReader(overrides_path.open()):
            assert (o["route_short_name"], o["printed_stop"]) in names, o
            assert o["passio_stop_ids"] or o["note"], o


def test_timepoints_follow_the_day_type_where_a_route_prints_one():
    from datetime import date

    from pipeline.gtfs.timepoints import day_type_applies, timepoint_pairs

    rows = [
        {"passio_route_id": "A", "passio_stop_id": "1", "day_type": "weekday"},
        {"passio_route_id": "A", "passio_stop_id": "2", "day_type": "saturday"},
        {"passio_route_id": "B", "passio_stop_id": "3", "day_type": "weekday AM"},
        {"passio_route_id": "B", "passio_stop_id": "", "day_type": "weekday"},
    ]
    friday, saturday, sunday = date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22)
    assert timepoint_pairs(rows) == {("A", "1"), ("A", "2"), ("B", "3")}
    assert timepoint_pairs(rows, friday) == {("A", "1"), ("B", "3")}
    assert timepoint_pairs(rows, saturday) == {("A", "2"), ("B", "3")}  # B prints no Saturday
    # Route A prints nothing for Sunday: every printed stop counts.
    assert timepoint_pairs(rows, sunday) == {("A", "1"), ("A", "2"), ("B", "3")}
    assert day_type_applies("weekend", sunday) and not day_type_applies("weekend", friday)


def test_reference_of_reads_the_timetable_date_from_a_folder_name():
    key = "passio-9b1efa882037__timetable-2026-09-27-970aa207"
    assert timepoints.reference_of(key) == "2026-09-27"
    assert timepoints.reference_of(None) is None
    assert timepoints.reference_of("something else") is None


def two_way_zip() -> bytes:
    """Route 60 in miniature: each direction stops on its own side of the
    road, and the southbound stops carry the names closest to the printed
    ones, as at Bayview P&R and the high school in the real schedule."""
    files = {
        "routes.txt": "route_id,route_short_name\nR60,60\n",
        "stops.txt": (
            "stop_id,stop_name\n"
            "F,Freeland at Main St\n"
            "BS,Bayview P&R\nBN,Bayview P&R\n"
            "MS,Maxwelton Rd at SW High School\nMN,South Whidbey High School\n"
            "K,Clinton Ferry Terminal\n"
        ),
        "trips.txt": "route_id,service_id,trip_id,direction_id\nR60,S,sb,1\nR60,S,nb,0\n",
        "stop_times.txt": (
            "trip_id,stop_id,stop_sequence\n"
            "sb,F,1\nsb,BS,2\nsb,MS,3\nsb,K,4\n"
            "nb,K,1\nnb,MN,2\nnb,BN,3\nnb,F,4\n"
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


NORTHBOUND = ("Clinton Ferry Terminal", "Maxwelton Rd at South Whidbey HS", "Bayview P&R",
              "Freeland at Main Street")  # fmt: skip


def test_each_direction_gets_the_stops_its_own_buses_use():
    """Before timepoints/0.2.0 the northbound timetable was matched to the
    southbound stops across the road (route 60 at Bayview P&R), where
    northbound buses never stop, so they were never scored there."""
    rows = timepoints.match(
        read_zip(two_way_zip()),
        printed("60", "southbound", *reversed(NORTHBOUND))
        + printed("60", "northbound", *NORTHBOUND),
        {},
    )
    north = [r for r in rows if r["direction"] == "northbound"]
    south = [r for r in rows if r["direction"] == "southbound"]
    assert ids(north, "Bayview P&R") == ["BN"]
    assert ids(north, "Maxwelton Rd at South Whidbey HS") == ["MN"]
    assert ids(south, "Bayview P&R") == ["BS"]
    assert {r["passio_direction_id"] for r in north} == {"0"}
    assert {r["passio_direction_id"] for r in south} == {"1"}
    keys = timepoints.timepoint_keys(rows)
    assert ("R60", "0", "BN") in keys and ("R60", "0", "BS") not in keys
    assert timepoints.is_timepoint(keys, "R60", "1", "BS")
    assert not timepoints.is_timepoint(keys, "R60", "1", "BN")
    assert timepoints.is_timepoint(keys, "R60", "0", "K")  # a terminal both directions use


def test_an_override_naming_both_sides_keeps_each_directions_own():
    overrides = {("60", "Bayview P&R"): {"passio_stop_ids": "BS;BN", "note": "both sides"}}
    rows = timepoints.match(
        read_zip(two_way_zip()),
        printed("60", "southbound", *reversed(NORTHBOUND))
        + printed("60", "northbound", *NORTHBOUND),
        overrides,
    )
    by_dir = {
        r["direction"]: r["passio_stop_id"] for r in rows if r["printed_stop"] == "Bayview P&R"
    }
    assert by_dir == {"northbound": "BN", "southbound": "BS"}


def test_a_list_without_directions_counts_in_either_direction():
    old = [{"passio_route_id": "R60", "passio_stop_id": "BS", "day_type": "weekday"}]
    keys = timepoints.timepoint_keys(old)
    assert timepoints.is_timepoint(keys, "R60", "0", "BS")
    assert timepoints.is_timepoint(keys, "R60", "1", "BS")
