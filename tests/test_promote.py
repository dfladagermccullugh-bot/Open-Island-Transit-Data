"""The staging data branch and its promotion (scripts/get_data_branch.sh,
publish_data.sh, promote_data.sh), against a bare repository in a temporary
folder: ``data`` moves only to a commit the gate passed, and only forward."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A bare repository whose ``data`` branch has one commit."""
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    seed = tmp_path / "seed"
    git("init", "-q", "-b", "data", str(seed), cwd=tmp_path)
    (seed / "figure.txt").write_text("1\n")
    git("add", "-A", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed", cwd=seed)
    git("push", "-q", str(bare), "data", cwd=seed)
    return bare


def run(
    script: str, *args: str, cwd: Path, remote: Path, env_file: Path
) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATA_REMOTE": f"file://{remote}", "GITHUB_ENV": str(env_file)}
    return subprocess.run(
        ["bash", str(SCRIPTS / script), *args], cwd=cwd, env=env, capture_output=True, text=True
    )


def tip(remote: Path, branch: str) -> str:
    return git("rev-parse", f"refs/heads/{branch}", cwd=remote)


def new_run(tmp_path: Path, remote: Path, name: str) -> tuple[Path, Path]:
    work = tmp_path / name
    work.mkdir()
    env_file = work / "env"
    done = run("get_data_branch.sh", cwd=work, remote=remote, env_file=env_file)
    assert done.returncode == 0, done.stderr
    return work, env_file


def gate(work: Path, *extra: str) -> int:
    """The real gate (light set) over the checkout, writing checks/gate.json."""
    data = work / "data-branch"
    head = git("rev-parse", "HEAD", cwd=data)
    args = ["--light", "--data", str(data), "--report", str(data / "checks" / "gate.json"),
            "--commit", head]  # fmt: skip
    for e in extra:
        args += ["--extra-check", e]
    return subprocess.run(
        [sys.executable, "-m", "pipeline.invariants", *args], cwd=ROOT, capture_output=True
    ).returncode


def stage(work: Path, remote: Path, env_file: Path, value: str) -> None:
    (work / "data-branch" / "figure.txt").write_text(value)
    done = run("publish_data.sh", "data-staging", "Stage:", cwd=work, remote=remote,
               env_file=env_file)  # fmt: skip
    assert done.returncode == 0, done.stderr


def test_bootstrap_publish_and_promote(tmp_path: Path, remote: Path) -> None:
    seed = tip(remote, "data")
    work, env_file = new_run(tmp_path, remote, "run1")
    assert tip(remote, "data-staging") == seed  # created from data
    assert f"DATA_BASE={seed}" in env_file.read_text()

    stage(work, remote, env_file, "2\n")
    assert tip(remote, "data") == seed  # writers never touch data
    assert gate(work) == 0
    run("publish_data.sh", "data-staging", "Gate:", cwd=work, remote=remote, env_file=env_file)
    done = run("promote_data.sh", cwd=work, remote=remote, env_file=env_file)
    assert done.returncode == 0, done.stderr
    assert tip(remote, "data") == tip(remote, "data-staging") != seed
    assert "PROMOTED=1" in env_file.read_text()
    report = json.loads((work / "data-branch" / "checks" / "gate.json").read_text())
    assert report["passed"] and report["rules_count"] == 8

    # A second run starts from staging and finds both branches level.
    _, env2 = new_run(tmp_path, remote, "run2")
    text = env2.read_text()
    assert text.count(tip(remote, "data")) == 2


def test_failing_gate_leaves_data_unchanged(tmp_path: Path, remote: Path) -> None:
    seed = tip(remote, "data")
    work, env_file = new_run(tmp_path, remote, "run1")
    stage(work, remote, env_file, "bad\n")
    assert gate(work, "frictionless=1") == 1
    run("publish_data.sh", "data-staging", "Gate:", cwd=work, remote=remote, env_file=env_file)
    done = run("promote_data.sh", cwd=work, remote=remote, env_file=env_file)
    assert done.returncode == 1
    assert "did not pass" in done.stderr
    assert tip(remote, "data") == seed
    assert tip(remote, "data-staging") != seed  # the failed work is kept for a look


def test_promotion_refuses_a_change_after_the_check(tmp_path: Path, remote: Path) -> None:
    seed = tip(remote, "data")
    work, env_file = new_run(tmp_path, remote, "run1")
    stage(work, remote, env_file, "2\n")
    assert gate(work) == 0
    stage(work, remote, env_file, "3\n")  # changed after the gate ran
    done = run("promote_data.sh", cwd=work, remote=remote, env_file=env_file)
    assert done.returncode == 1 and "differs from the checked commit" in done.stderr
    assert tip(remote, "data") == seed


def test_promotion_refuses_a_non_fast_forward(tmp_path: Path, remote: Path) -> None:
    work, env_file = new_run(tmp_path, remote, "run1")
    stage(work, remote, env_file, "2\n")
    assert gate(work) == 0
    run("publish_data.sh", "data-staging", "Gate:", cwd=work, remote=remote, env_file=env_file)

    # Someone commits to data directly, so staging no longer contains it.
    other = tmp_path / "other"
    git("clone", "-q", "--branch", "data", str(remote), str(other), cwd=tmp_path)
    (other / "figure.txt").write_text("hand edit\n")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "hand", cwd=other)
    git("push", "-q", "origin", "data", cwd=other)
    hand = tip(remote, "data")

    done = run("promote_data.sh", cwd=work, remote=remote, env_file=env_file)
    assert done.returncode == 1 and "not an ancestor" in done.stderr
    assert tip(remote, "data") == hand
