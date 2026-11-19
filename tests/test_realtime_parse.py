from pathlib import Path

import pytest
from google.protobuf.message import DecodeError

from pipeline.parse.realtime import field_population, parse_feed

FIXTURES = Path(__file__).parent / "fixtures"


def test_empty_after_hours_feed():
    feed = parse_feed((FIXTURES / "empty_feed_20261118.pb").read_bytes())
    assert feed.header_ts == 1795071012
    assert feed.gtfs_realtime_version == "2.0"
    assert feed.incrementality == "FULL_DATASET"
    assert feed.entity_count == 0
    assert feed.vehicles == [] and feed.trip_update_stops == [] and feed.alerts == []


def test_synthetic_feed_rows():
    feed = parse_feed((FIXTURES / "synthetic_feed.pb").read_bytes())
    assert feed.header_ts == 1795100000
    assert feed.entity_count == 3
    assert len(feed.vehicles) == 2
    assert len(feed.trip_updates) == 1
    assert len(feed.trip_update_stops) == 3

    v = feed.vehicles[0]
    assert v["vehicle_id"] == "20001"
    assert v["vehicle_label"] == "301"
    assert v["trip_id"] == "1155211"
    assert v["route_id"] == "9404"
    assert v["vehicle_ts"] == 1795099990
    assert v["lat"] == pytest.approx(48.01, abs=1e-5)
    assert v["lon"] == pytest.approx(-122.51, abs=1e-5)
    assert v["bearing"] == pytest.approx(90.0)
    assert v["current_stop_sequence"] == 1
    assert v["occupancy_status"] == "MANY_SEATS_AVAILABLE"

    s = feed.trip_update_stops[1]
    assert s["stop_sequence"] == 2
    assert s["arrival_time"] == 1795100000 + 120
    assert s["departure_time"] == 1795100000 + 125


def test_unset_fields_are_none_not_defaults():
    """The feed does not send speed, start_date, current_status or delays.
    Those must come out as None, never as 0 or an empty string."""
    feed = parse_feed((FIXTURES / "synthetic_feed.pb").read_bytes())
    v = feed.vehicles[0]
    assert v["speed"] is None
    assert v["start_date"] is None
    assert v["current_status"] is None
    s = feed.trip_update_stops[0]
    assert s["arrival_delay"] is None
    assert s["departure_delay"] is None
    assert s["trip_delay"] is None


def test_field_population():
    feed = parse_feed((FIXTURES / "synthetic_feed.pb").read_bytes())
    pop = field_population(feed.vehicles)
    assert pop["trip_id"] == 1.0
    assert pop["bearing"] == 1.0
    assert pop["speed"] == 0.0
    assert field_population([]) == {}


def test_garbage_raises():
    with pytest.raises(DecodeError):
        parse_feed(b"<html>502 Bad Gateway</html>")
