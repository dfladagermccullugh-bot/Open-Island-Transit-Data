"""Exercise the Release upload path with the Actions token, then clean up.

Creates a throwaway release, uploads two small files (twice, to test
--clobber), confirms their sizes, downloads one and checks its hash, then
deletes the release and its tag. Run by the manual `Release self-test`
workflow; publishes nothing that stays.

    python scripts/selftest_release.py <owner/repo> <work dir>
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from pipeline import release

TAG = "selftest-release"


def main(repo: str, work: Path) -> None:
    out = work / "up"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for name, body in (("a-2000-01-01.parquet", b"a" * 1000), ("b-2000-01-01.zip", b"b" * 37)):
        (out / name).write_bytes(body)
        files.append({"name": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
    try:
        release.ensure_release(TAG, repo, "Self-test (deleted after the run)", "Temporary.")
        release.ensure_release(TAG, repo, "unused", "unused")  # already exists: no-op
        release.upload(TAG, repo, out, files)
        release.upload(TAG, repo, out, files)  # --clobber over existing assets
        print("assets:", release.assets(TAG, repo))
        got = release.fetch(TAG, repo, files[0]["name"], work / "down", files[0]["sha256"])
        print("downloaded and verified:", got)
    finally:
        release.gh("release", "delete", TAG, "--repo", repo, "--yes", "--cleanup-tag")
    print("release self-test OK")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
