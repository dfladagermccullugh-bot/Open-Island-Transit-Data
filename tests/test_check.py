import json

from pipeline.check import check


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def test_check_finds_missing_and_wrong_size_files(tmp_path):
    day = "2026-11-19"
    f = [{"name": f"raw-{day}.parquet", "bytes": 100, "sha256": "x"}]
    write(
        tmp_path / "manifest/raw.json", {"days": {day: {"release_tag": "raw-2026-11", "files": f}}}
    )
    p = [{"name": f"vehicles-{day}.parquet", "bytes": 50, "sha256": "y"}]
    write(tmp_path / "manifest/parsed.json", {"days": {day: {"release_tag": "parsed-2026-11",
                                                             "files": p}}})  # fmt: skip
    write(tmp_path / "manifest/events.json", {"days": {}})
    write(tmp_path / "manifest/static.json",
          {"days": {day: {"crosswalk": "cw1", "timepoints": "tp1"}}})  # fmt: skip
    (tmp_path / "crosswalk/cw1").mkdir(parents=True)
    releases = {"raw-2026-11": {f"raw-{day}.parquet": 100}, "parsed-2026-11": {}}
    problems, counts = check(tmp_path, lambda tag: releases[tag])
    assert counts == {"days": 1, "files": 2}
    assert any("vehicles-2026-11-19.parquet in parsed-2026-11 is None bytes" in x for x in problems)
    assert any("timepoints folder tp1 missing" in x for x in problems)
    assert any("quality 2026-11-19: day file missing" in x for x in problems)
    assert len(problems) == 3

    releases["parsed-2026-11"][f"vehicles-{day}.parquet"] = 50
    (tmp_path / "timepoints/tp1").mkdir(parents=True)
    write(tmp_path / f"quality/days/{day}.json", {})
    assert check(tmp_path, lambda tag: releases[tag])[0] == []
