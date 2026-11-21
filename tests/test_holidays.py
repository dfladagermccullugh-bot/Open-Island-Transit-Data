import json
from datetime import date

from pipeline.events.routes import write_routes
from pipeline.gtfs.holidays import last_listed, no_service, service_expected


def test_listed_days_have_no_service(tmp_path):
    assert no_service(date(2026, 11, 26))["holiday"] == "Thanksgiving Day"
    assert no_service("2026-12-25")["source"].startswith("https://www.islandtransit.org/")
    assert service_expected(date(2026, 11, 27))
    f = tmp_path / "days.csv"
    f.write_text("service_date,holiday,source,read_on\n2027-02-15,Snow,https://x,2027-02-15\n")
    assert not service_expected(date(2027, 2, 15), f)
    assert service_expected(date(2026, 11, 26), tmp_path / "missing.csv")


def _day(day, expected=True, observed=8):
    block = {"scheduled": 10, "observed": observed, "on_time": observed, "early": 0, "late": 0,
             "observed_share": observed / 10,
             "on_time_share_of_observed": 1.0 if observed else None,
             "deviation_s_median": 30.0 if observed else None}  # fmt: skip
    return {"service_date": day, "service_expected": expected, "ontime_version": "ontime/0.3.0",
            **block, "by_route": {"R1": block}, "routes": {"R1": {"short_name": "1"}},
            "by_route_stop": {"R1": {"S1": {**block, "name": "Stop", "stop_sequence_median": 1,
                                            "deviations_s": [30] * observed}}}}  # fmt: skip


def test_route_summary_leaves_out_days_without_service(tmp_path):
    (tmp_path / "days").mkdir()
    for d in (_day("2026-11-25"), _day("2026-11-26", expected=False, observed=0)):
        (tmp_path / "days" / f"{d['service_date']}.json").write_text(json.dumps(d))
    assert write_routes(tmp_path) == 2
    recent = json.loads((tmp_path / "recent.json").read_text())
    assert recent["days"] == 1 and recent["days_without_service"] == ["2026-11-26"]
    assert recent["overall"]["scheduled"] == 10 and recent["overall"]["observed_share"] == 0.8
    assert len(recent["routes"]["R1"]["daily"]) == 1


def test_last_listed_is_the_latest_date_in_the_file(tmp_path):
    assert last_listed() >= date(2027, 12, 25)
    f = tmp_path / "days.csv"
    f.write_text(
        "service_date,holiday,source,read_on\n"
        "2027-02-15,Snow,https://x,2027-02-15\n2026-12-25,Christmas Day,https://x,2026-11-21\n"
    )
    assert last_listed(f) == date(2027, 2, 15)
    assert last_listed(tmp_path / "missing.csv") is None
