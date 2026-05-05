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
        "tag_name": "v0.2.0",
        "name": "Refrain v0.2.0",
        "body": "## Changes\n- Fixed thing",
        "html_url": "https://github.com/Rockykln/refrain/releases/tag/v0.2.0",
        "assets": [
            {
                "name": "Refrain-0.2.0-x86_64.AppImage",
                "browser_download_url": "https://github.com/x/y/Refrain.AppImage",
                "size": 12345678,
            },
            {
                "name": "refrain-0.2.0.tar.gz",
                "browser_download_url": "https://github.com/x/y/refrain.tar.gz",
                "size": 100000,
            },
        ],
    }
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: _fake_response(payload))

    info = updater.check_latest_release()
    assert info is not None
    assert info.tag == "v0.2.0"
    assert info.version == "0.2.0"
    assert info.name == "Refrain v0.2.0"
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
        "tag_name": "v0.2.0",
        "name": "Refrain v0.2.0",
        "body": "",
        "html_url": "",
        "assets": [],
    }
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: _fake_response(payload))

    info = updater.check_latest_release()
    assert info is not None
    assert info.appimage_url is None
    assert info.appimage_size == 0


def test_apply_update_dispatches_per_install_type(updater):
    info = updater.ReleaseInfo(
        tag="v0.2.0",
        version="0.2.0",
        name="x",
        body="",
        html_url="",
    )
    # Without an APPIMAGE env var, "appimage" path errors out cleanly
    os.environ.pop("APPIMAGE", None)
    r = updater.apply_update(info, install_type="flatpak")
    assert r.success is False
    assert "flatpak update" in r.message.lower()

    r = updater.apply_update(info, install_type="aur")
    assert r.success is False
    assert "yay" in r.message.lower()

    r = updater.apply_update(info, install_type="dev")
    assert r.success is False
    assert "git pull" in r.message.lower()
