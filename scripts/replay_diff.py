"""Compare two replay folders and say exactly which published files differ.

    python scripts/replay_diff.py <baseline> <changed> [--expect-changed GLOB ...]

Each folder is one written by ``scripts/rehearse_nightly.py --replay``: it
holds ``data-branch/`` (the would-be data branch) and ``releases/<tag>/`` (the
would-be release files). Run both replays with the same ``--now`` so the
timestamps agree; then any difference comes from the code.

Exits 0 when the two agree, or when every file that differs matches one of the
``--expect-changed`` patterns (paths relative to the replay folder, as in
``data-branch/quality/daily.csv`` or ``releases/*/stop-events-*``). A pattern
that matches no differing file is reported too, because a change that was
meant to reach a file and did not is as much a surprise as one that reached a
file it should not have.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import sys
from pathlib import Path

COMPARED = ("data-branch", "releases")


def tree(root: Path) -> dict[str, str]:
    """SHA-256 of every file under the compared folders, by relative path."""
    found = {}
    for top in COMPARED:
        for path in sorted((root / top).rglob("*")):
            if path.is_file():
                found[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    return found


def compare(a: dict[str, str], b: dict[str, str]) -> dict[str, str]:
    """Paths that differ, each with how: ``changed``, ``only in baseline``, ``only in changed``."""
    out = {}
    for path in sorted(a.keys() | b.keys()):
        if path not in b:
            out[path] = "only in baseline"
        elif path not in a:
            out[path] = "only in changed"
        elif a[path] != b[path]:
            out[path] = "changed"
    return out


def judge(differing: dict[str, str], expected: list[str]) -> tuple[list[str], list[str]]:
    """Differing paths no pattern allows, and patterns that matched nothing."""
    surprises = [p for p in differing if not any(fnmatch.fnmatch(p, g) for g in expected)]
    idle = [g for g in expected if not any(fnmatch.fnmatch(p, g) for p in differing)]
    return surprises, idle


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare two replay folders.")
    ap.add_argument("baseline", type=Path)
    ap.add_argument("changed", type=Path)
    ap.add_argument("--expect-changed", nargs="*", default=[], metavar="GLOB")
    args = ap.parse_args(argv)
    a, b = tree(args.baseline), tree(args.changed)
    differing = compare(a, b)
    surprises, idle = judge(differing, args.expect_changed)
    for path, how in differing.items():
        print(f"{'unexpected' if path in surprises else 'expected  '}  {how}: {path}")
    for glob in idle:
        print(f"no file changed for --expect-changed {glob}")
    print(f"{len(a)} and {len(b)} files; {len(differing)} differ, {len(surprises)} unexpected")
    return 1 if surprises or idle else 0


if __name__ == "__main__":
    sys.exit(main())
