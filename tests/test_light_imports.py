"""The watchdog workflow installs only ``requests``.

It runs every half hour, and installing the whole pipeline took most of each
run. So the scripts it starts, and every module they import, must load without
numpy, pyarrow or protobuf. Nothing else would catch a stray import until the
watchdog itself failed on GitHub, silently, with the collector unwatched.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAVY = ("numpy", "pyarrow", "google.protobuf", "pandas")
SCRIPTS = ("watchdog.py", "catch_up.py")

PROBE = """
import importlib.abc, importlib.util, sys

HEAVY = {heavy!r}

class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == h or name.startswith(h + ".") for h in HEAVY):
            raise ImportError("the watchdog must not need " + name)
        return None

sys.meta_path.insert(0, Refuse())
spec = importlib.util.spec_from_file_location("probe", {script!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
"""


@pytest.mark.parametrize("script", SCRIPTS)
def test_watchdog_scripts_load_without_the_heavy_libraries(script):
    code = PROBE.format(heavy=HEAVY, script=str(ROOT / "scripts" / script))
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr[-2000:]


GATE = """
import importlib.abc, sys

HEAVY = {heavy!r}

class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == h or name.startswith(h + ".") for h in HEAVY):
            raise ImportError("the light gate must not need " + name)
        return None

sys.meta_path.insert(0, Refuse())
from pipeline import invariants
sys.exit(invariants.main(["--data", {data!r}, "--light"]))
"""


def test_the_light_publishing_gate_runs_without_the_heavy_libraries():
    """The site job runs ``pipeline.invariants --light`` with only Python
    installed, before it copies the data into the site."""
    code = GATE.format(heavy=HEAVY, data=str(ROOT / "tests" / "golden" / "data-branch"))
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
