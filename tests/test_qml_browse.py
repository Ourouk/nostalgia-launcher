"""QML browse-dialog tests: catalog search + one-click install."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest

pytest.importorskip("PySide6")

from nostalgia_launcher.ui.qml.browse import BrowseModel


@pytest.fixture()
def model():
    return BrowseModel()


def _entries():
    return [
        {
            "id": "a",
            "name": "Alpha",
            "version": "1.0",
            "description": "shiny",
            "repo_url": "",
        },
        {
            "id": "b",
            "name": "Beta",
            "version": "2.0",
            "description": "beta",
            "repo_url": "",
        },
    ]


def test_search_filters(model):
    model.set_entries(_entries())
    assert model.rowCount() == 2
    model.setFilterText("alp")
    assert model.rowCount() == 1
    model.setFilterText("")
    assert model.rowCount() == 2


def test_install_calls_handler(model):
    calls = []
    model.set_entries(_entries())
    model.set_install_handler(lambda eid: calls.append(eid) or True)
    model.install("b")
    assert calls == ["b"]
    assert model.property("busy") is True
    model.set_running(False)
    assert model.property("busy") is False
