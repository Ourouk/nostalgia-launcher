"""Unit tests for the launcher theme resolution (core/themes)."""

from vanilla_wow_launcher.core.themes import (
    DEFAULT_COLORS,
    COLOR_KEYS,
    resolve_colors,
)


def test_default_is_octowow_palette():
    assert DEFAULT_COLORS["C_BG"] == "#120e1a"
    assert DEFAULT_COLORS["C_GOLD"] == "#c8922a"
    assert set(COLOR_KEYS) == set(DEFAULT_COLORS)


def test_none_returns_default():
    assert resolve_colors(None) is DEFAULT_COLORS


def test_valid_dict_overrides_on_default():
    colors = resolve_colors({"C_GOLD": "#d4a02f", "C_BG": "#101218"})
    assert colors["C_GOLD"] == "#d4a02f"
    assert colors["C_BG"] == "#101218"
    # Unlisted slots keep the default.
    assert colors["C_TEXT"] == DEFAULT_COLORS["C_TEXT"]
    # The defaults are never mutated.
    assert DEFAULT_COLORS["C_GOLD"] == "#c8922a"


def test_non_dict_falls_back():
    for spec in ("octowow", 42, ["C_GOLD"], None):
        assert resolve_colors(spec) is DEFAULT_COLORS


def test_empty_dict_falls_back():
    assert resolve_colors({}) is DEFAULT_COLORS


def test_bad_hex_falls_back():
    for value in ("red", "#zzzzzz", "#12345", "#1234567", 0xFF0000):
        assert resolve_colors({"C_GOLD": value}) is DEFAULT_COLORS


def test_unknown_slot_falls_back():
    assert resolve_colors({"C_NOT_A_COLOR": "#d4a02f"}) is DEFAULT_COLORS


def test_any_invalid_entry_poisons_the_whole_theme():
    spec = {"C_GOLD": "#d4a02f", "C_BG": "not-a-color"}
    assert resolve_colors(spec) is DEFAULT_COLORS
