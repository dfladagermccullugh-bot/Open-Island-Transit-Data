from pathlib import Path

from pipeline.gtfs.zipread import read_zip

FIXTURES = Path(__file__).parent / "fixtures"


def test_complete_zip_is_valid():
    z = read_zip((FIXTURES / "gtfs_complete.zip").read_bytes())
    assert z.has_directory
    assert z.problems == []
    assert z.missing_required == []
    assert z.is_valid_gtfs
    assert z.files["routes.txt"].startswith(b"route_id,")


def test_zip_without_directory_is_recovered_and_flagged():
    """Reproduces the Passio zip of 2026-11-18: entries intact, no calendar,
    no central directory."""
    z = read_zip((FIXTURES / "gtfs_no_directory.zip").read_bytes())
    assert not z.has_directory
    assert not z.is_valid_gtfs
    assert sorted(z.files) == [
        "agency.txt",
        "routes.txt",
        "stop_times.txt",
        "stops.txt",
        "trips.txt",
    ]
    assert z.missing_required == ["calendar.txt or calendar_dates.txt"]
    assert any("central directory" in p for p in z.problems)
    # Recovered content is byte-identical to the complete zip's.
    full = read_zip((FIXTURES / "gtfs_complete.zip").read_bytes())
    for name, body in z.files.items():
        assert body == full.files[name]


def test_entry_cut_off_mid_stream_is_reported_not_returned():
    data = (FIXTURES / "gtfs_no_directory.zip").read_bytes()
    z = read_zip(data[:-20])
    assert "stop_times.txt" not in z.files
    assert any("cut off" in p or "corrupt" in p for p in z.problems)
    assert "agency.txt" in z.files


def test_non_zip_bytes():
    z = read_zip(b"<html>502 Bad Gateway</html>")
    assert z.files == {}
    assert not z.is_valid_gtfs
    assert z.problems
