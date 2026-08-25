"""Unit tests for the per-profile advisory store lock (core/app_lock).

Subprocess-based cases prove the cross-PROCESS semantics: a second
process fails fast on a held lock, a SIGKILL'd holder frees it, and the
NOSTALGIA_RELAUNCH grace window outlasts a holder that releases shortly.
POSIX-specific paths are skipped on Windows.
"""

import os
import subprocess
import sys
import time

import pytest

from nostalgia_launcher.core import app_lock

_HOLDER_SCRIPT = """
import sys, time
from nostalgia_launcher.core import app_lock
app_lock.acquire_store_lock(sys.argv[1])
print("READY", flush=True)
time.sleep(float(sys.argv[2]))
"""


@pytest.fixture(autouse=True)
def _clean_lock():
    app_lock.release_store_lock()
    yield
    app_lock.release_store_lock()


def test_state_key_stable_and_distinct():
    a = "/x/state.json"
    assert app_lock.state_key(a) == app_lock.state_key(a)
    assert app_lock.state_key(a) != app_lock.state_key("/y/state.json")
    assert app_lock.state_key(a).startswith("nostalgia-launcher-")
    assert len(app_lock.state_key(a)) == len("nostalgia-launcher-") + 12


def test_lock_file_sits_beside_state_file():
    assert app_lock.lock_file_for("/cfg/profiles/p/state.json") == (
        "/cfg/profiles/p/state.lock"
    )
    assert app_lock.lock_file_for("/cfg/nostalgia_launcher_config.json") == (
        "/cfg/nostalgia_launcher_config.lock"
    )


def test_acquire_release_reacquire(tmp_path):
    state = str(tmp_path / "state.json")
    app_lock.acquire_store_lock(state)
    # Idempotent within the process:
    app_lock.acquire_store_lock(state)
    app_lock.release_store_lock()
    app_lock.acquire_store_lock(state)


def _spawn_holder(state_path, hold_s):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _HOLDER_SCRIPT,
            state_path,
            str(hold_s),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX flock")
def test_second_process_fails_fast(tmp_path):
    state = str(tmp_path / "state.json")
    proc = _spawn_holder(state, hold_s=10)
    try:
        assert proc.stdout.readline().strip() == "READY"
        start = time.monotonic()
        with pytest.raises(app_lock.AcquireError):
            app_lock.acquire_store_lock(state)
        assert time.monotonic() - start < 2.0  # no grace without env var
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX flock")
def test_sigkilled_holder_frees_lock(tmp_path):
    state = str(tmp_path / "state.json")
    proc = _spawn_holder(state, hold_s=60)
    try:
        assert proc.stdout.readline().strip() == "READY"
    finally:
        proc.kill()
        proc.wait()
    app_lock.acquire_store_lock(state)  # OS dropped the flock at death


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX flock")
def test_grace_window_waits_for_relaunching_parent(tmp_path):
    state = str(tmp_path / "state.json")
    # Holder releases after ~300 ms (simulates the quitting parent).
    proc = _spawn_holder(state, hold_s=0.3)
    try:
        assert proc.stdout.readline().strip() == "READY"
    finally:
        pass
    os.environ["NOSTALGIA_RELAUNCH"] = "1"
    try:
        start = time.monotonic()
        app_lock.acquire_with_grace(state)
        elapsed = time.monotonic() - start
    finally:
        os.environ.pop("NOSTALGIA_RELAUNCH", None)
        proc.wait()
    assert elapsed < 4.0


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX flock")
def test_no_grace_without_env_var(tmp_path):
    state = str(tmp_path / "state.json")
    proc = _spawn_holder(state, hold_s=10)
    try:
        assert proc.stdout.readline().strip() == "READY"
        os.environ.pop("NOSTALGIA_RELAUNCH", None)
        start = time.monotonic()
        with pytest.raises(app_lock.AcquireError):
            app_lock.acquire_with_grace(state)
        assert time.monotonic() - start < 2.0
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX flock")
def test_different_profiles_may_run_in_parallel(tmp_path):
    """--profile A running must NOT block --profile B: distinct state
    paths -> distinct keys and distinct lock files."""
    state_a = str(tmp_path / "a" / "state.json")
    state_b = str(tmp_path / "b" / "state.json")
    assert app_lock.state_key(state_a) != app_lock.state_key(state_b)
    assert app_lock.lock_file_for(state_a) != app_lock.lock_file_for(state_b)
    proc = _spawn_holder(state_a, hold_s=10)
    try:
        assert proc.stdout.readline().strip() == "READY"
        app_lock.acquire_store_lock(state_b)  # B while A held elsewhere
        app_lock.release_store_lock()
    finally:
        proc.kill()
        proc.wait()
