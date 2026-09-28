"""Cover the pfUI "Default" profile patch (idempotent file edits)."""

import nostalgia_launcher.services.pfui as pfui


def test_patch_pfui_installs_profile(tmp_path):
    client = tmp_path / "client"
    base = client / "Interface" / "AddOns" / "pfUI"
    (base / "env").mkdir(parents=True)
    (base / "modules").mkdir(parents=True)
    (base / "env" / "profiles.lua").write_text(
        "pfUI_profiles = {}\n", encoding="utf-8"
    )

    pfui.patch_pfui_default_profile(str(client))
    content = (base / "env" / "profiles.lua").read_text(encoding="utf-8")
    assert pfui._PFUI_MARK_BEGIN in content
    assert 'pfUI_profiles["Default"]' in content

    # Idempotent: re-applying must not duplicate the block.
    pfui.patch_pfui_default_profile(str(client))
    content2 = (base / "env" / "profiles.lua").read_text(encoding="utf-8")
    assert content2.count(pfui._PFUI_MARK_BEGIN) == 1


def test_patch_pfui_missing_profile_returns_gracefully(tmp_path):
    client = tmp_path / "client"
    pfui.patch_pfui_default_profile(str(client))  # no pfUI installed
