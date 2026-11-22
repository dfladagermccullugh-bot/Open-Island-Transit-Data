"""Run the examples in ``examples/`` against the published data.

    python scripts/check_examples.py            # every example whose tool is installed
    python scripts/check_examples.py --require python duckdb

The Download page and the README show these scripts to people who have never
seen the data. A renamed column or a moved file would break them without
breaking any other test, so this runs each one for real, in an empty folder,
against the live ``data`` branch and Releases. An example whose tool, or a
library it loads, is not installed is skipped unless ``--require`` names it.
Needs the network.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
# name: (command, file fed to it on standard input, a quick command that
# succeeds only when the libraries the example loads are installed)
TOOLS: dict[str, tuple[list[str], Path | None, list[str] | None]] = {
    "python": (
        [sys.executable, str(EXAMPLES / "quickstart.py")],
        None,
        [sys.executable, "-c", "import pandas, pyarrow"],
    ),
    "r": (
        ["Rscript", str(EXAMPLES / "quickstart.R")],
        None,
        ["Rscript", "-e", "library(readr); library(arrow)"],
    ),
    "duckdb": (["duckdb"], EXAMPLES / "quickstart.sql", None),
}


def run(name: str) -> tuple[str, str]:
    """(``passed``, ``failed`` or ``skipped``, what was printed)."""
    command, stdin, probe = TOOLS[name]
    if shutil.which(command[0]) is None:
        return "skipped", f"{command[0]} is not installed"
    if probe and subprocess.run(probe, capture_output=True, timeout=120).returncode != 0:
        return "skipped", f"the libraries the {name} example loads are not installed"
    with tempfile.TemporaryDirectory() as folder:
        done = subprocess.run(
            command,
            cwd=folder,
            input=stdin.read_text(encoding="utf-8") if stdin else None,
            capture_output=True,
            text=True,
            timeout=600,
        )
    output = (done.stdout + done.stderr).strip()
    return ("passed" if done.returncode == 0 else "failed"), output


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the published examples.")
    ap.add_argument("--require", nargs="*", default=[], choices=sorted(TOOLS), metavar="TOOL")
    args = ap.parse_args(argv)
    failed = False
    for name in TOOLS:
        result, output = run(name)
        print(f"--- {name}: {result}")
        if result != "passed" or name in args.require:
            print(output[-2000:])
        if result == "failed" or (result == "skipped" and name in args.require):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
