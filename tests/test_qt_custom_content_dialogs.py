"""Headless Qt tests for the custom mod/asset entry dialogs."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog

from nostalgia_launcher.services import catalog
from nostalgia_launcher.ui.qt.app import create_qt_app
from nostalgia_launcher.ui.qt.custom_asset_dialog import CustomAssetDialog
from nostalgia_launcher.ui.qt.custom_mod_dialog import CustomModDialog
from nostalgia_launcher.ui.qt.theme import Palette

QDialogAccepted = QDialog.DialogCode.Accepted


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return create_qt_app()


def test_mod_dialog_accepts_release_entry(qapp):
    dlg = CustomModDialog(Palette())
    dlg._id.setText("SuperWoW")
    dlg._kind.setCurrentText("github_release")
    dlg._owner.setText("example")
    dlg._repo.setText("superwow")
    dlg._pattern.setText("SuperWoW-*.zip")
    dlg._extract_map.setPlainText("SuperWoW.dll = SuperWoW.dll")
    dlg.modRequested.connect(lambda e: records.append(e))
    records = []
    dlg._submit()
    assert dlg.result() == QDialogAccepted
    assert records[0]["source"]["owner"] == "example"


def test_mod_dialog_rejects_invalid_url(qapp):
    dlg = CustomModDialog(Palette())
    dlg._id.setText("Bad")
    dlg._kind.setCurrentText("direct_file")
    dlg._url.setText("http://insecure.example/x.dll")
    dlg._dest.setText("x.dll")
    dlg._submit()
    assert dlg.result() != QDialogAccepted
    assert dlg._error.text()


def test_asset_dialog_roundtrip(qapp):
    dlg = CustomAssetDialog(Palette())
    dlg._id.setText("Patch")
    dlg._url.setText("https://cdn.example/p.mpq")
    dlg._dest.setText("Data/p.mpq")
    dlg._size.setText("not-a-number")
    dlg._submit()
    assert dlg.result() != QDialogAccepted
    assert "Size" in dlg._error.text()
    dlg._size.setText("4096")
    out = []
    dlg.assetRequested.connect(out.append)
    dlg._submit()
    assert dlg.result() == QDialogAccepted
    assert catalog.validate_asset(out[0]) is not None
    assert out[0]["size"] == 4096
