"""The data dictionary says what the code writes and the codebook explains.

``datapackage.json`` takes each table's columns from the definitions the
pipeline writes its files with, and each column's explanation from
``docs/codebook.md``. These tests hold the three together: a column added in
code must be explained in the codebook, an explanation of a column that no
longer exists must go, and the copy of the dictionary kept in the repository
must be current.
"""

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from pipeline import datapackage

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "data-branch"
FIXTURE = ROOT / "tests" / "site" / "fixture" / "data"
BOOK = datapackage.codebook_columns(datapackage.CODEBOOK.read_text(encoding="utf-8"))
BOOK["files.csv"] = datapackage._files_section()
EVERY = [*datapackage.tables(), *datapackage.versioned_tables()]


@pytest.mark.parametrize("table", EVERY, ids=lambda t: t.name)
def test_every_column_is_explained_in_the_codebook(table):
    unexplained = [name for name, text in datapackage.describe(table, BOOK).items() if not text]
    assert unexplained == [], (
        f"{table.path}: add these columns to the table under "
        f"'{table.sections[0]}' in docs/codebook.md"
    )


@pytest.mark.parametrize("table", EVERY, ids=lambda t: t.name)
def test_the_codebook_explains_no_column_that_is_gone(table):
    written = {name for name, _ in table.fields}
    stale = sorted(set(BOOK.get(table.sections[0], {})) - written)
    # The raw table's section also serves the fetch log, which lacks `body`.
    if table.name == "fetch-log":
        stale = [c for c in stale if c != "body"]
    assert stale == [], f"docs/codebook.md, '{table.sections[0]}': no such column in {table.path}"


def test_the_copy_in_the_repository_is_current():
    kept = (ROOT / "datapackage.json").read_text(encoding="utf-8")
    assert kept == datapackage.dumps(datapackage.build()), (
        "datapackage.json is out of date: run python -m pipeline.datapackage --write "
        "datapackage.json"
    )


def test_it_is_a_data_package_other_tools_can_read():
    package = datapackage.build()
    assert package["profile"] == "data-package" and package["name"] == "open-island-transit-data"
    names = [r["name"] for r in package["resources"]]
    assert names == ["quality-daily", "ontime-daily", "ontime-routes", "files"]
    for resource in package["resources"]:
        assert resource["path"] and "<" not in resource["path"] and "YYYY" not in resource["path"]
        assert resource["schema"]["fields"] and resource["schema"]["missingValues"] == [""]
    for table in package["dailyTables"]:
        assert "path" not in table and "YYYY-MM-DD" in table["pathPattern"]
        assert table["urlPattern"].startswith("https://github.com/")
    every = [*package["resources"], *package["dailyTables"], *package["versionedTables"]]
    assert len({t["name"] for t in every}) == len(every)
    allowed = {"string", "integer", "number", "boolean", "date", "datetime", "array", "object"}
    assert {f["type"] for t in every for f in t["schema"]["fields"]} <= allowed


def test_units_come_from_the_names_and_timestamps_are_utc():
    fields = {
        (t["name"], f["name"]): f
        for t in (lambda p: [*p["resources"], *p["dailyTables"]])(datapackage.build())
        for f in t["schema"]["fields"]
    }
    assert fields["quality-daily", "trip_coverage"]["unit"] == "fraction from 0 to 1"
    assert fields["quality-daily", "position_age_s_median"]["unit"] == "seconds"
    assert fields["quality-daily", "jitter_m_p95"]["unit"] == "meters"
    assert fields["quality-daily", "vehicle_latency_ms_median"]["unit"] == "milliseconds"
    assert fields["vehicles", "speed"]["unit"] == "meters per second"
    assert fields["stop-events", "stop_distance_m"]["unit"] == "meters"
    assert fields["stop-events", "arrival_time"]["timezone"] == "UTC"
    assert "unit" not in fields["quality-daily", "scheduled_trips"]
    assert "unit" not in fields["vehicles", "route_id"]


def _parses(value: str, kind: str) -> bool:
    if value == "":
        return True
    try:
        if kind == "integer":
            int(value)
        elif kind == "number":
            float(value)
        elif kind == "date":
            date.fromisoformat(value)
        elif kind == "boolean":
            return value in ("true", "false", "True", "False")
    except ValueError:
        return False
    return True


CSV_FILES = [
    ("quality-daily", FIXTURE / "quality" / "daily.csv"),
    ("ontime-daily", FIXTURE / "ontime" / "daily.csv"),
    ("quality-daily", GOLDEN / "quality" / "daily.csv"),
    ("ontime-daily", GOLDEN / "ontime" / "daily.csv"),
    ("ontime-routes", GOLDEN / "ontime" / "routes.csv"),
    ("timepoints", next(GOLDEN.glob("timepoints/*/timepoints.csv"))),
    *((f"crosswalk-{n.replace('_', '-')}", next(GOLDEN.glob(f"crosswalk/*/{n}.csv")))
      for n in datapackage.CROSSWALK_COLUMNS),
]  # fmt: skip


@pytest.mark.parametrize(
    ("name", "path"), CSV_FILES, ids=[f"{n} in {p.parts[-4]}" for n, p in CSV_FILES]
)
def test_real_files_have_the_columns_and_types_the_dictionary_gives(name, path):
    table = next(t for t in EVERY if t.name == name)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        assert reader.fieldnames == [c for c, _ in table.fields]
    assert rows, path
    for column, kind in table.fields:
        bad = [r[column] for r in rows if not _parses(r[column], kind)]
        assert bad == [], f"{path.name}: {column} is not always {kind}"


def test_the_file_index_lists_every_release_file_once():
    rows = datapackage.files_index(GOLDEN, "example/synthetic")
    names = [r[3] for r in rows]
    assert len(names) == len(set(names))
    released = json.loads((ROOT / "tests" / "golden" / "releases.json").read_text())
    expected = {n.split("/")[1] for n in released if "SHA256SUMS" not in n}
    assert set(names) == expected
    row = next(r for r in rows if r[3] == "vehicles-2026-11-24.parquet")
    assert row[:3] == ["2026-11-24", "vehicles", "parsed-2026-11"]
    assert row[6] == (
        "https://github.com/example/synthetic/releases/download/parsed-2026-11/"
        "vehicles-2026-11-24.parquet"
    )
    assert rows == sorted(rows, key=lambda r: (r[0], r[2], r[3]))


@pytest.mark.parametrize(
    ("name", "table"),
    [
        ("vehicles-2026-11-20.parquet", "vehicles"),
        ("trip-update-stops-2026-11-20.parquet", "trip-update-stops"),
        ("raw-2026-11-20.parquet", "raw"),
        ("fetch-log-2026-11-20.csv", "fetch-log"),
        ("static-2026-11-20-passio-3.zip", "static-passio"),
        ("static-2026-11-19-trillium-2.zip", "static-trillium"),
    ],
)
def test_the_kind_of_table_is_read_from_the_file_name(name, table):
    assert datapackage.table_of(name) == table
