"""Baseline consistency checks for repository metadata."""

import pathlib
import tomllib

from octo_updater.core.constants import UPDATER_VERSION


def test_updater_version_matches_pyproject():
    pyproject = tomllib.loads(
        (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml")
        .read_text(encoding="utf-8"))
    assert UPDATER_VERSION == pyproject["project"]["version"]
