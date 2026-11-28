"""The period files (``pipeline/periods.py``) and their publishing rule (I8)."""

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

from pipeline import invariants as inv
from pipeline import periods

GOLDEN = Path(__file__).resolve().parent / "golden" / "data-branch"


def day(d: date, on_time=8, late=2, service=True, timetable="2026-09-27"):
    devs = [30] * on_time + [600] * late
    block = {"scheduled": 12, "observed": on_time + late, "on_time": on_time, "early": 0,
             "late": late}  # fmt: skip
    return {
        "service_date": d.isoformat(), "service_expected": service, **block,
        "on_time_share_of_observed": round(on_time / (on_time + late), 4),
        "observed_share": round((on_time + late) / 12, 4),
        "timepoints": f"passio-x__timetable-{timetable}-abc",
        "by_hour": {"7": {**block, "deviations_s": devs}},
        "by_route_stop": {"R": {"S": {**block, "deviations_s": devs}}},
    }  # fmt: skip


def run_of(n, start=date(2026, 11, 2), **kw):
    return [day(start + timedelta(days=k), **kw) for k in range(n)]


def test_headline_waits_for_14_days_before_comparing():
    h = periods.headline(run_of(30))
    assert h["current"]["days"] == 28 and h["previous"] is None and h["change_points"] is None
    h = periods.headline(run_of(28, on_time=6, late=4) + run_of(28, start=date(2026, 11, 30)))
    assert h["previous"]["days"] == 28
    assert h["change_points"] == 20.0  # 60% then 80%


def test_headline_spans_a_timetable_change_and_skips_holidays():
    days = run_of(10) + run_of(10, start=date(2026, 11, 12), timetable="2026-12-27")
    days[5]["service_expected"] = False
    h = periods.headline(days)
    assert h["current"]["days"] == 19
    assert h["timetables"] == ["2026-09-27", "2026-12-27"]
    assert h["days_without_service"] == ["2026-11-07"]


def test_weeks_start_on_monday_and_are_marked_partial():
    p = periods.ontime_periods(run_of(10, start=date(2026, 11, 4)))  # Wednesday to Friday
    weeks = p["grains"]["week"]
    assert [w["period"] for w in weeks] == ["2026-11-02", "2026-11-09"]
    assert [w["partial"] for w in weeks] == [True, True]
    assert weeks[0]["days"] == 5 and weeks[0]["scheduled"] == 60
    assert p["grains"]["month"][0]["partial"] is True


def test_hour_medians_come_from_the_pooled_deviations():
    p = periods.ontime_periods(run_of(3))
    row = p["grains"]["hour"]["pooled"]["rows"][0]
    assert (
        row["hour"] == 7 and row["deviations_counted"] == 30 and row["deviation_s_median"] == 30.0
    )


def test_the_golden_period_files_pass_and_a_changed_headline_fails(tmp_path):
    data = tmp_path / "data-branch"
    shutil.copytree(GOLDEN, data)
    assert [p for p in inv.run(data, light_only=True).problems if p.startswith("I8")] == []
    path = data / "ontime" / "headline.json"
    h = json.loads(path.read_text())
    h["current"]["on_time"] += 1
    path.write_text(json.dumps(h))
    assert any(p.startswith("I8") for p in inv.run(data, light_only=True).problems)


def _with_trips(d, trips):
    return {**d, "by_trip": {f"T{k}": list(c) for k, c in enumerate(trips)}}


def test_the_headline_interval_resamples_whole_trips_and_repeats_exactly():
    days = [_with_trips(x, [(4, 4), (4, 0), (2, 1)] * 10) for x in run_of(10)]
    a, b = periods.trip_interval(days), periods.trip_interval(days)
    assert a == b and a["method"] == "bootstrap over trips" and a["trips"] == 300
    share = sum(c[1] for d in days for c in d["by_trip"].values()) / sum(
        c[0] for d in days for c in d["by_trip"].values())  # fmt: skip
    assert a["low"] < share < a["high"]
    # Fewer trips, a wider interval.
    few = periods.trip_interval(days[:1])
    assert few["high"] - few["low"] > a["high"] - a["low"]


def test_no_interval_when_a_day_has_no_counts_per_trip():
    days = [_with_trips(x, [(4, 4), (4, 0)]) for x in run_of(3)]
    days[1].pop("by_trip")
    assert periods.trip_interval(days) is None
    assert periods.headline(days)["current"]["on_time_share_interval95"] is None


# --- how sure the headline is (periods/0.3.0) --------------------------------------


def test_the_worst_case_range_counts_every_untimed_departure_late_then_on_time():
    b = periods.bounds({"scheduled": 12, "observed": 10, "on_time": 8})
    assert (b["low"], b["high"], b["untimed"]) == (round(8 / 12, 4), round(10 / 12, 4), 2)


def test_small_cells_merge_into_their_route_and_band_then_route_then_all():
    cells = {"R|midday|middle": [20, 15, 12], "R|midday|first": [5, 4, 4],
             "R|midday|last": [8, 7, 7], "R|early|middle": [3, 2, 2],
             "S|midday|middle": [3, 0, 0]}  # fmt: skip
    t = periods.merge_cells(cells)
    assert t["R|midday|middle"] == "R|midday|middle"
    # 4 + 7 timed: enough together for their route and band.
    assert t["R|midday|first"] == t["R|midday|last"] == "R|midday|*"
    # 2 timed on route R in the early band and nothing on route S: all left over.
    assert t["R|early|middle"] == t["S|midday|middle"] == "*|*|*"


def test_the_weighted_share_counts_a_cell_by_what_was_scheduled():
    # 90% on time where everything was timed, 50% where one in ten was:
    # unweighted 70%, weighted (10 * 0.9 + 100 * 0.5) / 110.
    value, top, left_out = periods.weighted_share({"A": [10, 10, 9], "B": [100, 10, 5]})
    assert round(value, 4) == round(59 / 110, 4) and top == 10 and left_out == 0


def test_a_cell_with_nothing_timed_is_left_out_and_counted():
    value, top, left_out = periods.weighted_share({"A": [10, 0, 0], "B": [20, 10, 5]})
    assert (value, top, left_out) == (0.5, 2.0, 10)
    assert periods.weighted_share({"A": [10, 0, 0]}) == (None, None, 10)


def _with_cells(d, trips):
    """A day whose departures fall in cells: ``trips`` maps a trip to its
    cells' [scheduled, observed, on_time]."""
    by_cell: dict = {}
    for cells in trips.values():
        for c, v in cells.items():
            by_cell[c] = [a + b for a, b in zip(by_cell.get(c, [0, 0, 0]), v, strict=True)]
    sched, seen, on_time = (sum(v[i] for v in by_cell.values()) for i in range(3))
    untimed = sched - seen
    return {**d, "scheduled": sched, "observed": seen, "on_time": on_time, "late": seen - on_time,
            "early": 0, "by_cell": by_cell, "by_trip_cell": trips,
            "by_trip": {t: [sum(v[1] for v in c.values()), sum(v[2] for v in c.values())]
                        for t, c in trips.items() if any(v[1] for v in c.values())},
            "by_position": {"first": {"scheduled": 0, "observed": 0},
                            "middle": {"scheduled": sched, "observed": seen},
                            "last": {"scheduled": 0, "observed": 0}},
            "untimed": {"departures": untimed, "by_reason": {"gap": untimed},
                        "by_position": {"first": {}, "middle": {"gap": untimed},
                                        "last": {}}}}  # fmt: skip


def test_the_headline_says_how_sure_it_is_three_ways_and_repeats_exactly():
    trips = {f"T{k}": {"R|midday|middle": [4, 4, 3]} for k in range(20)}
    trips |= {f"U{k}": {"R|evening|middle": [4, 2, 1]} for k in range(20)}
    days = [_with_cells(x, trips) for x in run_of(5)]
    cur = periods.headline(days)["current"]
    assert cur["untimed"]["departures"] == 5 * 40 and cur["untimed"]["by_reason"]["gap"] == 200
    assert (cur["bounds"]["low"], cur["bounds"]["high"]) == (0.5, 0.75)
    # Midday 75% on time, evening 50%, half the schedule each: 62.5% weighted,
    # where the timed departures alone give 400 / 600 = 66.7%.
    a = cur["adjusted"]
    assert a["value"] == 0.625 and cur["on_time_share_of_observed"] == 0.6667
    assert a["max_weight"] == 2 and a["shown"] is True
    assert a["interval95"]["low"] <= 0.625 <= a["interval95"]["high"]
    assert periods.headline(days)["current"]["adjusted"] == a
    res = inv.Result()
    inv.check_headline_ranges(res, cur, days)
    assert res.problems == []
    cur["adjusted"]["value"] = 0.4
    inv.check_headline_ranges(res, cur, days)
    assert any("weighted share" in p for p in res.problems)


def test_the_weighted_share_is_hidden_when_a_weight_reaches_ten():
    trips = {f"T{k}": {"R|midday|middle": [1, 1, 1]} for k in range(20)}
    trips |= {f"U{k}": {"R|evening|middle": [10, 1, 0]} for k in range(10)}
    cur = periods.headline([_with_cells(x, trips) for x in run_of(1)])["current"]
    assert cur["adjusted"]["max_weight"] == 10 and cur["adjusted"]["shown"] is False


def test_reasons_and_cells_need_the_new_day_files():
    days = run_of(3)
    cur = periods.headline(days)["current"]
    assert cur["untimed"] is None and cur["adjusted"] is None
    assert cur["bounds"]["low"] == round(24 / 36, 4)


def test_one_list_of_untimed_reasons():
    from pipeline.columns import QUALITY_HEADLINE
    from pipeline.events.ontime import UNTIMED_REASONS

    cols = [n.removeprefix("untimed_") for n, _ in QUALITY_HEADLINE if n.startswith("untimed_")]
    assert tuple(cols) == UNTIMED_REASONS == periods.UNTIMED_REASONS
    assert set(UNTIMED_REASONS) < set(periods.REASON_NAMES)


# --- highlights and since (periods/0.4.0) ------------------------------------------


def _late_days(n=5):
    """Route A on time 9 of 10 at midday, route B 2 of 10 in the evening."""
    trips = {f"A{k}": {"A|midday|middle": [10, 10, 9]} for k in range(10)}
    trips |= {f"B{k}": {"B|evening|middle": [10, 10, 2]} for k in range(10)}
    days = []
    for x in run_of(n):
        d = _with_cells(x, trips)
        d["by_route"] = {"A": {"observed": 100, "on_time": 90},
                         "B": {"observed": 100, "on_time": 20}}  # fmt: skip
        d["routes"] = {"A": {"short_name": "1", "long_name": "One"},
                       "B": {"short_name": "58", "long_name": "Fifty-eight"}}  # fmt: skip
        d["by_route_stop"] = {"B": {"FERRY": {"scheduled": 10, "observed": 8, "on_time": 6,
                                              "position": "last"}}}  # fmt: skip
        days.append(d)
    return days


FERRY = [{"stop_id": "FERRY", "stop_name": "Ferry", "terminal": "Clinton",
          "ferry_route": "Mukilteo/Clinton", "bus_routes": "58 99"}]  # fmt: skip


def test_highlights_name_what_is_clearly_below_the_system_and_repeat_exactly():
    days = _late_days()
    h = periods.highlights(days, FERRY)
    assert h == periods.highlights(days, FERRY)
    assert h["system"] == {"observed": 1000, "on_time": 550, "on_time_share_of_observed": 0.55}
    late = h["running_late"]
    # Route B (20%) and the evening (the same departures) are named; route A
    # and midday (90%) are not.
    named = [(i["kind"], i["key"]) for i in late["items"]]
    assert named == [("route", "B"), ("time_of_day", "evening")]
    assert late["items"][0]["short_name"] == "58" and late["items"][1]["hours"] == [18, 24]
    assert all(i["interval95"]["high"] < 0.55 for i in late["items"])
    assert {g["key"]: g["below_system"] for g in late["groups"]} == {
        "A": False, "B": False, "midday": False, "evening": True} | {"B": True}  # fmt: skip


def test_highlights_need_enough_departures_and_name_at_most_three():
    trips = {f"T{r}{k}": {f"{r}|midday|middle": [1, 1, 0 if r != "OK" else 1]}
             for r in ("OK", "W", "X", "Y", "Z") for k in range(30)}  # fmt: skip
    trips |= {"S": {"S|evening|middle": [9, 9, 0]}}  # 9 departures: too few to name
    days = [_with_cells(x, trips) for x in run_of(1)]
    late = periods.highlights(days, [])["running_late"]
    assert len(late["items"]) == 3
    assert all(i["kind"] == "route" and i["key"] in "WXYZ" for i in late["items"])
    assert "S" not in {g["key"] for g in late["groups"]}


def test_nothing_is_named_when_nothing_is_clearly_worse():
    trips = {f"T{k}": {f"{'AB'[k % 2]}|midday|middle": [4, 4, 3]} for k in range(40)}
    late = periods.highlights([_with_cells(x, trips) for x in run_of(3)], [])["running_late"]
    assert late["items"] == [] and len(late["groups"]) == 3


def test_ferry_terminals_sum_their_routes_and_say_which_are_missing():
    (t,) = periods.highlights(_late_days(), FERRY)["ferry_terminals"]
    assert (t["observed"], t["on_time"], t["on_time_share_of_observed"]) == (40, 30, 0.75)
    assert t["routes"] == [{"route_id": "B", "short_name": "58", "position": "last",
                            "scheduled": 50, "observed": 40, "on_time": 30}]  # fmt: skip
    assert t["routes_not_found"] == ["99"]


def test_the_reference_list_of_ferry_terminals():
    rows = periods.read_ferry_terminals()
    assert {r["terminal"]: r["bus_routes"].split() for r in rows} == {
        "Clinton": ["1", "58", "60"], "Coupeville": ["6"]}  # fmt: skip


def test_since_covers_the_whole_archive():
    days = run_of(30)
    days[3]["service_expected"] = False
    raw = {d["service_date"]: {"ok_rows": 100} for d in days} | {"2027-01-01": {"ok_rows": 7}}
    s = periods.headline(days, raw)["since"]
    assert s == {"first_service_date": "2026-11-02", "last_service_date": "2026-12-01",
                 "days": 30, "service_days": 29, "departures_scheduled": 29 * 12,
                 "departures_timed": 290, "departures_on_time": 232,
                 "feed_responses_archived": 3000}  # fmt: skip


def test_i8_checks_the_highlights_and_since(tmp_path):
    data = tmp_path / "data-branch"
    shutil.copytree(GOLDEN, data)
    assert (data / "ontime" / "highlights.json").exists()
    assert [p for p in inv.run(data, light_only=True).problems if p.startswith("I8")] == []

    def broken(path, change):
        shutil.rmtree(data)
        shutil.copytree(GOLDEN, data)
        obj = json.loads((data / path).read_text())
        change(obj)
        (data / path).write_text(json.dumps(obj))
        return [p for p in inv.run(data, light_only=True).problems if p.startswith("I8")]

    def more_timed(h):
        h["since"]["departures_timed"] += 1

    def wrong_group(h):
        h["running_late"]["groups"][0]["on_time"] += 1

    def named_anyway(h):
        g = h["running_late"]["groups"][0]
        h["running_late"]["items"] = [{**g, "below_system": True}]

    def ferry_off(h):
        h["ferry_terminals"][0]["on_time"] += 1

    assert any("since" in p for p in broken("ontime/headline.json", more_timed))
    assert any("not the sum" in p for p in broken("ontime/highlights.json", wrong_group))
    assert broken("ontime/highlights.json", named_anyway)
    assert any("ferry" in p for p in broken("ontime/highlights.json", ferry_off))
