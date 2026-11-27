"""Class A comparisons: our method on a sample of another feed
(``pipeline/compare_sample.py``), checked on the made-up two-trip schedule."""

import io
import zipfile
from datetime import UTC, datetime, timedelta

from pipeline import compare_sample as cs
from pipeline.events import infer
from pipeline.gtfs import schedule
from pipeline.gtfs.zipread import read_zip
from tests.test_continuity import run
from tests.test_events import T0


def schedule_with_zone(zone: str):
    from tests.test_continuity import two_trips_zip

    original = two_trips_zip()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in original.files.items():
            text = data if isinstance(data, str) else data.decode()
            if name == "agency.txt":
                text = f"agency_id,agency_name,agency_timezone\n1,IT,{zone}\n"
            z.writestr(name, text)
    return read_zip(buf.getvalue())


def test_a_sample_is_scored_as_our_own_figures_are():
    contents = schedule_with_zone("America/Los_Angeles")
    start = datetime.fromtimestamp(T0 - 600, UTC)
    out = cs.summarize(contents, run(-60), start, start + timedelta(hours=1), "made-up")
    assert out["trips_measured"] == 1 and out["moments_measured"] >= 3
    # The same share our own on-time code gives for these events.
    keys, _ = cs.timepoint_keys(contents)
    events, _ = infer.infer_day(start.astimezone(schedule.PACIFIC).date(), contents, run(-60), keys)
    devs = [
        d for ds in cs.deviations(events, start, start + timedelta(hours=1)).values() for d in ds
    ]
    assert out["on_time_share"] == cs.on_time(devs) and 0 < out["on_time_share"] < 1
    assert set(out["by_stop_radius_m"]) == {"30", "40", "50"}
    assert out["timepoints"].startswith("every stop")
    assert out["on_time_interval_95"] == [None, None]  # too few trips for an interval
    # The module's settings are put back after the run.
    assert infer.STOP_RADIUS_M == 40.0 and schedule.PACIFIC.key == "America/Los_Angeles"


def test_the_bootstrap_resamples_whole_trips():
    by_trip = {f"T{k}": [30.0] * 5 if k % 2 else [900.0] * 5 for k in range(20)}
    lo, hi = cs.bootstrap(by_trip)
    assert lo is not None and 0.2 < lo < 0.5 < hi < 0.8


def test_a_schedule_that_cannot_be_used_stops_the_sample_before_it_starts():
    # A download that is not a GTFS zip (an error page, say) reads as empty.
    assert "no trips" in cs.schedule_problem(read_zip(b"<html>not a zip</html>"))
    assert "no time zone" in cs.schedule_problem(schedule_with_zone(""))
    assert "unknown time zone" in cs.schedule_problem(schedule_with_zone("Mars/Olympus"))
    assert cs.schedule_problem(schedule_with_zone("America/Halifax")) is None


def test_a_sample_says_which_clock_it_used_and_how_many_trips_matched():
    contents = schedule_with_zone("America/Los_Angeles")
    start = datetime.fromtimestamp(T0 - 600, UTC)
    out = cs.summarize(contents, run(-60), start, start + timedelta(hours=1), "made-up")
    assert out["agency_timezone"] == "America/Los_Angeles"
    assert out["trips_reported"] >= 1
    assert out["trips_in_schedule"] == out["trips_reported"]
