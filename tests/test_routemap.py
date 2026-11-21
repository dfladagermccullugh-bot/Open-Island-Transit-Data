import json

from pipeline.events.routes import write_routes
from pipeline.gtfs import routemap
from pipeline.gtfs.zipread import read_zip
from tests.test_events import gtfs_zip


def test_simplify_drops_collinear_points_and_keeps_corners():
    lats = [48.0, 48.0, 48.0, 48.01, 48.02]
    lons = [-122.5, -122.49, -122.48, -122.48, -122.48]
    assert routemap.simplify(lats, lons) == [0, 2, 4]


def test_build_route_map():
    first = {"passio_route_id": "R1", "passio_stop_id": "S1", "printed_stop": "First St"}
    rows = [first, first, {"passio_route_id": "R1", "passio_stop_id": "", "printed_stop": "X"}]
    m = routemap.build(read_zip(gtfs_zip()), rows)
    assert m["map_version"] == routemap.MAP_VERSION
    (r,) = m["routes"]
    assert (r["route_id"], r["name"], r["trips"], r["color"]) == ("R1", "1", 1, "#777777")
    assert len(r["paths"]) == 1 and len(r["paths"][0]) == 2  # straight line: ends only
    (s,) = m["timepoint_stops"]
    assert (s["stop_id"], s["name"], s["routes"]) == ("S1", "First St", ["R1"])
    assert m["bbox"][0] < m["bbox"][2]


def block(sch, obs, on, early, late, devs=None, name=""):
    b = {"scheduled": sch, "observed": obs, "on_time": on, "early": early, "late": late,
         "observed_share": obs / sch, "on_time_share_of_observed": on / obs,
         "deviation_s_median": 60.0}  # fmt: skip
    if devs is not None:
        b |= {"name": name, "stop_sequence_median": 3, "deviations_s": devs}
    return b


def test_write_routes_pools_window(tmp_path):
    days = tmp_path / "days"
    days.mkdir()
    for d, devs in (("2026-10-01", [999]), ("2026-11-19", [0, 60]), ("2026-11-20", [600])):
        n = len(devs)
        late = sum(x > 300 for x in devs)
        day = {"service_date": d, "ontime_version": "ontime/0.2.0",
               "definition": {"window_s": [-60, 300]},
               **block(n, n, n - late, 0, late),
               "routes": {"R1": {"short_name": "1", "long_name": "One"}},
               "by_route": {"R1": block(n, n, n - late, 0, late)},
               "by_route_stop": {"R1": {"S1": block(n, n, n - late, 0, late, devs, "First")}},
               }  # fmt: skip
        (days / f"{d}.json").write_text(json.dumps(day))
    assert write_routes(tmp_path) == 3
    lines = (tmp_path / "routes.csv").read_text().splitlines()
    assert len(lines) == 4 and lines[1].startswith("2026-10-01,R1,1,")
    recent = json.loads((tmp_path / "recent.json").read_text())
    assert recent["days"] == 2  # 2026-10-01 is outside the 28-day window
    r = recent["routes"]["R1"]
    assert (r["scheduled"], r["on_time"], r["late"]) == (3, 2, 1)
    assert r["deviation_s_median"] == 60.0
    assert [x["service_date"] for x in r["daily"]] == ["2026-11-19", "2026-11-20"]
    assert r["stops"][0]["name"] == "First" and r["stops"][0]["deviations_counted"] == 3
