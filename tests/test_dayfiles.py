import json

from pipeline.common import DayFiles
from pipeline.gtfs.zipread import load_zip, read_zip
from tests.helpers.synthetic import passio_zip


def test_each_day_file_is_parsed_once_and_a_write_is_seen_at_once(tmp_path, monkeypatch):
    folder = tmp_path / "days"
    files = DayFiles(folder)
    assert files.days() == [] and files.read("2026-11-24") is None

    files.write("2026-11-25", json.dumps({"n": 2}) + "\n")
    (folder / "2026-11-24.json").write_text(json.dumps({"n": 1}))
    assert files.days() == ["2026-11-24", "2026-11-25"]
    assert [m["n"] for m in files.all()] == [1, 2]

    # A second pass reads nothing from disk.
    calls = []
    real = json.loads
    monkeypatch.setattr(json, "loads", lambda text: calls.append(1) or real(text))
    assert [m["n"] for m in files.all()] == [1, 2]
    assert calls == []

    # What is written through the object replaces what it remembered.
    files.write("2026-11-24", json.dumps({"n": 9}))
    assert files.read("2026-11-24") == {"n": 9}
    assert json.loads((folder / "2026-11-24.json").read_text()) == {"n": 9}


def test_what_is_remembered_is_what_would_be_read_back(tmp_path):
    """A stage keeps the text it wrote, parsed: dates become text and tuples
    become lists, exactly as for a later run that reads the file."""
    files = DayFiles(tmp_path)
    files.write("2026-11-24", json.dumps({"pair": (1, 2)}))
    assert files.read("2026-11-24") == {"pair": [1, 2]}


def test_a_schedule_zip_is_read_once_and_keyed_by_its_hash(tmp_path):
    path = tmp_path / "schedule.zip"
    path.write_bytes(passio_zip())
    first = load_zip(path, "hash-a")
    assert load_zip(path, "hash-a") is first
    assert first.files == read_zip(path.read_bytes()).files
    # Another file at the same path, with another recorded hash, is not
    # mistaken for the first.
    path.write_bytes(b"not a zip")
    other = load_zip(path, "hash-b")
    assert other is not first and other.problems
