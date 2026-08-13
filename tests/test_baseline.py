"""Baseline checks: the main module compiles and imports on a Tk-capable host.

The refactor is split into leaf modules that do not import tkinter, so most
tests never need a display. This smoke test only guards the monolith itself.
"""

import importlib.util
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "octo_updater.py"


def test_main_module_compiles():
    py_compile.compile(str(MAIN), doraise=True)


def test_main_module_imports_when_tk_available():
    if importlib.util.find_spec("_tkinter") is None:
        import pytest
        pytest.skip("tkinter not available on this host")
    subprocess.run(
        [sys.executable, "-c", "import octo_updater"],
        cwd=ROOT, check=True)
