"""Shared libtorrent/HTTP/archive fakes for the torrent-adjacent tests.

Consolidates the triplicated fakes previously copy-pasted across
``test_torrent_download.py``, ``test_client_update.py``,
``test_torrent_fallback_regression.py``, ``test_torrent_update_e2e.py``,
``test_assets.py``, ``test_mods.py``, ``test_sources.py`` and
``test_extract_security.py``.

Import as ``from _torrent_fakes import ...`` — pytest's ``prepend``
import mode puts ``tests/`` on ``sys.path`` (there is no
``tests/__init__.py``), so the ``tests.`` package prefix does NOT
resolve under pytest. The leading underscore keeps this module out
of test collection.

Bespoke one-off fakes (stall-on-peer-timing sessions, read-piece
alerts, piece-progress pumps, real-data e2e snapshot builders) stay
local to their test modules — only the canonical shared shapes live
here.
"""

import io
import zipfile
from types import SimpleNamespace

import nostalgia_launcher.services.update_backend.torrent_update as td

# ── client dir ───────────────────────────────────────────────────────────────


def make_client_dir(tmp_path, name="client"):
    """Create and return ``tmp_path / name`` (the fake game folder)."""
    d = tmp_path / name
    d.mkdir()
    return d


# ── HTTP response + urlopen ──────────────────────────────────────────────────


class BodyResp:
    """A fake HTTP response: serves ``body`` with slicing ``read()``.

    Covers every local variant: whole-body-once-then-EOF, chunked
    reads, ``headers`` and ``status``/``getcode()``.
    """

    def __init__(self, body=b"", headers=None, status=200):
        self._body = bytes(body)
        self._pos = 0
        self.headers = dict(headers or {})
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        if self._pos >= len(self._body):
            return b""
        if n is None or n < 0:
            out = self._body[self._pos :]
            self._pos = len(self._body)
        else:
            out = self._body[self._pos : self._pos + n]
            self._pos += len(out)
        return out

    def getcode(self):
        return self.status


# Back-compat alias for the ``test_client_update.py`` name.
_BodyResp = BodyResp


def fake_urlopen(
    body=b"",
    *,
    assert_suffix=None,
    calls=None,
    headers=None,
    status=200,
    exc=None,
):
    """Build a ``secure_urlopen`` fake serving ``body``.

    ``assert_suffix`` asserts the request URL tail; ``calls`` (a
    list) records each request; ``exc`` (an exception instance)
    makes every call raise instead (manifest-down paths).
    """

    def _open(req, timeout=10, allowed_hosts=None, **kw):
        if calls is not None:
            calls.append(req)
        if exc is not None:
            raise exc
        url = getattr(req, "full_url", req)
        if assert_suffix is not None:
            assert url.endswith(assert_suffix), url
        return BodyResp(body, headers=headers, status=status)

    return _open


def failing_urlopen(exc):
    """Build a ``secure_urlopen`` fake that always raises ``exc``."""
    return fake_urlopen(exc=exc)


# ── TorrentVerifier stand-ins ────────────────────────────────────────────────


def make_verifier_class(stale=None, error=None, calls=None):
    """Build a ``TorrentVerifier`` stand-in class.

    ``stale`` is the file list ``verify()`` returns, ``error`` (an
    exception instance) is raised instead, and ``calls`` (a list)
    records each verified URL.
    """
    stale_list = list(stale) if stale else []

    class _Verifier:
        def __init__(self, out_dir, dispatcher=None, *a, **kw):
            self.out_dir = out_dir

        def verify(self, url, snapshot=None):
            if calls is not None:
                calls.append(url)
            if error is not None:
                raise error
            return list(stale_list)

    return _Verifier


#: Up-to-date verifier (``verify()`` returns ``[]``).
FakeVerifier = make_verifier_class()

#: Cancelled verifier (``verify()`` raises ``RuntimeError``).
CancelledVerifier = make_verifier_class(error=RuntimeError("Cancelled"))


# ── libtorrent module fakes ──────────────────────────────────────────────────

_DEFAULT_PATHS = [
    "client/Data/a.bin",
    "client/Data/b.mpq",
    "client/WoW.exe",
]
_DEFAULT_SIZES = [1024, 2048, 4096]


def make_fake_lt(finished_after=3, paths=None, sizes=None):
    """A fake libtorrent module for ``TorrentDownloader`` tests.

    ``finished_after`` counts ``status()`` polls until the handle
    reports finished; ``paths``/``sizes`` override the torrent's
    file layout (defaults mirror the original fake).
    """
    _paths = list(paths) if paths is not None else list(_DEFAULT_PATHS)
    _sizes = list(sizes) if sizes is not None else list(_DEFAULT_SIZES)

    class FakeStatus:
        def __init__(self, finished=False, pieces_done=0):
            self.name = "client"
            self.total_wanted = 10
            self.total_wanted_done = 10 if finished else 0
            self.download_rate = 0
            self.num_peers = 0
            self.is_finished = finished
            # libtorrent 2.1 fields for verification
            self.verified_pieces = pieces_done
            self.checking_files = not finished

    class FakeHandle:
        def __init__(self):
            self.cancelled = False
            self.paused = False
            self.status_calls = 0

        def status(self):
            self.status_calls += 1
            return FakeStatus(
                self.status_calls >= finished_after,
                pieces_done=self.status_calls,
            )

        def cancel(self):
            self.cancelled = True

        def pause(self):
            self.paused = True

        def resume(self):
            self.paused = False

    class FakeFiles:
        def num_files(self):
            return len(_paths)

        def file_path(self, i):
            return _paths[i]

        def file_offset(self, i):
            return sum(_sizes[:i])

        def file_size(self, i):
            return _sizes[i]

    class FakeTorrentInfo:
        def files(self):
            return FakeFiles()

    class FakeSession:
        def __init__(self, settings):
            self.settings = settings
            self.atp = None
            self.removed = []

        def add_torrent(self, atp):
            self.atp = atp
            return FakeHandle()

        def pop_alerts(self):
            return []

        def wait_for_alert(self, ms):
            return None

        def remove_torrent(self, h):
            self.removed.append(h)

    class FakeLT:
        class alert:
            class category_t:
                error_notification = 1
                storage_notification = 8
                status_notification = 16

        class torrent_status:
            class states:
                checking_files = "checking_files"
                checking_resume_data = "checking_resume_data"
                queued_for_checking = "queued_for_checking"
                downloading = "downloading"
                finished = "finished"

        def __init__(self):
            self.last_session = None

        def torrent_info(self, path):
            return FakeTorrentInfo()

        def session(self, settings):
            self.last_session = FakeSession(settings)
            return self.last_session

        def add_torrent_params(self):
            return SimpleNamespace()

    return FakeLT()


def make_verifier_lt(stale_file=None, piece_count=3):
    """A libtorrent fake tailored to ``TorrentVerifier``.

    Two torrent files: ``client/Data/a.bin`` (pieces 0..1) and
    ``client/WoW.exe`` (piece 2). ``stale_file`` (if not None) is a
    piece index ``have_piece`` reports as missing after the recheck.
    """

    class FakeFiles:
        def __init__(self):
            self.paths = ["client/Data/a.bin", "client/WoW.exe"]
            self.sizes = [512, 256]

        def num_files(self):
            return len(self.paths)

        def file_path(self, i):
            return self.paths[i]

        def file_offset(self, i):
            return sum(self.sizes[:i])

        def file_size(self, i):
            return self.sizes[i]

    class FakeTorrentInfo:
        def files(self):
            return FakeFiles()

        def piece_length(self):
            return 256

        def num_pieces(self):
            return piece_count

    class FakeStatus:
        verified_pieces = [True] * piece_count
        state = "finished"
        progress = 1.0
        num_pieces = piece_count

    class FakeHandle:
        def __init__(self):
            self.force_rechecked = False
            self.cancelled = False

        def force_recheck(self):
            self.force_rechecked = True

        def cancel(self):
            self.cancelled = True

        def status(self):
            return FakeStatus()

        def have_piece(self, i):
            return i != stale_file

        def pause(self):
            pass

        def resume(self):
            self.paused = False

    class FakeSession:
        def __init__(self, settings):
            self.settings = settings
            self.atp = None
            self.removed = []

        def add_torrent(self, atp):
            self.atp = atp
            return FakeHandle()

        def pop_alerts(self):
            return []

        def wait_for_alert(self, ms):
            return None

        def remove_torrent(self, h):
            self.removed.append(h)

    class FakeLT:
        class alert:
            class category_t:
                error_notification = 1
                storage_notification = 8
                status_notification = 16

        class torrent_status:
            class states:
                checking_files = "checking_files"
                checking_resume_data = "checking_resume_data"
                queued_for_checking = "queued_for_checking"
                downloading = "downloading"
                finished = "finished"

        def __init__(self):
            self.last_session = None

        def torrent_info(self, path):
            return FakeTorrentInfo()

        def session(self, settings):
            self.last_session = FakeSession(settings)
            return self.last_session

        def add_torrent_params(self):
            return SimpleNamespace()

    return FakeLT()


def make_magnet_lt(metadata_poll=1, peers=3):
    """A fake libtorrent module exposing just what ``_resolve_magnet``
    uses.

    ``metadata_poll`` is the poll iteration after which ``status()``
    reports has_metadata; ``peers`` is the reported peer count.
    Returns ``(fake_module, holder)`` where ``holder["h"]`` /
    ``holder["ses"]`` are the created handle/session for assertions.
    """

    class FakeTI:
        def info_hashes(self):
            return SimpleNamespace(v1="cd" * 20, v2="")

    class FakeCreated:
        def generate(self):
            return {"info": "section"}

    class FakeStatus:
        def __init__(self, polls):
            self.has_metadata = polls >= metadata_poll
            self.num_peers = peers
            self.name = "client"

    class FakeHandle:
        def __init__(self):
            self.polls = 0
            self.resumed = False
            self.upload_mode = False

        def set_flags(self, flags):
            self.upload_mode = bool(flags)

        def resume(self):
            self.resumed = True

        def pause(self):
            pass

        def status(self):
            self.polls += 1
            return FakeStatus(self.polls)

        def torrent_file(self):
            return FakeTI()

    class FakeSession:
        def __init__(self, settings):
            self.settings = settings
            self.atp = None

        def add_torrent(self, atp):
            self.atp = atp
            return holder["h"]

        def pop_alerts(self):
            return []

        def wait_for_alert(self, ms):
            pass

    class FakeFlags:
        upload_mode = "upload-mode-flag"

    class FakeLT:
        torrent_flags = FakeFlags()
        last_session = None

        @staticmethod
        def parse_magnet_uri(uri):
            if not uri.startswith("magnet:?xt="):
                raise ValueError("bad magnet")
            return SimpleNamespace(save_path="", url=uri)

        @staticmethod
        def session(settings):
            FakeLT.last_session = FakeSession(settings)
            holder["ses"] = FakeLT.last_session
            return FakeLT.last_session

        @staticmethod
        def create_torrent(ti):
            return FakeCreated()

        @staticmethod
        def bencode(entry):
            return b"resolved-metadata"

    holder: dict = {"h": FakeHandle()}
    return FakeLT(), holder


def make_snapshot_fake_lt(
    info_hash="aa" * 20, resume_atp=None, save_alert=False
):
    """A libtorrent fake exposing info_hashes plus resume-data APIs."""

    class _InfoHashes:
        def __init__(self):
            self.v1 = info_hash
            self.v2 = ""

    class FakeFiles:
        def __init__(self):
            self.paths = ["client/Data/a.bin", "client/WoW.exe"]
            self.sizes = [1024, 4096]

        def num_files(self):
            return len(self.paths)

        def file_path(self, i):
            return self.paths[i]

        def file_offset(self, i):
            return sum(self.sizes[:i])

        def file_size(self, i):
            return self.sizes[i]

    class FakeTorrentInfo:
        def info_hashes(self):
            return _InfoHashes()

        def files(self):
            return FakeFiles()

        def piece_length(self):
            return 256

        def num_pieces(self):
            return 3

    class FakeStatus:
        def __init__(self, finished=False):
            self.name = "client"
            self.total_wanted = 10
            self.total_wanted_done = 10 if finished else 0
            self.download_rate = 0
            self.num_peers = 0
            self.is_finished = finished

    class FakeHandle:
        def __init__(self):
            self.status_calls = 0
            self.resume_requested = False
            self.resumed = False

        def status(self):
            self.status_calls += 1
            return FakeStatus(self.status_calls >= 3)

        def cancel(self):
            pass

        def pause(self):
            pass

        def resume(self):
            self.paused = False
            self.resumed = True

        def save_resume_data(self):
            self.resume_requested = True

    class save_resume_data_alert:
        def __init__(self):
            self.params = SimpleNamespace()

    class FakeSession:
        def __init__(self, settings):
            self.settings = settings
            self.atp = None
            self.removed = []
            self._alert = save_resume_data_alert() if save_alert else None
            self._pops = 0

        def add_torrent(self, atp):
            self.atp = atp
            self.handle = FakeHandle()
            return self.handle

        def pop_alerts(self):
            # The download pump drains alerts first; the save-resume
            # alert is only produced after _save_resume() requests it.
            self._pops += 1
            if self._alert is not None and self._pops >= 4:
                self._alert = None
                return [save_resume_data_alert()]
            return []

        def wait_for_alert(self, ms):
            return None

        def remove_torrent(self, h):
            self.removed.append(h)

    class FakeLT:
        class alert:
            class category_t:
                error_notification = 1
                storage_notification = 8
                status_notification = 16

        class torrent_status:
            class states:
                checking_files = "checking_files"
                checking_resume_data = "checking_resume_data"
                queued_for_checking = "queued_for_checking"
                downloading = "downloading"
                finished = "finished"

        def __init__(self):
            self.last_session = None
            self.resume_atp = resume_atp

        def torrent_info(self, path):
            return FakeTorrentInfo()

        def session(self, settings):
            self.last_session = FakeSession(settings)
            return self.last_session

        def add_torrent_params(self):
            return SimpleNamespace()

        def read_resume_data(self, buf):
            if self.resume_atp is None:
                raise ValueError("no resume data")
            return self.resume_atp

        def write_resume_data_buf(self, params):
            return b"resume-bytes"

    return FakeLT()


# ── installers / small plumbing ─────────────────────────────────────────────


def _install_lt(monkeypatch, fake, body=b"fake"):
    import sys

    monkeypatch.setitem(sys.modules, "libtorrent", fake)
    monkeypatch.setattr(td, "allowed_download_hosts", lambda: set())
    monkeypatch.setattr(td, "secure_urlopen", fake_urlopen(body))
    return fake


def install_fake_lt(monkeypatch, **kwargs):
    """Install the downloader fake ``make_fake_lt(**kwargs)``."""
    return _install_lt(monkeypatch, make_fake_lt(**kwargs))


def install_verifier_fake(monkeypatch, **kwargs):
    """Install the verifier fake ``make_verifier_lt(**kwargs)``."""
    return _install_lt(monkeypatch, make_verifier_lt(**kwargs))


def install_snapshot_fake(monkeypatch, **kwargs):
    """Install the snapshot fake ``make_snapshot_fake_lt(**kwargs)``."""
    return _install_lt(monkeypatch, make_snapshot_fake_lt(**kwargs))


def redirect_torrent_cache(monkeypatch, cache_root):
    """Point torrent metadata persistence at ``<cache_root>/torrents``."""
    monkeypatch.setattr(
        td, "torrent_cache_dir", lambda: str(cache_root / "torrents")
    )


def quiet_log():
    """A no-op ``log`` callable for ``_fetch_torrent``/resolvers."""
    return lambda m, t="": None


# ── launcher download-config bootstrap ───────────────────────────────────────

TORRENT_URL = "https://srv.example/client.torrent"
FALLBACK_URL = "https://srv.example/client.zip"
SERVER_URL = "https://srv.example"


def make_download_config(
    torrent_url=TORRENT_URL,
    fallback_url=FALLBACK_URL,
    server_url=SERVER_URL,
):
    """Build a ``launcher.configure_from_dict`` payload with a single
    torrent + HTTP-fallback download source."""
    return {
        "server": {
            "url": server_url,
            "download": {
                "torrent": {"torrent_url": torrent_url},
                "http": {"fallback": fallback_url},
            },
        }
    }


# ── archive builders ─────────────────────────────────────────────────────────


def make_zip(members):
    """Build zip bytes from ``{name: data_or_ZipInfo}``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            if isinstance(data, zipfile.ZipInfo):
                zf.writestr(data, b"evil")
            else:
                zf.writestr(name, data)
    return buf.getvalue()


def write_zip_file(path, members):
    """Write a zip file at ``path`` from ``{name: data_or_ZipInfo}``."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            if isinstance(data, zipfile.ZipInfo):
                zf.writestr(data, b"evil")
            else:
                zf.writestr(name, data)


# ── catalog entry builders ───────────────────────────────────────────────────


def make_asset_entry(**over):
    """Build a minimal valid asset entry (``test_assets.py`` shape)."""
    e = {
        "id": "patch3",
        "name": "Patch 3",
        "url": "https://server.test/uploads/patch-3.MPQ",
        "dest": "Data/patch-3.MPQ",
    }
    e.update(over)
    return e


def make_direct_file_entry(**src):
    """Build a ``direct_file`` catalog entry (``test_sources.py``)."""
    base = {
        "kind": "direct_file",
        "url": "https://server.test/uploads/patch-3.MPQ",
        "dest": "Data/patch-3.MPQ",
    }
    base.update(src)
    return {"id": "p3", "source": base}
