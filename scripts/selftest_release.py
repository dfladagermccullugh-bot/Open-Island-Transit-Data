"""Exercise the Release upload path with the Actions token, then clean up.

Creates a throwaway release, uploads two small files under content-addressed
names (twice: a file already there is not sent again) and one of them under
its plain name, confirms their sizes, downloads one and checks its hash,
checks that prune deletes only the plain-named copy no manifest names,
replaces the release notes and reads them back, then deletes the release and
its tag.
Run by the manual `Release self-test` workflow; publishes nothing that stays.

    python scripts/selftest_release.py <owner/repo> <work dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from pipeline import release

TAG = "selftest-release"


def main(repo: str, work: Path) -> None:
    out = work / "up"
    out.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    for name, body in (("a-2000-01-01.parquet", b"a" * 1000), ("b-2000-01-01.zip", b"b" * 37)):
        (out / name).write_bytes(body)
        files.append({"name": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
    try:
        release.ensure_release(TAG, repo, "Self-test (deleted after the run)", "Temporary.")
        release.ensure_release(TAG, repo, "unused", "unused")  # already exists: no-op
        release.upload(TAG, repo, out, files)
        release.upload(TAG, repo, out, files)  # already there: not sent again
        release.upload(TAG, repo, out, [dict(files[1])], content_names=False)  # plain, --clobber
        names = release.assets(TAG, repo)
        print("assets:", names)
        want = {f["asset"] for f in files} | {files[1]["name"]}
        if set(names) != want:
            raise RuntimeError(f"expected {sorted(want)}, found {sorted(names)}")
        got = release.fetch(TAG, repo, files[0]["name"], work / "down", files[0]["sha256"])
        print("downloaded and verified:", got)
        # A manifest that names only the content-addressed files: prune
        # deletes the plain-named copy and nothing else.
        data = work / "data"
        (data / "manifest").mkdir(parents=True, exist_ok=True)
        (data / "manifest" / "parsed.json").write_text(json.dumps(
            {"days": {"2000-01-01": {"release_tag": TAG, "files": files}}}))  # fmt: skip
        gone = release.prune_superseded(data, [], repo)
        left = set(release.assets(TAG, repo))
        if gone != {TAG: [files[1]["name"]]} or left != {f["asset"] for f in files}:
            raise RuntimeError(f"prune removed {gone}, left {sorted(left)}")
        print("prune removed only the superseded file")
        notes = "# Self-test notes\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
        release.set_notes(TAG, repo, notes)
        shown = release.gh("release", "view", TAG, "--repo", repo, "--json", "body", "-q", ".body")
        if shown.strip() != notes.strip():
            raise RuntimeError(f"release notes not replaced: {shown!r}")
        print("release notes replaced")
    finally:
        release.gh("release", "delete", TAG, "--repo", repo, "--yes", "--cleanup-tag")
    print("release self-test OK")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
