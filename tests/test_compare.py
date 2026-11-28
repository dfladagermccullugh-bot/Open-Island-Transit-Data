"""Comparisons with other bus systems (``pipeline/compare.py``)."""

import json
import shutil
from pathlib import Path

from pipeline import compare

GOLDEN = Path(__file__).resolve().parent / "golden" / "data-branch"
# Minutes late of 100 departures: 10 early, 60 within 0 to 3, 20 within 3
# to 5, 10 later.
DEVS = [-120] * 10 + [60] * 60 + [240] * 20 + [600] * 10


def test_a_recount_under_another_rule_uses_its_window():
    assert compare.share_within(DEVS, 1, 5) == 0.8
    assert compare.share_within(DEVS, 2, 5) == 0.9
    assert compare.share_within(DEVS, 1, 3) == 0.6


def test_a_figure_under_our_own_rule_converts_to_itself():
    r = compare.converted_range([x + k for x in DEVS for k in range(0, 60, 7)], (1, 5), 0.8)
    assert r["low"] is not None and abs(r["low"] - 0.8) < 0.02 and abs(r["high"] - 0.8) < 0.02


def test_a_stricter_rule_converts_to_a_higher_share_under_ours():
    """80% on time within 3 minutes is better service than 80% within 5."""
    r = compare.converted_range(DEVS, (1, 3), 0.8)
    assert r["low"] is not None and r["low"] >= 0.8


def test_the_summary_labels_each_peer_with_its_classes(tmp_path):
    data = tmp_path / "data-branch"
    shutil.copytree(GOLDEN, data)
    peers = tmp_path / "peers.csv"
    peers.write_text(
        "agency,region,country,window_early_min,window_late_min,measured_moment,measured_where,"
        "cancellations,published_share,published_period,metric_note,source_urls,source_date,confidence\n"
        "Rule only,Washington,US,1,5,departure,timepoints,,,,,https://example.org,2026,low\n"
        "Rule and figure,Europe,UK,1,5,departure,timepoints,excluded,0.5,2025,,https://example.org,2026,high\n"
        "Headways,Asia,SG,,,,,,0.95,2025,excess wait time,https://example.org,2026,high\n"
    )
    out = compare.write(data, peers)
    saved = json.loads((data / "compare" / "summary.json").read_text())
    assert saved == out
    classes = {p["agency"]: p["classes"] for p in out["peers"]}
    assert classes == {"Rule only": ["R"], "Rule and figure": ["R", "B"], "Headways": []}
    assert out["island_transit"]["departures"] > 0


def _sample(day: str, start: str, moments: int, on_time: int, trips: int = 6) -> dict:
    per, rest = divmod(moments, trips)
    by_trip = {f"T{k}": [per + (k < rest), 0] for k in range(trips)}
    left = on_time
    for v in by_trip.values():
        v[1] = min(v[0], left)
        left -= v[1]
    return {"sample_version": "compare-sample/0.3.0", "service_date": day,
            "sample_start": start, "sample_end": start.replace("T19", "T21"),
            "agency_timezone": "America/Los_Angeles", "local_weekday": None,
            "local_hours": None, "trips_measured": trips, "moments_measured": moments,
            "moments_on_time": on_time,
            "on_time_share": round(on_time / moments, 4) if moments else None,
            "trips_reported": trips + 1, "trips_in_schedule": trips,
            "by_trip": by_trip}  # fmt: skip


def _peer_world(tmp_path, samples):
    data = tmp_path / "data-branch"
    shutil.copytree(GOLDEN, data)
    peers = tmp_path / "peers.csv"
    peers.write_text(
        "agency,region,country,window_early_min,window_late_min,measured_moment,measured_where,"
        "cancellations,published_share,published_period,metric_note,source_urls,source_date,confidence\n"
        "Sampled,Washington,US,,,,,,,,,https://example.org,2026,low\n"
        "Not sampled,Washington,US,,,,,,,,,https://example.org,2026,low\n"
    )
    feeds = tmp_path / "feeds.csv"
    feeds.write_text(
        "agency_id,agency,region,rt_url,static_url,note\nsam,Sampled,Washington,x,y,\n"
    )
    (data / "compare" / "sam").mkdir(parents=True)
    for s in samples:
        name = s["sample_start"][:10]
        (data / "compare" / "sam" / f"{name}.json").write_text(json.dumps(s))
    return data, peers, feeds


def test_the_samples_of_the_last_eight_weeks_are_pooled(tmp_path):
    data, peers, feeds = _peer_world(tmp_path, [
        _sample("2026-10-01", "2026-10-01T19:17:00+00:00", 100, 10),  # older than 8 weeks
        _sample("2026-11-24", "2026-11-24T19:17:00+00:00", 40, 30),
        _sample("2026-11-27", "2026-11-27T19:17:00+00:00", 50, 50),  # day after Thanksgiving
        _sample("2026-12-01", "2026-12-01T19:17:00+00:00", 60, 45),
        _sample("2026-12-03", "2026-12-03T19:17:00+00:00", 0, 0),  # no buses measured
    ])  # fmt: skip
    out = compare.write(data, peers, feeds)
    rows = {p["agency"]: p for p in out["peers"]}
    assert rows["Sampled"]["classes"] == ["A"] and rows["Not sampled"]["classes"] == []
    pool = rows["Sampled"]["our_method_on_their_feed"]
    assert pool["samples_pooled"] == 2
    assert pool["service_dates"] == ["2026-11-24", "2026-12-01"]
    assert (pool["moments_measured"], pool["moments_on_time"]) == (100, 75)
    assert pool["on_time_share"] == 0.75
    lo, hi = pool["on_time_interval_95"]
    assert lo is not None and lo < 0.75 < hi
    assert pool["share_in_schedule"] == round(12 / 14, 4)
    assert [s["reason"] for s in pool["samples_skipped"]] == [
        "the day after Thanksgiving Day",
        "no departure measured",
    ]
    # Each sample is listed, with the weekday and clock hours it covered.
    assert [s["local_weekday"] for s in pool["samples"]] == ["Tuesday", "Tuesday"]
    assert pool["hours"] == {"Tuesday": [11, 12, 13]}
    same = rows["Sampled"]["island_same_hours"]
    assert all(d.startswith("2026-11") for d in same["service_dates"])


def test_a_sample_older_than_its_version_still_pools(tmp_path):
    old = _sample("2026-12-01", "2026-12-01T19:17:00+00:00", 60, 45)
    for k in ("by_trip", "moments_on_time", "local_weekday", "local_hours"):
        old.pop(k)
    data, peers, feeds = _peer_world(tmp_path, [old])
    pool = compare.write(data, peers, feeds)["peers"][0]["our_method_on_their_feed"]
    assert pool["moments_on_time"] == 45 and pool["hours"] == {"Tuesday": [11, 12, 13]}
    assert pool["on_time_interval_95"] == [None, None]  # no trips to resample


def test_samples_on_or_next_to_a_federal_holiday_are_skipped():
    assert compare.skip_reason("2026-11-26") == "Thanksgiving Day"
    assert compare.skip_reason("2026-11-27") == "the day after Thanksgiving Day"
    assert compare.skip_reason("2026-11-25") == "the day before Thanksgiving Day"
    assert compare.skip_reason("2026-12-01") is None


def test_island_transit_is_recounted_over_the_peers_weekdays_and_hours():
    def day(d):
        return {"service_date": d, "by_hour": {
            "11": {"deviations_s": [0, 400]}, "12": {"deviations_s": [10]},
            "13": {"deviations_s": [20]}}}  # fmt: skip

    days = [day(d) for d in ("2026-11-24", "2026-11-25", "2026-12-01", "2026-12-02")]
    out = compare.same_hours(days, {"Tuesday": [11], "Wednesday": [12]}, "2026-11-20", "2026-12-02")
    # 2026-11-25, the day before Thanksgiving, is left out as it is for the peers.
    assert out["service_dates"] == ["2026-11-24", "2026-12-01", "2026-12-02"]
    assert out["departures"] == 5 and out["recount_under_our_rule"] == 0.6


def test_the_recount_under_our_rule_is_the_published_count_less_early_arrivals(tmp_path):
    data = tmp_path / "data-branch"
    shutil.copytree(GOLDEN, data)
    it = compare.write(data, tmp_path / "none.csv", tmp_path / "none.csv")["island_transit"]
    days = [json.loads(p.read_text()) for p in sorted((data / "ontime" / "days").glob("*.json"))]
    early = sum(d["last_stop_early_arrivals"] for d in days if d.get("service_expected", True))
    assert it["last_stop_early_arrivals"] == early
    assert it["recount_on_time"] == it["on_time"] - early
    assert it["recount_under_our_rule"] == round(it["recount_on_time"] / it["observed"], 4)
    assert it["headline_on_time_share"] == round(it["on_time"] / it["observed"], 4)
