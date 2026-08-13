"""Unit tests for the client update engine (VerifyWorker/UpdateWorker)."""

import json
import os
import queue
import urllib.request

import pytest

import octo_updater.services.client_update as client_update
from octo_updater.services.client_update import UpdateWorker, VerifyWorker


def _mk_client(tmp_path):
    d = tmp_path / "client"
    d.mkdir()
    return d


def test_verify_worker_up_to_date(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    f = client / "data.bin"
    f.write_bytes(b"x")

    manifest = {"root": {"files": [
        {"type": "file", "name": "data.bin",
         "hash": "11F6AD8EC52A2984ABAAFD7C3B516503785C2072",  # sha1("x")
         "size": 1},
    ]}}
    fake_resp = type("R", (), {"__enter__": lambda s: s,
                               "__exit__": lambda *a: None,
                               "read": lambda s: json.dumps(manifest).encode()})
    monkeypatch.setattr(client_update, "secure_urlopen",
                        lambda *a, **k: fake_resp())

    log_q, prog_q = queue.Queue(), queue.Queue()
    vw = VerifyWorker(str(client), log_q, prog_q)
    vw.run()

    msgs = [log_q.get_nowait()[0] for _ in range(log_q.qsize())]
    assert "__UP_TO_DATE__" in msgs
    assert "__DIFF_TREE__" not in msgs


def test_verify_worker_detects_stale_file(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    (client / "data.bin").write_bytes(b"old")

    manifest = {"root": {"files": [
        {"type": "file", "name": "data.bin",
         "hash": "11F6AD8EC52A2984ABAAFD7C3B516503785C2072",  # sha1("x")
         "size": 1},
    ]}}
    fake_resp = type("R", (), {"__enter__": lambda s: s,
                               "__exit__": lambda *a: None,
                               "read": lambda s: json.dumps(manifest).encode()})
    monkeypatch.setattr(client_update, "secure_urlopen",
                        lambda *a, **k: fake_resp())

    log_q, prog_q = queue.Queue(), queue.Queue()
    vw = VerifyWorker(str(client), log_q, prog_q)
    vw.run()

    msgs = [log_q.get_nowait()[0] for _ in range(log_q.qsize())]
    assert "__UPDATE_NEEDED__" in msgs
    assert "__DIFF_TREE__" in msgs


def test_verify_worker_config_wtf_created_when_missing(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    manifest = {"root": {"files": []}}
    fake_resp = type("R", (), {"__enter__": lambda s: s,
                               "__exit__": lambda *a: None,
                               "read": lambda s: json.dumps(manifest).encode()})
    monkeypatch.setattr(client_update, "secure_urlopen",
                        lambda *a, **k: fake_resp())

    log_q, prog_q = queue.Queue(), queue.Queue()
    vw = VerifyWorker(str(client), log_q, prog_q)
    vw.run()
    assert (client / "WTF" / "Config.wtf").exists()


def test_nodes_contain_wow_exe():
    assert UpdateWorker._nodes_contain_wow_exe(None) is True
    assert UpdateWorker._nodes_contain_wow_exe(
        [{"type": "file", "name": "WoW.exe"}]) is True
    assert UpdateWorker._nodes_contain_wow_exe(
        [{"type": "dir", "files": [{"type": "file", "name": "WoW.exe"}]}]) is True
    assert UpdateWorker._nodes_contain_wow_exe(
        [{"type": "file", "name": "data.bin"}]) is False


def test_update_worker_downloads_and_verifies(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    payload = b"hello world"

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            # Return the payload once, then EOF — mirrors a real socket.
            if getattr(self, "_exhausted", False):
                return b""
            self._exhausted = True
            return payload

        def getcode(self):
            return 200

    calls = {"n": 0}
    def fake_urlopen(req, timeout, allowed_hosts=None):
        calls["n"] += 1
        assert req.full_url.endswith("/data.bin")
        return FakeResp()

    monkeypatch.setattr(client_update, "secure_urlopen", fake_urlopen)

    log_q, prog_q = queue.Queue(), queue.Queue()
    worker = UpdateWorker(str(client), log_q, prog_q)
    import hashlib
    digest = worker.download("https://octowow.st/client/latest/data.bin",
                             str(client / "data.bin"), len(payload))
    assert digest == hashlib.sha1(payload).hexdigest().upper()
    assert (client / "data.bin").read_bytes() == payload


def test_update_worker_traverse_skips_up_to_date(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    f = client / "data.bin"
    f.write_bytes(b"x")
    node = {"type": "file", "name": "data.bin", "size": 1,
            "hash": "11F6AD8EC52A2984ABAAFD7C3B516503785C2072"}

    def fail(*a, **k):
        raise AssertionError("download must not be attempted for a matching file")

    monkeypatch.setattr(client_update, "secure_urlopen", fail)
    log_q, prog_q = queue.Queue(), queue.Queue()
    worker = UpdateWorker(str(client), log_q, prog_q)
    worker.traverse(node, [])
    assert (client / "data.bin").read_bytes() == b"x"
