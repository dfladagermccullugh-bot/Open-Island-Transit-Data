import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "replay_diff", Path(__file__).resolve().parents[1] / "scripts" / "replay_diff.py"
)
replay_diff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay_diff)


def folder(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


BASE = {"data-branch/quality/daily.csv": "a\n", "releases/raw-1/x.parquet": "p\n"}


def test_identical_folders_pass(tmp_path):
    a, b = folder(tmp_path / "a", BASE), folder(tmp_path / "b", BASE)
    assert replay_diff.main([str(a), str(b)]) == 0


def test_an_unexpected_change_fails(tmp_path):
    a = folder(tmp_path / "a", BASE)
    b = folder(tmp_path / "b", {**BASE, "data-branch/quality/daily.csv": "b\n"})
    assert replay_diff.main([str(a), str(b)]) == 1


def test_an_expected_change_passes_and_work_files_are_ignored(tmp_path):
    a = folder(tmp_path / "a", {**BASE, "work/tables/t.parquet": "1"})
    b = folder(tmp_path / "b", {**BASE, "data-branch/quality/daily.csv": "b\n"})
    assert replay_diff.main([str(a), str(b), "--expect-changed", "data-branch/quality/*"]) == 0


def test_an_expected_change_that_did_not_happen_fails(tmp_path):
    a, b = folder(tmp_path / "a", BASE), folder(tmp_path / "b", BASE)
    assert replay_diff.main([str(a), str(b), "--expect-changed", "data-branch/ontime/*"]) == 1


def test_a_missing_or_new_file_is_a_difference(tmp_path):
    a = folder(tmp_path / "a", BASE)
    b = folder(tmp_path / "b", {"data-branch/quality/daily.csv": "a\n", "releases/new.txt": "n"})
    differing = replay_diff.compare(replay_diff.tree(a), replay_diff.tree(b))
    assert differing == {
        "releases/new.txt": "only in changed",
        "releases/raw-1/x.parquet": "only in baseline",
    }
