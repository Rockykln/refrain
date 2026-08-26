"""Updater module: version compare, install-type detection, ReleaseInfo parsing.

The HTTP call is mocked at the urllib level so the suite is hermetic.
"""

from __future__ import annotations

import io
import json
import os

import pytest


@pytest.fixture
def updater(monkeypatch):
    import refrain.updater as u

    return u


def test_is_newer_basic(updater):
    assert updater.is_newer("0.2.0", "0.1.0") is True
    assert updater.is_newer("0.1.1", "0.1.0") is True
    assert updater.is_newer("1.0.0", "0.9.9") is True


def test_is_newer_equal_or_older(updater):
    assert updater.is_newer("0.1.0", "0.1.0") is False
    assert updater.is_newer("0.1.0", "0.2.0") is False
    assert updater.is_newer("0.1.0", "1.0.0") is False


def test_is_newer_handles_v_prefix(updater):
    assert updater.is_newer("v0.2.0", "0.1.0") is True
    assert updater.is_newer("0.2.0", "v0.1.0") is True
    assert updater.is_newer("v0.2.0", "v0.1.0") is True


def test_is_newer_handles_pre_release_suffix(updater):
    assert updater.is_newer("0.2.0-rc1", "0.1.0") is True
    assert updater.is_newer("0.2.0+build.5", "0.1.0") is True


def test_is_newer_invalid_versions(updater):
    assert updater.is_newer("nope", "0.1.0") is False
    assert updater.is_newer("0.1.0", "weird") is False
    assert updater.is_newer("", "") is False


def test_detect_install_type_appimage(monkeypatch, updater):
    monkeypatch.setenv("APPIMAGE", "/tmp/Refrain-0.1.0-x86_64.AppImage")
    assert updater.detect_install_type() == "appimage"


def test_detect_install_type_flatpak(monkeypatch, updater):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setenv("FLATPAK_ID", "io.github.Rockykln.Refrain")
    assert updater.detect_install_type() == "flatpak"


def test_detect_install_type_returns_known_value(monkeypatch, updater):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.delenv("container", raising=False)
    # Returns whichever bucket the test interpreter actually lives in:
    #   - pip:    .venv inside the project (local dev)
    #   - dev:    editable install in a checkout with .git
    #   - system: GitHub Actions hosted-toolcache (/opt/hostedtoolcache/…)
    #   - aur:    Arch system Python that owns /usr/bin/refrain
    # Anything from the supported set is fine — we only assert it doesn't
    # land on the unreachable "appimage" / "flatpak" branches.
    install = updater.detect_install_type()
    assert install in ("pip", "dev", "system", "aur")


def test_detect_install_type_pipx(monkeypatch, updater):
    # pipx app venv: own venv (prefix != base_prefix) but NO pip — must
    # be detected as pipx, not pip, or self-update dies with
    # "No module named pip".
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.delenv("container", raising=False)
    monkeypatch.setattr(
        updater.sys,
        "executable",
        "/home/u/.local/share/pipx/venvs/refrain/bin/python",
    )
    monkeypatch.setattr(updater.sys, "prefix", "/home/u/.local/share/pipx/venvs/refrain")
    monkeypatch.setattr(updater.sys, "base_prefix", "/usr")
    assert updater.detect_install_type() == "pipx"


class _Proc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_apply_pipx_success(updater, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda _n: "/usr/bin/pipx")
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *a, **kw: _Proc(0, "upgraded package refrain from 0.2.7 to 0.3.0\n"),
    )
    r = updater._apply_pipx()
    assert r.success is True
    assert r.needs_restart is True


def test_apply_pipx_already_latest_is_not_success(updater, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda _n: "/usr/bin/pipx")
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *a, **kw: _Proc(0, "refrain is already at latest version 0.3.0\n"),
    )
    r = updater._apply_pipx()
    assert r.success is False
    assert "pipx upgrade --force refrain" in r.message


def test_apply_pipx_missing_pipx_binary(updater, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda _n: None)
    r = updater._apply_pipx()
    assert r.success is False
    assert "pipx upgrade refrain" in r.message


def test_apply_update_routes_pipx(updater, monkeypatch):
    info = updater.ReleaseInfo(tag="v0.3.1", version="0.3.1", name="x", body="", html_url="")
    called = {}
    monkeypatch.setattr(
        updater,
        "_apply_pipx",
        lambda: called.setdefault("hit", True) or updater.UpdateResult(True, "ok"),
    )
    updater.apply_update(info, install_type="pipx")
    assert called.get("hit") is True


def _fake_response(payload):
    class _R:
        def __init__(self, data):
            self._buf = io.BytesIO(data)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a, **kw):
            return self._buf.read(*a, **kw)

    return _R(json.dumps(payload).encode("utf-8"))


def test_check_latest_release_parses_basic_payload(monkeypatch, updater):
    payload = {
        "tag_name": "v9.9.9",
        "name": "Refrain v9.9.9",
        "body": "## Changes\n- Fixed thing",
        "html_url": "https://github.com/Rockykln/refrain/releases/tag/v9.9.9",
        "assets": [
            {
                "name": "Refrain-9.9.9-x86_64.AppImage",
                "browser_download_url": "https://github.com/x/y/Refrain.AppImage",
                "size": 12345678,
            },
            {
                "name": "refrain-9.9.9.tar.gz",
                "browser_download_url": "https://github.com/x/y/refrain.tar.gz",
                "size": 100000,
            },
        ],
    }
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: _fake_response(payload))

    info = updater.check_latest_release()
    assert info is not None
    assert info.tag == "v9.9.9"
    assert info.version == "9.9.9"
    assert info.name == "Refrain v9.9.9"
    assert info.appimage_url == "https://github.com/x/y/Refrain.AppImage"
    assert info.appimage_size == 12345678
    assert info.is_newer_than_current is True


def test_check_latest_release_returns_none_on_network_error(monkeypatch, updater):
    def _boom(*a, **kw):
        raise OSError("offline")

    monkeypatch.setattr(updater.urllib.request, "urlopen", _boom)
    assert updater.check_latest_release(timeout_s=0.5) is None


def test_check_latest_release_handles_empty_assets(monkeypatch, updater):
    payload = {
        "tag_name": "v9.9.9",
        "name": "Refrain v9.9.9",
        "body": "",
        "html_url": "",
        "assets": [],
    }
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: _fake_response(payload))

    info = updater.check_latest_release()
    assert info is not None
    assert info.appimage_url is None
    assert info.appimage_size == 0


def test_apply_update_dispatches_per_install_type(updater, monkeypatch):
    info = updater.ReleaseInfo(
        tag="v0.2.0",
        version="0.2.0",
        name="x",
        body="",
        html_url="",
    )
    # Without an APPIMAGE env var, "appimage" path errors out cleanly
    os.environ.pop("APPIMAGE", None)
    # Force the fallback path (no terminal available) so the
    # assertions test the offline-friendly message-box behaviour.
    # The terminal-spawn path is exercised separately below.
    monkeypatch.setattr(updater, "_run_in_terminal", lambda _cmd: False)

    r = updater.apply_update(info, install_type="flatpak")
    assert r.success is False
    assert "flatpak update" in r.message.lower()

    r = updater.apply_update(info, install_type="aur")
    assert r.success is False
    # The hint message names whatever helper detect produced
    # (yay/paru/trizen/pikaur or the bare-pacman fallback) — assert
    # we mention the canonical keyword "syu" which all of them use.
    assert "syu" in r.message.lower()

    r = updater.apply_update(info, install_type="dev")
    assert r.success is False
    assert "git pull" in r.message.lower()


def test_apply_update_aur_launches_terminal_when_available(updater, monkeypatch):
    """AUR install + a usable terminal → spawn it, mark needs_restart."""
    spawned: list[str] = []

    def fake_terminal(cmd: str) -> bool:
        spawned.append(cmd)
        return True

    monkeypatch.setattr(updater, "_run_in_terminal", fake_terminal)

    info = updater.ReleaseInfo(tag="v0.2.0", version="0.2.0", name="x", body="", html_url="")
    r = updater.apply_update(info, install_type="aur")
    assert r.success is True
    assert r.needs_restart is True
    assert spawned and "syu refrain" in spawned[0].lower()


def test_apply_update_flatpak_launches_terminal_when_available(updater, monkeypatch):
    spawned: list[str] = []
    monkeypatch.setattr(updater, "_run_in_terminal", lambda cmd: spawned.append(cmd) or True)

    info = updater.ReleaseInfo(tag="v0.2.0", version="0.2.0", name="x", body="", html_url="")
    r = updater.apply_update(info, install_type="flatpak")
    assert r.success is True
    assert r.needs_restart is True
    assert spawned and "flatpak update" in spawned[0].lower()


def test_aur_helper_falls_back_to_pacman(updater, monkeypatch):
    """When no AUR helper is installed, _aur_helper falls back to a
    bare pacman command. Update dialog will still surface this so
    the user can swap in their preferred helper if they prefer."""
    monkeypatch.setattr(updater.shutil, "which", lambda _name: None)
    cmd = updater._aur_helper()
    assert "pacman" in cmd
    assert "refrain" in cmd


# ---------------------------------------------------------------------------
# cleanup_orphan_downloads — self-heal of *.AppImage.new files left behind
# by a SIGKILL / power-loss mid-download.
# ---------------------------------------------------------------------------


def test_cleanup_orphan_downloads_removes_stale_new(tmp_path, monkeypatch, updater):
    appimage = tmp_path / "Refrain-x86_64.AppImage"
    appimage.write_bytes(b"")
    orphan = tmp_path / "Refrain-x86_64.AppImage.new"
    orphan.write_bytes(b"partial download")
    monkeypatch.setenv("APPIMAGE", str(appimage))

    updater.cleanup_orphan_downloads()

    assert not orphan.exists()
    # The real AppImage must NOT be touched.
    assert appimage.exists()


def test_cleanup_orphan_downloads_no_appimage_env_is_safe(monkeypatch, updater):
    monkeypatch.delenv("APPIMAGE", raising=False)
    # Must not raise.
    updater.cleanup_orphan_downloads()


def test_cleanup_orphan_downloads_no_orphan_is_idempotent(tmp_path, monkeypatch, updater):
    appimage = tmp_path / "Refrain-x86_64.AppImage"
    appimage.write_bytes(b"")
    monkeypatch.setenv("APPIMAGE", str(appimage))

    # Call twice — must succeed both times even when nothing is there.
    updater.cleanup_orphan_downloads()
    updater.cleanup_orphan_downloads()

    assert appimage.exists()
