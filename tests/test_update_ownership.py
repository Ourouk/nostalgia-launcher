"""Ownership registry: server-base vs community-overlay collisions."""

from nostalgia_launcher.services.update import ownership


def test_collisions_server_wins():
    import sys

    server = {"data/patch-a.mpq", "wow.exe"}
    user = {"data/patch-a.mpq", "interface/addons/foo/foo.lua"}
    if sys.platform in ("win32", "darwin"):
        assert ownership.collisions(server, user) == ["data/patch-a.mpq"]
    else:
        # Linux compares exactly: mixed-case server paths are distinct.
        assert (
            ownership.collisions({"Data/patch-a.mpq"}, {"data/patch-a.mpq"})
            == []
        )
        assert ownership.collisions(server, user) == ["data/patch-a.mpq"]


def test_check_server_write_collision_warns_and_proceeds():
    import sys

    logged = []
    if sys.platform in ("win32", "darwin"):
        norm = "data/patch-a.mpq"
    else:
        norm = "Data/patch-A.MPQ"
    ok = ownership.check_server_write(
        "Data/patch-A.MPQ",
        {norm},
        {norm},
        log=lambda *a: logged.append(a),
    )
    assert ok is True
    assert logged, "collision must warn"


def test_check_server_write_refuses_user_area():
    logged = []
    ok = ownership.check_server_write(
        "Interface/AddOns/Foo/foo.lua",
        set(),
        set(),
        log=lambda *a: logged.append(a),
    )
    assert ok is False
    assert logged, "refusal must log"


def test_check_server_write_plain_server_path_silent():
    import sys

    logged = []
    norm = (
        "data/patch-b.mpq"
        if sys.platform in ("win32", "darwin")
        else "Data/patch-B.MPQ"
    )
    ok = ownership.check_server_write(
        "Data/patch-B.MPQ",
        {norm},
        set(),
        log=lambda *a: logged.append(a),
    )
    assert ok is True
    assert not logged


def test_is_user_managed_addons_and_dlls():
    assert ownership.is_user_managed("Interface/AddOns/X/y.lua", set())
    assert ownership.is_user_managed("dlls.txt", set())
    assert not ownership.is_user_managed("Data/patch-A.MPQ", set())
    assert ownership.is_user_managed("custom.dll", {"custom.dll"})


def test_server_paths_normalize_and_reject_unsafe():
    import sys

    got = ownership.server_paths_from_list(
        ["Data/Patch-A.MPQ", "../evil", "", "Data\\patch-b.mpq"]
    )
    if sys.platform in ("win32", "darwin"):
        assert got == {"data/patch-a.mpq", "data/patch-b.mpq"}
    else:
        assert got == {"Data/Patch-A.MPQ", "Data/patch-b.mpq"}


def test_user_paths_from_mixed_records():
    records = [
        {"installed_files": ["custom.dll", "../evil"]},
        {"installed_files": "not-a-list"},
        "not-a-record",
    ]
    assert ownership.user_paths_from_records(records) == {"custom.dll"}
