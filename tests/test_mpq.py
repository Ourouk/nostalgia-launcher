"""Tests for services/mpq.py — stock tables, scanning and safe removal."""

import os

import pytest

from nostalgia_launcher.services import mpq


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x")


# ── stock tables ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "version,name",
    [
        # Documented Vanilla set (patch chain ends at -2).
        ("1.12.1", "dbc.MPQ"),
        ("1.12.1", "patch-2.mpq"),
        # TBC introduces locales; its chain also ends at -2.
        ("2.4.3", "common.mpq"),
        ("2.4.3", "locale-enus.MPQ"),
        ("2.4.3", "expansion-locale-enGB.mpq"),
        # WotLK adds lichking + patch-3.
        ("3.3.5a", "lichking.MPQ"),
        ("3.3.5a", "common-2.mpq"),
        ("3.3.5a", "patch-enUS-3.mpq"),
        ("3.3.5a", "base-enUS.mpq"),
        # Cata's wow-update system.
        ("4.3.4", "art.mpq"),
        ("4.3.4", "wow-update-base-15595.mpq"),
        ("4.3.4", "wow-update-enUS-16016.mpq"),
        ("4.3.4", "expansion2-locale-deDE.mpq"),
        ("4.3.4", "base-7.mpq"),
        # MoP extends expansions to 4 and keeps the update system.
        ("5.4.8", "misc.mpq"),
        ("5.4.8", "world2.mpq"),
        ("5.4.8", "expansion4.mpq"),
        ("5.4.8", "wow-update-base-18505.mpq"),
    ],
)
def test_stock_names(version, name):
    assert mpq.is_stock_mpq(name, version)


@pytest.mark.parametrize(
    "version,name",
    [
        # Vanilla/TBC chains end at patch-2: higher patches are custom.
        ("1.12.1", "patch-3.MPQ"),
        ("2.4.3", "patch-3.mpq"),
        # WotLK ends at patch-3.
        ("3.3.5a", "patch-4.mpq"),
        # Not Blizzard names in any era.
        ("1.12.1", "mymod.mpq"),
        ("3.3.5a", "expansion2.mpq"),  # classic custom-patch disguise
        ("4.3.4", "patch-enUS-9.mpq"),
    ],
)
def test_non_stock_names(version, name):
    assert not mpq.is_stock_mpq(name, version)


def test_unsupported_version_rejected():
    with pytest.raises(ValueError):
        mpq.scan_custom_mpqs("nowhere", "6.0.0")


# ── scanning ─────────────────────────────────────────────────────────────────


def test_scan_classifies_buckets(tmp_path):
    client = tmp_path / "client"
    data = client / "Data"
    _touch(str(data / "dbc.MPQ"))
    _touch(str(data / "patch-3.MPQ"))  # managed below
    _touch(str(data / "enUS" / "patch-enUS-9.MPQ"))  # foreign
    _touch(str(data / "Cache" / "patch-base-1.MPQ"))  # cache skipped
    _touch(str(data / "readme.txt"))  # not an MPQ

    got = mpq.scan_custom_mpqs(
        str(client),
        "1.12.1",
        mpq.managed_dests([{"dest": "Data/patch-3.MPQ"}]),
    )
    assert got["stock"] == ["Data/dbc.MPQ"]
    assert [e["path"] for e in got["custom_managed"]] == ["Data/patch-3.MPQ"]
    assert [e["path"] for e in got["custom_foreign"]] == [
        "Data/enUS/patch-enUS-9.MPQ"
    ]
    assert got["custom_foreign"][0]["size"] == 1


def test_scan_matches_managed_by_basename_in_locale_dir(tmp_path):
    client = tmp_path / "client"
    _touch(str(client / "Data" / "enUS" / "custom.mpq"))
    got = mpq.scan_custom_mpqs(
        str(client), "1.12.1", mpq.managed_dests([{"dest": "Data/custom.MPQ"}])
    )
    assert got["custom_foreign"] == []
    assert len(got["custom_managed"]) == 1


def test_scan_missing_data_dir(tmp_path):
    got = mpq.scan_custom_mpqs(str(tmp_path / "client"), "3.3.5a")
    assert got["stock"] == []
    assert got["custom_managed"] == []
    assert got["custom_foreign"] == []


# ── removal guard ────────────────────────────────────────────────────────────


def test_remove_custom_mpq_guardrails(tmp_path):
    client = tmp_path / "client"
    target = client / "Data" / "enUS" / "weird.mpq"
    _touch(str(target))
    outside = client / "WoW.exe"
    _touch(str(outside))

    # Refuse anything outside Data/, traversal included.
    assert mpq.remove_custom_mpq(str(client), "WoW.exe")
    assert mpq.remove_custom_mpq(str(client), "Data/../WoW.exe")
    assert outside.exists()
    # Real removal works and reports ''.
    assert mpq.remove_custom_mpq(str(client), "Data/enUS/weird.mpq") == ""
    assert not target.exists()
    assert mpq.remove_custom_mpq(str(client), "Data/enUS/weird.mpq") == (
        "File no longer exists."
    )
