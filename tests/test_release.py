import subprocess

import pytest

from pipeline import release


def test_gh_retries_then_reports_stderr(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        ok = len(calls) == 3
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, "out" if ok else "", "not found")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    monkeypatch.setattr(release.time, "sleep", lambda s: None)
    assert release.gh("release", "view", "x") == "out"
    assert len(calls) == 3

    calls.clear()
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "HTTP 403: denied"),
    )
    with pytest.raises(RuntimeError, match="HTTP 403: denied"):
        release.gh("release", "upload", "raw-2026-11")
