import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "site_data", Path(__file__).resolve().parents[1] / "scripts" / "site_data.py"
)
site_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_data)


def branch(root, days=("2026-11-25", "2026-11-26"), holiday="2026-11-26", key="p__t"):
    def write(rel, text):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    rows = "".join(f"{d},{d != holiday}\n" for d in days)
    for folder in ("ontime", "quality"):
        write(f"{folder}/daily.csv", "service_date,service_expected\n" + rows)
        for d in days:
            write(f"{folder}/days/{d}.json", "{}")
    write("ontime/recent.json", json.dumps({"timepoints": key, "last_service_date": days[0]}))
    write("manifest/raw.json", "{}")
    write(f"maps/{key}.json", "{}")
    write("maps/older.json", "{}")
    return root


def test_only_what_the_pages_read_is_copied(tmp_path):
    files, missing = site_data.wanted(branch(tmp_path))
    assert missing == []
    # The latest day with service, not the holiday after it; one map, not every map.
    assert "ontime/days/2026-11-25.json" in files and "quality/days/2026-11-25.json" in files
    assert not any("2026-11-26" in f for f in files)
    assert [f for f in files if f.startswith("maps/")] == ["maps/p__t.json"]


def test_a_missing_required_file_or_map_is_reported(tmp_path):
    root = branch(tmp_path)
    (root / "ontime" / "recent.json").unlink()
    assert "ontime/recent.json" in site_data.wanted(root)[1]
    root = branch(tmp_path / "b")
    (root / "maps" / "p__t.json").unlink()
    assert site_data.wanted(root)[1] == ["maps/p__t.json"]
