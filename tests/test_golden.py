"""The whole nightly chain on a made-up archive, compared with a kept copy.

``tests/helpers/synthetic.py`` builds three service days. This test runs every
stage after the archive over them, exactly as ``scripts/rehearse_nightly.py
--replay`` does, with the clock fixed, and compares everything the stages
would publish with ``tests/golden/``.

A change that is meant to leave the published files alone (a speed-up, a
tidier function) must pass this test untouched. A change that is meant to
alter them is made visible by it: run

    pytest tests/test_golden.py --update-golden

and the difference in ``tests/golden/`` is the review of what the change does
to the output.

Two things are evened out before comparing, because they depend on the
library versions and not on our code: SHA-256 values and byte counts in the
text files, and the bytes of Parquet files (their rows and column types are
compared instead).
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from tests.helpers.synthetic import build_world

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "golden"
NOW = "2026-12-08T09:00:00+00:00"
TEXT = {".json", ".csv", ".md", ".txt"}


def even_out(text: str) -> str:
    text = re.sub(r"\b[0-9a-f]{64}\b", "<sha256>", text)
    text = re.sub(r'"bytes": \d+', '"bytes": 0', text)
    return re.sub(r"\b\d+(\.\d+)? (MB|KB|bytes)\b", r"0 \2", text)


def parquet_summary(path: Path) -> dict:
    table = pq.read_table(path)
    rows = json.dumps(table.to_pylist(), default=str, sort_keys=True)
    return {
        "rows": table.num_rows,
        "columns": {f.name: str(f.type) for f in table.schema},
        "content": hashlib.sha256(rows.encode()).hexdigest(),
    }


def published(root: Path) -> dict[str, str]:
    """Everything the stages would publish, as text keyed by relative path."""
    out: dict[str, str] = {}
    for path in sorted((root / "data-branch").rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = even_out(path.read_text(encoding="utf-8"))
    releases: dict[str, object] = {}
    for path in sorted((root / "releases").rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(root / "releases").as_posix()
        if path.suffix == ".parquet":
            releases[name] = parquet_summary(path)
        elif path.suffix in TEXT and not path.name.startswith("fetch-log-"):
            out[f"releases/{name}"] = even_out(path.read_text(encoding="utf-8"))
        else:
            releases[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    out["releases.json"] = json.dumps(releases, indent=1) + "\n"
    return out


@pytest.fixture
def replay(tmp_path, monkeypatch):
    """Run the chain; put back everything the rehearsal script replaces."""
    import pipeline.archive.build as archive_build
    import pipeline.release as rel

    for name in ("ensure_release", "upload", "fetch", "assets", "set_notes"):
        monkeypatch.setattr(rel, name, getattr(rel, name))
    monkeypatch.setattr(archive_build, "build_day", archive_build.build_day)
    monkeypatch.setattr(sys, "argv", sys.argv)
    monkeypatch.setenv("OITD_NOW", NOW)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    import rehearse_nightly

    def run(root: Path) -> int:
        world = build_world(root)
        reference = world["reference"]
        return rehearse_nightly.main(
            ["--replay", str(root), "--max-days", "100", "--now", NOW,
             "--reference-dir", str(reference),
             "--observations", str(reference / "field-observations.csv")]
        )  # fmt: skip

    return run


def test_nightly_chain_matches_golden(tmp_path, replay, update_golden, capsys):
    assert replay(tmp_path / "world") == 0
    got = published(tmp_path / "world")
    if update_golden:
        for old in sorted(GOLDEN.rglob("*"), reverse=True):
            old.unlink() if old.is_file() else old.rmdir()
        for name, text in got.items():
            (GOLDEN / name).parent.mkdir(parents=True, exist_ok=True)
            (GOLDEN / name).write_text(text, encoding="utf-8")
        return
    want = {
        p.relative_to(GOLDEN).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(GOLDEN.rglob("*"))
        if p.is_file()
    }
    assert sorted(got) == sorted(want), "the set of published files changed"
    changed = [name for name in want if got[name] != want[name]]
    assert not changed, (
        f"published output changed in {changed}; if that is intended, run "
        "pytest tests/test_golden.py --update-golden and review the diff"
    )


def test_the_made_up_days_hold_what_their_story_says(tmp_path, replay):
    """Guards the fixture itself: a golden file full of empty days would
    compare equal forever and prove nothing."""
    assert replay(tmp_path / "world") == 0
    data = tmp_path / "world" / "data-branch"
    first = json.loads((data / "quality" / "days" / "2026-11-24.json").read_text())
    second = json.loads((data / "quality" / "days" / "2026-11-25.json").read_text())
    holiday = json.loads((data / "ontime" / "days" / "2026-11-26.json").read_text())
    assert first["coverage"]["trip_coverage"] == 0.75  # T4 never reported
    assert second["coverage"]["trip_coverage"] == 1.0
    assert holiday["service_expected"] is False
    ontime = json.loads((data / "ontime" / "days" / "2026-11-24.json").read_text())
    assert ontime["early"] > 0 and ontime["late"] > 0 and ontime["on_time"] > 0
    assert (data / "reports" / "2026-11.json").exists()
    assert os.environ["OITD_NOW"] == NOW
