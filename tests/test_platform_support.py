"""Unit tests for platform detection and cross-platform helpers."""

import os
import subprocess
import sys
import unittest.mock as mock

import pytest

import platform_support
from platform_support import (
    can_launch_client,
    can_manage_antivirus,
    can_patch_client,
    cache_dir,
    config_dir,
    default_out_dir,
    is_linux,
    is_macos,
    is_windows,
    open_folder,
)


@pytest.fixture
def fake_platform(monkeypatch):
    """Set sys.platform for the duration of a test."""
    def _set(platform):
        monkeypatch.setattr(sys, "platform", platform)
    return _set


# ── detection ───────────────────────────────────────────────────────────────

def test_is_windows(fake_platform):
    fake_platform("win32")
    assert is_windows()
    assert not is_macos()
    assert not is_linux()


def test_is_macos(fake_platform):
    fake_platform("darwin")
    assert is_macos()
    assert not is_windows()


def test_is_linux(fake_platform):
    fake_platform("linux")
    assert is_linux()
    assert not is_windows()


# ── capabilities (option 2: generic only on non-Windows) ────────────────────

def test_capabilities_windows(fake_platform):
    fake_platform("win32")
    assert can_launch_client()
    assert can_patch_client()
    assert can_manage_antivirus()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_capabilities_non_windows(fake_platform, platform):
    fake_platform(platform)
    assert not can_launch_client()
    assert not can_patch_client()
    assert not can_manage_antivirus()


# ── config/cache dirs ───────────────────────────────────────────────────────

def test_config_dir_windows_uses_app_dir(fake_platform, monkeypatch, tmp_path):
    fake_platform("win32")
    monkeypatch.setattr(platform_support, "_app_dir", lambda: str(tmp_path))
    assert config_dir() == str(tmp_path)


def test_config_dir_linux_uses_xdg(fake_platform, monkeypatch, tmp_path):
    fake_platform("linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_dir() == str(tmp_path / "xdg" / "octo-updater")


def test_config_dir_linux_falls_back_to_home(fake_platform, monkeypatch):
    fake_platform("linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/user")
    assert config_dir() == "/home/user/.config/octo-updater"


def test_config_dir_macos_application_support(fake_platform, monkeypatch):
    fake_platform("darwin")
    monkeypatch.setenv("HOME", "/Users/user")
    assert config_dir() == \
        "/Users/user/Library/Application Support/OctoUpdater"


def test_cache_dir_macos(fake_platform, monkeypatch):
    fake_platform("darwin")
    monkeypatch.setenv("HOME", "/Users/user")
    assert cache_dir() == "/Users/user/Library/Caches/OctoUpdater"


def test_default_out_dir_non_windows_writable(fake_platform, monkeypatch):
    fake_platform("linux")
    monkeypatch.setenv("HOME", "/home/user")
    assert default_out_dir() == "/home/user/OctoWoW"


# ── open_folder ─────────────────────────────────────────────────────────────

def test_open_folder_windows(fake_platform, monkeypatch):
    fake_platform("win32")
    popen = mock.Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    open_folder("C:\\games")
    popen.assert_called_once_with(["explorer.exe", "C:\\games"], close_fds=True)


def test_open_folder_macos(fake_platform, monkeypatch):
    fake_platform("darwin")
    popen = mock.Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    open_folder("/games")
    popen.assert_called_once_with(["open", "/games"], close_fds=True)


def test_open_folder_linux(fake_platform, monkeypatch):
    fake_platform("linux")
    popen = mock.Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    open_folder("/games")
    popen.assert_called_once_with(["xdg-open", "/games"], close_fds=True)


def test_open_folder_missing_binary_raises(fake_platform, monkeypatch):
    fake_platform("linux")
    monkeypatch.setattr(
        subprocess, "Popen", mock.Mock(side_effect=FileNotFoundError("xdg-open")))
    with pytest.raises(OSError):
        open_folder("/games")
