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


class FakeGh:
    """A release store behind ``release.gh``: upload, view, download, delete."""

    def __init__(self, tmp_path):
        self.store: dict[str, dict[str, bytes]] = {}
        self.uploads: list[str] = []

    def __call__(self, *args):
        import json
        from pathlib import Path

        cmd, tag = args[1], args[2]
        files = self.store.setdefault(tag, {})
        if cmd == "upload":
            for p in args[3 : args.index("--repo")]:
                files[Path(p).name] = Path(p).read_bytes()
                self.uploads.append(Path(p).name)
            return ""
        if cmd == "view":
            return json.dumps({"assets": [{"name": n, "size": len(b)} for n, b in files.items()]})
        if cmd == "download":
            name = args[args.index("--pattern") + 1]
            dest = Path(args[args.index("--dir") + 1])
            (dest / name).write_bytes(files[name])
            return ""
        if cmd == "delete-asset":
            del files[args[3]]
            return ""
        raise AssertionError(args)


def _entry(path):
    from pipeline.common import file_entry

    return dict(file_entry(path))


def test_upload_names_files_by_content(tmp_path, monkeypatch):
    from pipeline.common import asset_name

    fake = FakeGh(tmp_path)
    monkeypatch.setattr(release, "gh", fake)
    release._listing.clear()
    out = tmp_path / "out"
    out.mkdir()
    (out / "stop-events-2026-11-20.parquet").write_bytes(b"first")
    f = _entry(out / "stop-events-2026-11-20.parquet")
    release.upload("parsed-2026-11", "r", out, [f])
    assert f["asset"] == asset_name(f["name"], f["sha256"])
    assert f["asset"] == f"stop-events-2026-11-20-{f['sha256'][:8]}.parquet"
    assert list(fake.store["parsed-2026-11"]) == [f["asset"]]

    # The same file again is not sent; a rebuild adds a file beside it.
    release.upload("parsed-2026-11", "r", out, [dict(f)])
    assert fake.uploads == [f["asset"]]
    (out / "stop-events-2026-11-20.parquet").write_bytes(b"second")
    g = _entry(out / "stop-events-2026-11-20.parquet")
    release.upload("parsed-2026-11", "r", out, [g])
    assert set(fake.store["parsed-2026-11"]) == {f["asset"], g["asset"]}

    # fetch finds the content-addressed file and saves it under its plain name.
    release._listing.clear()
    got = release.fetch("parsed-2026-11", "r", f["name"], tmp_path / "down", f["sha256"])
    assert got.name == f["name"] and got.read_bytes() == b"first"


def test_fetch_reads_an_old_plain_name(tmp_path, monkeypatch):
    import hashlib

    fake = FakeGh(tmp_path)
    fake.store["raw-2026-11"] = {"raw-2026-11-19.parquet": b"old"}
    monkeypatch.setattr(release, "gh", fake)
    release._listing.clear()
    sha = hashlib.sha256(b"old").hexdigest()
    got = release.fetch("raw-2026-11", "r", "raw-2026-11-19.parquet", tmp_path / "d", sha)
    assert got.read_bytes() == b"old"


def test_monthly_checksums_keep_their_plain_name(tmp_path, monkeypatch):
    fake = FakeGh(tmp_path)
    monkeypatch.setattr(release, "gh", fake)
    release._listing.clear()
    (tmp_path / "SHA256SUMS-raw-2026-11.txt").write_text("x\n")
    release.upload(
        "raw-2026-11",
        "r",
        tmp_path,
        [{"name": "SHA256SUMS-raw-2026-11.txt", "bytes": 2}],
        content_names=False,
    )
    assert list(fake.store["raw-2026-11"]) == ["SHA256SUMS-raw-2026-11.txt"]


def _data(tmp_path, name, files):
    import json

    d = tmp_path / name
    (d / "manifest").mkdir(parents=True)
    (d / "manifest" / "events.json").write_text(json.dumps(
        {"days": {"2026-11-20": {"release_tag": "parsed-2026-11", "files": files}}}))  # fmt: skip
    return d


def test_prune_keeps_what_either_data_commit_names(tmp_path, monkeypatch):
    fake = FakeGh(tmp_path)
    plain = "stop-events-2026-11-20.parquet"
    fake.store["parsed-2026-11"] = {
        plain: b"v0",  # an old plain-named file
        "stop-events-2026-11-20-aaaaaaaa.parquet": b"v1",  # named by the previous data commit
        "stop-events-2026-11-20-bbbbbbbb.parquet": b"v2",  # named by the new one
        "stop-events-2026-11-20-cccccccc.parquet": b"v3",  # a failed run's upload, never named
        "SHA256SUMS-parsed-2026-11.txt": b"sums",  # nothing names it here: not ours to judge
        "notes.md": b"other",
    }
    fake.store["raw-2026-11"] = {
        "raw-2026-11-20.parquet": b"r",
        "raw-2026-11-20-dddddddd.parquet": b"r",
    }
    monkeypatch.setattr(release, "gh", fake)
    release._listing.clear()
    entry = {"name": plain, "bytes": 2, "sha256": "b" * 64}
    new = _data(tmp_path, "new", [{**entry, "asset": "stop-events-2026-11-20-bbbbbbbb.parquet"}])
    old = _data(tmp_path, "old", [{**entry, "asset": "stop-events-2026-11-20-aaaaaaaa.parquet"}])
    (new / "manifest" / "raw.json").write_text(
        '{"days": {"2026-11-20": {"release_tag": "raw-2026-11", "files": '
        '[{"name": "raw-2026-11-20.parquet", "bytes": 1, "sha256": "d"}]}}}'
    )

    gone = release.prune_superseded(new, [old], "r", dry_run=True)
    assert gone == {"parsed-2026-11": ["stop-events-2026-11-20-cccccccc.parquet", plain]}
    release.prune_superseded(new, [old], "r")
    assert set(fake.store["parsed-2026-11"]) == {
        "stop-events-2026-11-20-aaaaaaaa.parquet", "stop-events-2026-11-20-bbbbbbbb.parquet",
        "SHA256SUMS-parsed-2026-11.txt", "notes.md",
    }  # fmt: skip
    assert len(fake.store["raw-2026-11"]) == 2  # raw releases are never pruned


def test_plain_name():
    assert release.plain_name("trip-tracks-2026-11-20-0a1b2c3d.parquet") == (
        "trip-tracks-2026-11-20.parquet"
    )
    assert release.plain_name("trip-tracks-2026-11-20.parquet") == "trip-tracks-2026-11-20.parquet"
