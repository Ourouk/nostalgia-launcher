"""qmllint gate for QML sources (skips when the binary is absent).

qmllint is a Qt binary, not a PyPI/uv package: the `pyside6` wheel does
not bundle it and no `pyside6-qmllint` package exists. Candidate
locations, first hit wins: `PATH`, then the distro Qt path
(`/usr/lib/qt6/bin/qmllint`, owned by `qt6-declarative` on Arch).
"""

import os
import shutil
import subprocess

import pytest

QML_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "src",
    "nostalgia_launcher",
    "ui",
    "qml",
    "qml",
)

_CANDIDATES = ("qmllint", "/usr/lib/qt6/bin/qmllint")


def _pyside6_qml_dir() -> str | None:
    """QML modules bundled in the PySide6 wheel (portable Arch/Ubuntu)."""
    try:
        import PySide6

        cand = os.path.join(
            os.path.dirname(os.path.abspath(PySide6.__file__)),
            "Qt",
            "qml",
        )
        if os.path.isdir(os.path.join(cand, "QtQuick")):
            return cand
    except Exception:
        pass
    return None


def _import_paths() -> list[str]:
    """-I dirs for qmllint (PySide6 wheel first, distro Qt fallback)."""
    paths = []
    for cand in (
        _pyside6_qml_dir(),
        "/usr/lib/qt6/qml",  # Arch qt6-declarative + Ubuntu -dev-tools
        os.environ.get("QML_IMPORT_PATH", ""),
    ):
        if cand and os.path.isdir(cand) and cand not in paths:
            paths.append(cand)
    return paths


# Base-module import failure is environment noise (version-skewed or
# missing QtQuick on CI), not a QML bug — skip the file instead of
# failing (same spirit as the QMLLINT-is-None skip).
_BASE_IMPORT_MARKERS = (
    "Warnings occurred while importing base modules",
    "Failed to import QtQuick",
    "jsroot.qmltypes",
)


def _qmllint():
    for cand in _CANDIDATES:
        path = shutil.which(cand) if "/" not in cand else cand
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


QMLLINT = _qmllint()

# Warning classes that are by design or environment-specific — never
# failures (see AGENTS.md "QML lint"):
_IGNORED = (
    "[unqualified]",  # engine root-context globals (appTheme, *Model)
    "OpenDirectory",  # undefined on the offscreen backend, guarded at runtime
    "Did you mean",  # qmllint follow-up hint for the line above
)

# Actionable classes: layout misuse is real undefined behavior; any other
# error outside the ignore list fails the file.
_FAIL_MARKERS = ("layout-positioning", "Error")


def _qml_files():
    return sorted(f for f in os.listdir(QML_DIR) if f.endswith(".qml"))


def _lint(path: str) -> str:
    cmd = [QMLLINT]
    for imp in _import_paths():
        cmd += ["-I", imp]
    cmd.append(path)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return (proc.stdout or "") + (proc.stderr or "")


@pytest.mark.skipif(
    QMLLINT is None,
    reason="qmllint not found (PATH or /usr/lib/qt6/bin/qmllint "
    "from qt6-declarative) — install distro Qt to run the QML lint",
)
def test_qmllint_available():
    assert QMLLINT is not None


@pytest.mark.skipif(QMLLINT is None, reason="no qmllint binary")
@pytest.mark.parametrize("name", _qml_files())
def test_qml_file_lints_clean(name):
    out = _lint(os.path.join(QML_DIR, name))
    if any(mark in out for mark in _BASE_IMPORT_MARKERS):
        pytest.skip(
            f"qmllint cannot resolve QtQuick base modules here "
            f"({_qmllint()}); environment issue, not a QML bug"
        )
    problems = []
    for line in out.splitlines():
        if any(ign in line for ign in _IGNORED):
            continue
        if any(mark in line for mark in _FAIL_MARKERS) or (
            line.strip().startswith("Warning:") and "[unqualified]" not in line
        ):
            # Re-check: only keep warnings that are NOT unqualified (the
            # dominant benign class); anything else deserves attention.
            problems.append(line)
    # Collapse: unqualified lines already filtered; report the rest.
    assert not problems, "\n".join(problems)
