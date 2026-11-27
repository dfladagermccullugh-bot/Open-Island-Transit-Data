"""Fail when the code behind a stage's numbers changed but its version did not.

    python scripts/check_versions.py           # check (CI runs this)
    python scripts/check_versions.py --update  # record the current code in the lock

Each stage's version (``EVENTS_VERSION`` and the rest) goes into every file
the stage writes, and a new version makes the nightly job rebuild every day.
A change to how a number is computed must therefore come with a new version.
Nothing enforced that for shared code: a change to ``pipeline/stats.py``
reaches every stage that takes a median, and no stage would have noticed.

``pipeline/versions.lock.json`` lists, for each stage, the version and the
sources that compute its numbers: whole modules (``pipeline/stats.py``) or
single top-level definitions (``pipeline/common.py::csv_cell``). It stores a
hash of their syntax tree, so comments, docstrings, formatting and the
version line itself do not count. The check fails when

- a stage's hash differs from the lock while its version does not: bump the
  version (and follow CLAUDE.md for a change to figures), then ``--update``;
- the version differs from the lock: run ``--update`` to record it.

A change that is known to leave every output alone (a speed-up shown
byte-neutral by ``tests/test_golden.py`` and a replay) may be recorded with
``--update`` without a bump; say so in the commit message.

Standard library only.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "pipeline" / "versions.lock.json"


def _strip(node: ast.AST, drop: str | None) -> ast.AST:
    """``node`` without docstrings and without the assignment of ``drop``."""
    for n in ast.walk(node):
        body = getattr(n, "body", None)
        if not isinstance(body, list):
            continue
        keep = []
        for i, child in enumerate(body):
            if (
                i == 0
                and isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, str)
                and isinstance(
                    n, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
                )
            ):
                continue
            if drop and isinstance(child, ast.Assign | ast.AnnAssign):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                if any(isinstance(t, ast.Name) and t.id == drop for t in targets):
                    continue
            keep.append(child)
        n.body = keep or [ast.Pass()]  # type: ignore[attr-defined]
    return node


def _named(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name == name:
                return node
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return node
    raise KeyError(name)


def source_hash(source: str, drop: str | None = None, root: Path = ROOT) -> str:
    """Hash of one source entry: ``path`` or ``path::name``."""
    path, _, name = source.partition("::")
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    node = _named(tree, name) if name else tree
    return hashlib.sha256(ast.dump(_strip(node, drop)).encode()).hexdigest()


def version_of(constant: str, root: Path = ROOT) -> str:
    """The value of a version constant, ``path::NAME``, read without importing."""
    path, _, name = constant.partition("::")
    node = _named(ast.parse((root / path).read_text(encoding="utf-8")), name)
    assert isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None
    value = ast.literal_eval(node.value)
    assert isinstance(value, str)
    return value


def stage_hash(stage: dict[str, Any], root: Path = ROOT) -> str:
    const_path, _, const_name = stage["constant"].partition("::")
    h = hashlib.sha256()
    for source in stage["sources"]:
        drop = const_name if source.partition("::")[0] == const_path else None
        h.update(f"{source}\0{source_hash(source, drop, root)}\0".encode())
    return h.hexdigest()


def check(lock: dict[str, Any], root: Path = ROOT) -> list[str]:
    problems = []
    for name, stage in lock["stages"].items():
        version = version_of(stage["constant"], root)
        changed = stage_hash(stage, root) != stage["hash"]
        if version != stage["version"]:
            problems.append(
                f"{name}: version is {version}, the lock records {stage['version']}: "
                "run scripts/check_versions.py --update"
            )
        elif changed:
            problems.append(
                f"{name}: the code behind its numbers changed but {version} did not: "
                "bump the version, or, if every output is unchanged, run --update"
            )
    return problems


def update(lock: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    for stage in lock["stages"].values():
        stage["version"] = version_of(stage["constant"], root)
        stage["hash"] = stage_hash(stage, root)
    return lock


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check stage versions against their code.")
    ap.add_argument("--update", action="store_true", help="record the current code in the lock")
    ap.add_argument("--lock", type=Path, default=LOCK)
    args = ap.parse_args(argv)
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    if args.update:
        args.lock.write_text(json.dumps(update(lock), indent=1) + "\n", encoding="utf-8")
        print(f"{args.lock.name}: {len(lock['stages'])} stages recorded")
        return 0
    problems = check(lock)
    for p in problems:
        print(f"PROBLEM {p}")
    print(f"{len(lock['stages'])} stages checked: {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
