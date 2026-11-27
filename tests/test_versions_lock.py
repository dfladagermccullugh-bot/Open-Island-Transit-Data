"""The version lock matches the code, and catches a change in the code behind
a stage's numbers that came without a new version (``scripts/check_versions.py``)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_versions as cv  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    """A copy of the pipeline and its lock to edit."""
    shutil.copytree(ROOT / "pipeline", tmp_path / "pipeline",
                    ignore=shutil.ignore_patterns("__pycache__"))  # fmt: skip
    return tmp_path


def lock_of(root: Path) -> dict:
    return json.loads((root / "pipeline" / "versions.lock.json").read_text())


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def test_the_lock_is_current():
    assert cv.check(lock_of(ROOT)) == []


def test_every_version_constant_and_source_exists():
    for stage in lock_of(ROOT)["stages"].values():
        cv.version_of(stage["constant"])
        for source in stage["sources"]:
            cv.source_hash(source)


def test_a_change_in_shared_code_without_a_bump_fails(tree):
    edit(tree / "pipeline" / "stats.py", "pos = (len(v) - 1) * q", "pos = (len(v) - 1) * q * 1")
    problems = cv.check(lock_of(tree), tree)
    stages = {p.split(":")[0] for p in problems}
    assert {"events", "ontime", "quality", "routes", "monthly"} <= stages
    assert "tables" not in stages


def test_comments_docstrings_and_the_version_line_do_not_count(tree):
    path = tree / "pipeline" / "events" / "ontime.py"
    edit(path, '"""Daily on-time summary', '"""A reworded daily on-time summary')
    edit(path, "LATE_S = 300", "LATE_S = 300  # five minutes")
    assert cv.check(lock_of(tree), tree) == []


def test_a_bump_must_be_recorded(tree):
    path = tree / "pipeline" / "events" / "ontime.py"
    edit(path, 'ONTIME_VERSION = "ontime/', 'ONTIME_VERSION = "ontime/9.')
    problems = cv.check(lock_of(tree), tree)
    assert len(problems) == 1 and "--update" in problems[0]
    lock = cv.update(lock_of(tree), tree)
    assert cv.check(lock, tree) == []


def test_a_change_with_a_bump_passes_once_recorded(tree):
    path = tree / "pipeline" / "events" / "ontime.py"
    edit(path, "LATE_S = 300", "LATE_S = 301")
    assert cv.check(lock_of(tree), tree)
    edit(path, 'ONTIME_VERSION = "ontime/', 'ONTIME_VERSION = "ontime/9.')
    assert cv.check(cv.update(lock_of(tree), tree), tree) == []


def test_the_script_runs_with_the_standard_library_only():
    done = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "check_versions.py")],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
