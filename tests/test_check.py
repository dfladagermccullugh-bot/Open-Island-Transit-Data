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
    assert any("route map maps/tp1.json missing" in x for x in problems)
    assert len(problems) == 4

    releases["parsed-2026-11"][f"vehicles-{day}.parquet"] = 50
    (tmp_path / "timepoints/tp1").mkdir(parents=True)
    write(tmp_path / "maps/tp1.json", {})
    write(tmp_path / f"quality/days/{day}.json", {})
    assert check(tmp_path, lambda tag: releases[tag])[0] == []


def test_the_file_index_must_match_the_manifests(tmp_path):
    from pipeline import datapackage

    files = [{"name": "raw-2026-11-24.parquet", "bytes": 10, "sha256": "a" * 64}]
    manifest = {"days": {"2026-11-24": {"release_tag": "raw-2026-11", "files": files}}}
    (tmp_path / "manifest").mkdir()
    (tmp_path / "manifest" / "raw.json").write_text(json.dumps(manifest))
    (tmp_path / "quality" / "days").mkdir(parents=True)
    assets = lambda tag: {"raw-2026-11-24.parquet": 10}  # noqa: E731

    assert check(tmp_path, assets)[0] == []  # no index yet: nothing to compare
    datapackage.write(tmp_path)
    assert check(tmp_path, assets)[0] == []

    index = tmp_path / "files.csv"
    good = index.read_text()
    index.write_text(good.replace("a" * 64, "b" * 64))
    assert check(tmp_path, assets)[0] == [
        "files.csv: raw-2026-11-24.parquet does not match its manifest entry"
    ]
    index.write_text(good.splitlines()[0] + "\n")
    assert check(tmp_path, assets)[0] == ["files.csv: raw-2026-11-24.parquet is missing"]
    index.write_text(good + good.splitlines()[1].replace("raw-2026-11-24", "raw-2026-11-25") + "\n")
    assert check(tmp_path, assets)[0] == ["files.csv: raw-2026-11-25.parquet is in no manifest"]
