"""Unit tests for the shared source backends (`services/sources`)."""

import pytest

import nostalgia_launcher.services.sources.direct_file as df_module
import nostalgia_launcher.services.sources.github_release as gh_module
from nostalgia_launcher.services import sources
from nostalgia_launcher.services.sources import deploy, hooks
from nostalgia_launcher.services.sources.base import FetchResult

# ── registry ─────────────────────────────────────────────────────────────────


def test_registry_has_all_wire_kinds():
    assert set(sources.kinds()) == {
        "github_release",
        "codeberg_release",
        "direct_file",
        "direct_tar",
        "git_archive",
    }


def test_get_unknown_kind_raises():
    with pytest.raises(KeyError):
        sources.get("carrier_pigeon")


def test_hook_policy_per_type():
    assert sources.TYPE_HOOK_POLICY["mods"] == {"write_dxvk_conf"}
    assert sources.TYPE_HOOK_POLICY["assets"] == frozenset()
    assert sources.TYPE_HOOK_POLICY["addons"] == frozenset()


# ── github_release backend ───────────────────────────────────────────────────


class _Resp:
    def __init__(self, payload, headers=None):
        self._payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        out, self._payload = self._payload[:n], self._payload[n:]
        return out


def test_github_validate_normalizes_source():
    b = sources.get("github_release")
    cleaned = b.validate(
        {
            "kind": "github_release",
            "owner": " Owner ",
            "repo": "repo.git!",
            "asset_pattern": "*.zip",
            "prefer_no": "-dxvk",
            "version_from": "bogus",
        }
    )
    # repo slug refuses "!" → entry invalid
    assert cleaned is None


def test_github_fetch_downloads_matched_asset(monkeypatch):
    b = sources.get("github_release")
    release = {
        "tag_name": "v1.2",
        "assets": [
            {
                "name": "m-1.2-dxvk.zip",
                "size": 2,
                "browser_download_url": "https://github.com/a/m-dxvk.zip",
            },
            {
                "name": "m-1.2.zip",
                "size": 3,
                "browser_download_url": "https://github.com/a/m.zip",
            },
        ],
    }
    monkeypatch.setattr(
        b, "latest_release", lambda src, raise_errors=False: release
    )
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return b"ZIPDATA"

    monkeypatch.setattr(gh_module, "fetch_bytes", fake_fetch)
    entry = {
        "id": "m",
        "source": {
            "kind": "github_release",
            "owner": "a",
            "repo": "m",
            "asset_pattern": "*.zip",
            "prefer_no": "-dxvk",
            "extract_map": None,
        },
    }
    result = b.fetch(entry)
    assert result.data == b"ZIPDATA"
    assert result.version == "v1.2"
    assert result.name == "m-1.2.zip"  # prefer_no demoted the dxvk asset
    assert seen["url"] == "https://github.com/a/m.zip"


def test_github_fetch_without_matching_asset_raises():
    b = sources.get("github_release")
    monkey_release = {"tag_name": "v1", "assets": []}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        b, "latest_release", lambda src, raise_errors=False: monkey_release
    )
    entry = {
        "id": "m",
        "source": {
            "kind": "github_release",
            "owner": "a",
            "repo": "m",
            "asset_pattern": "*.zip",
        },
    }
    with pytest.raises(RuntimeError, match="No matching asset"):
        b.fetch(entry)
    monkeypatch.undo()


# ── direct_file backend ──────────────────────────────────────────────────────


def _entry(**src):
    base = {
        "kind": "direct_file",
        "url": "https://server.test/uploads/patch-3.MPQ",
        "dest": "Data/patch-3.MPQ",
    }
    base.update(src)
    return {"id": "p3", "source": base}


def test_direct_file_validate_rejects_http_and_bad_pins():
    b = sources.get("direct_file")
    assert (
        b.validate(
            {"kind": "direct_file", "url": "http://x.test/a", "dest": "a"}
        )
        is None
    )
    assert b.validate(_entry()["source"] | {"sha1": "nope"}) is None
    assert b.validate(_entry()["source"] | {"size": 0}) is None
    ok = b.validate(_entry(sha1="A" * 40, size=5)["source"])
    assert ok["sha1"] == "a" * 40 and ok["size"] == 5


def test_direct_tar_requires_extract_map():
    b = sources.get("direct_tar")
    assert (
        b.validate({"kind": "direct_tar", "url": "https://x.test/a.tar.gz"})
        is None
    )
    # map destinations must be safe relpaths
    assert (
        b.validate(
            {
                "kind": "direct_tar",
                "url": "https://x.test/a.tar.gz",
                "extract_map": {"entry": "../evil"},
            }
        )
        is None
    )


def test_direct_file_streaming_stages_beside_dest_and_verifies(
    tmp_path, monkeypatch
):
    client = tmp_path / "client"
    client.mkdir()
    body = b"MPQDATA!"
    monkeypatch.setattr(
        df_module,
        "secure_urlopen",
        lambda req, **k: _Resp(
            body,
            headers={
                "ETag": '"e1"',
                "Content-Length": str(len(body)),
            },
        ),
    )
    b = sources.get("direct_file")
    result = b.fetch(_entry(size=len(body)), client_dir=str(client))
    staged = result.file
    # Staged beside the final destination (same volume) — not yet installed.
    assert staged.path.endswith("patch-3.MPQ.tmp")
    assert staged.size == len(body)
    assert staged.sha1_hex is not None
    assert staged.probe["etag"] == '"e1"'
    assert staged.probe["size"] == len(body)
    assert not (client / "Data" / "patch-3.MPQ").exists()

    written = deploy.move_into_place(
        staged.path, str(client), "Data/patch-3.MPQ"
    )
    assert written == "Data/patch-3.MPQ"
    assert (client / "Data" / "patch-3.MPQ").read_bytes() == body


def test_direct_file_streaming_size_mismatch(tmp_path, monkeypatch):
    client = tmp_path / "client"
    client.mkdir()
    monkeypatch.setattr(
        df_module, "secure_urlopen", lambda req, **k: _Resp(b"short")
    )
    b = sources.get("direct_file")
    with pytest.raises(RuntimeError, match="expected"):
        b.fetch(_entry(size=999), client_dir=str(client))
    assert list(client.rglob("*.tmp")) == []


def test_direct_file_streaming_sha_mismatch(tmp_path, monkeypatch):
    client = tmp_path / "client"
    client.mkdir()
    monkeypatch.setattr(
        df_module, "secure_urlopen", lambda req, **k: _Resp(b"corrupt!")
    )
    b = sources.get("direct_file")
    with pytest.raises(RuntimeError, match="SHA-1 mismatch"):
        b.fetch(_entry(sha1="a" * 40), client_dir=str(client))


# ── deployers ────────────────────────────────────────────────────────────────


def test_deploy_extract_zip_map_and_traversal_refusal(tmp_path):
    import zipfile

    client = tmp_path / "client"
    client.mkdir()
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Mod/x.dll", b"A")
    # A traversal destination is refused outright (defence in depth — the
    # catalog validator already drops such maps before they get here).
    emap = {"Mod/x.dll": "../../evil.dll"}
    with pytest.raises(RuntimeError, match="unsafe install path"):
        deploy.extract_zip_map(str(client), buf.getvalue(), "t", emap)
    assert not (tmp_path / "evil.dll").exists()
    # A missing zip member is skipped, not fatal.
    written = deploy.extract_zip_map(
        str(client), buf.getvalue(), "t", {"Mod/nope.dll": "ok.dll"}
    )
    assert written == []


def test_deploy_unpack_folder_strips_top_dir_and_replaces(tmp_path):
    import io
    import zipfile

    dest_root = tmp_path / "Interface" / "AddOns" / "pfUI"
    dest_root.mkdir(parents=True)
    (dest_root / "old.txt").write_text("old")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pfUI-master/pfUI.toc", "## Title: pfUI\n")
        zf.writestr("pfUI-master/lib/x.lua", "-- lib")
        zf.writestr("pfUI-master/../escape.txt", "evil")
    deploy.unpack_folder(buf.getvalue(), str(dest_root))
    assert (dest_root / "pfUI.toc").read_text() == "## Title: pfUI\n"
    assert (dest_root / "lib" / "x.lua").exists()
    assert not (dest_root / "old.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
    assert not list(tmp_path.rglob("*.tmp_install"))


# ── hooks ────────────────────────────────────────────────────────────────────


def test_hooks_run_unknown_name_raises(tmp_path):
    with pytest.raises(RuntimeError, match="Unknown post-install hook"):
        hooks.run("format_c_drive", str(tmp_path))


def test_git_archive_validate_and_version_resolution(tmp_path, monkeypatch):
    import nostalgia_launcher.core.config_store as config_store

    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    b = sources.get("git_archive")
    # Shape validation: a git URL is required; host allowlisting happens at
    # resolve/fetch time (addons._custom_validator gates catalog entries).
    assert b.validate({"git": ""}) is None
    cleaned = b.validate({"git": "https://github.com/a/b", "branch": "main"})
    assert cleaned == {
        "kind": "git_archive",
        "git": "https://github.com/a/b",
        "branch": "main",
    }

    # resolve_version consults the addon_sha_cache without network.
    config_store.save_config(
        {
            "addon_sha_cache": {
                "https://github.com/a/b#main": {
                    "timestamp": 9_999_999_999,
                    "sha": "f" * 40,
                }
            }
        }
    )
    assert (
        b.resolve_version(
            {"source": {"git": "https://github.com/a/b", "branch": "main"}}
        )
        == "f" * 40
    )


def test_fetch_result_defaults():
    r = FetchResult()
    assert r.data is None and r.file is None and r.version is None


def test_extract_tar_map_skips_directory_members(tmp_path):
    """A pattern matching a directory entry (e.g. the archive's leading
    folder) has no payload — it must be skipped with a warning, never
    crash on extractfile() → None."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        dir_info = tarfile.TarInfo("pkg")
        dir_info.type = tarfile.DIRTYPE
        tf.addfile(dir_info)
        data = b"hello"
        info = tarfile.TarInfo("pkg/mod.lua")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    written = deploy.extract_tar_map(
        str(tmp_path), buf.getvalue(), {"pkg": "out/lua"}
    )
    # The directory match was skipped; nothing was installed for it.
    assert written == []
