"""Updater module: version compare, install-type detection, ReleaseInfo parsing.

The HTTP call is mocked at the urllib level so the suite is hermetic.
"""

from __future__ import annotations

import io
import json
import os

import pytest

from tests.release_signing import PUBLIC_KEY_HEX, SEED, release_key, signature_for


@pytest.fixture
def updater(monkeypatch):
    import refrain.updater as u

    monkeypatch.setattr(u, "RELEASE_PUBLIC_KEY", PUBLIC_KEY_HEX)
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


def test_a_development_build_hears_about_releases(updater):
    """A development build still hears about releases."""
    assert updater.is_newer("0.5.3", "0.5.3.dev0") is True
    assert updater.is_newer("v9.9.9", "0.5.3.dev0") is True
    assert updater.is_newer("0.5.2", "0.5.3.dev0") is False
    assert updater.is_newer("0.5.3", "0.5.3-rc1") is True
    assert updater.is_newer("0.5.3-rc1", "0.5.3") is False


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


def _system_python(monkeypatch, updater, module_path):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.delenv("container", raising=False)
    monkeypatch.setattr(updater.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(updater.sys, "prefix", "/usr")
    monkeypatch.setattr(updater.sys, "base_prefix", "/usr")
    monkeypatch.setattr(updater, "__file__", module_path)
    monkeypatch.setattr(
        updater.site, "getusersitepackages", lambda: "/home/u/.local/lib/python3.14/site-packages"
    )


def test_a_pip_user_install_is_pip_not_system(monkeypatch, updater):
    _system_python(
        monkeypatch,
        updater,
        "/home/u/.local/lib/python3.14/site-packages/refrain/updater.py",
    )
    assert updater.detect_install_type() == "pip"


def test_a_distro_install_asks_pacman(monkeypatch, updater):
    _system_python(monkeypatch, updater, "/usr/lib/python3.14/site-packages/refrain/updater.py")
    monkeypatch.setattr(updater.shutil, "which", lambda _name: "/usr/bin/pacman")
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *_a, **_k: _Proc(0, "/usr/bin/refrain is owned by refrain 0.5.2-1"),
    )
    assert updater.detect_install_type() == "aur"
    monkeypatch.setattr(updater.shutil, "which", lambda _name: None)
    assert updater.detect_install_type() == "system"


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
    # Installed into that venv, not run from this checkout.
    monkeypatch.setattr(
        updater,
        "__file__",
        "/home/u/.local/share/pipx/venvs/refrain/lib/python3.14/site-packages/refrain/updater.py",
    )
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
    spawned: list[tuple[str, ...]] = []

    def fake_terminal(cmd: tuple[str, ...]) -> bool:
        spawned.append(cmd)
        return True

    monkeypatch.setattr(updater, "_run_in_terminal", fake_terminal)

    info = updater.ReleaseInfo(tag="v0.2.0", version="0.2.0", name="x", body="", html_url="")
    r = updater.apply_update(info, install_type="aur")
    assert r.success is True
    assert r.needs_restart is True
    assert spawned and spawned[0][-2:] == ("-Syu", "refrain")


def test_apply_update_flatpak_launches_terminal_when_available(updater, monkeypatch):
    spawned: list[tuple[str, ...]] = []
    monkeypatch.setattr(updater, "_run_in_terminal", lambda cmd: spawned.append(cmd) or True)

    info = updater.ReleaseInfo(tag="v0.2.0", version="0.2.0", name="x", body="", html_url="")
    r = updater.apply_update(info, install_type="flatpak")
    assert r.success is True
    assert r.needs_restart is True
    assert spawned == [("flatpak", "update", "-y", "io.github.Rockykln.Refrain")]
    assert "flatpak update -y io.github.Rockykln.Refrain" in r.message


def test_aur_helper_falls_back_to_pacman(updater, monkeypatch):
    """When no AUR helper is installed, _aur_helper falls back to a
    bare pacman command. Update dialog will still surface this so
    the user can swap in their preferred helper if they prefer."""
    monkeypatch.setattr(updater.shutil, "which", lambda _name: None)
    assert updater._aur_helper() == ("sudo", "pacman", "-Syu", "refrain")


def test_aur_helper_prefers_an_installed_helper(updater, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda name: name == "paru")
    assert updater._aur_helper() == ("paru", "-Syu", "refrain")


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


def test_cleanup_orphan_downloads_without_an_appimage_deletes_nothing(
    tmp_path, monkeypatch, updater
):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.chdir(tmp_path)
    download = tmp_path / "Refrain-x86_64.AppImage.new"
    download.write_bytes(b"partial download")
    updater.cleanup_orphan_downloads()
    assert download.exists()


def test_cleanup_orphan_downloads_no_orphan_is_idempotent(tmp_path, monkeypatch, updater):
    appimage = tmp_path / "Refrain-x86_64.AppImage"
    appimage.write_bytes(b"")
    monkeypatch.setenv("APPIMAGE", str(appimage))

    # Call twice — must succeed both times even when nothing is there.
    updater.cleanup_orphan_downloads()
    updater.cleanup_orphan_downloads()

    assert appimage.exists()


def test_a_source_checkout_in_a_venv_is_never_updated_by_pip(updater, tmp_path, monkeypatch):
    """pip must never upgrade over an editable checkout."""
    checkout = tmp_path / "refrain"
    (checkout / "src" / "refrain").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("")
    (checkout / ".git").mkdir()
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(updater, "__file__", str(checkout / "src" / "refrain" / "updater.py"))
    assert updater.detect_install_type() == "dev"


def test_a_pip_install_in_a_venv_is_still_pip(updater, tmp_path, monkeypatch):
    site = tmp_path / "venv" / "lib" / "python3.14" / "site-packages" / "refrain"
    site.mkdir(parents=True)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(updater, "__file__", str(site / "updater.py"))
    monkeypatch.setattr(updater.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(updater.sys, "base_prefix", "/usr")
    assert updater.detect_install_type() == "pip"


# --------------------------------------------------------------------------- #
# apply_update: which way each install type is updated                         #
# --------------------------------------------------------------------------- #


def _release(updater):
    return updater.ReleaseInfo(
        tag="v9.9.9", version="9.9.9", name="Refrain v9.9.9", body="", html_url="https://x"
    )


def test_a_checkout_is_never_touched(updater, monkeypatch):
    monkeypatch.setattr(updater, "_apply_pip", lambda: pytest.fail("pip ran over a checkout"))
    result = updater.apply_update(_release(updater), install_type="dev")
    assert result.success is False
    assert "git pull" in result.message


@pytest.mark.parametrize("kind,helper", [("pip", "_apply_pip"), ("pipx", "_apply_pipx")])
def test_pip_and_pipx_go_their_own_way(updater, monkeypatch, kind, helper):
    called = []
    monkeypatch.setattr(
        updater, helper, lambda: called.append(kind) or updater.UpdateResult(True, "ok")
    )
    updater.apply_update(_release(updater), install_type=kind)
    assert called == [kind]


@pytest.mark.parametrize("kind", ["aur", "flatpak"])
def test_system_packages_are_left_to_the_package_manager(updater, monkeypatch, kind):
    """The package manager runs in a terminal, where the user confirms sudo."""
    commands = []
    monkeypatch.setattr(updater, "_run_in_terminal", lambda cmd: commands.append(cmd) or True)
    monkeypatch.setattr(updater, "_aur_helper", lambda: ("yay", "-Syu", "refrain"))
    result = updater.apply_update(_release(updater), install_type=kind)
    assert result.success is True and result.needs_restart is True
    assert len(commands) == 1 and "sudo" not in commands[0]
    assert commands[0] in updater._TERMINAL_COMMANDS


@pytest.mark.parametrize("kind", ["aur", "flatpak"])
def test_without_a_terminal_the_command_is_shown(updater, monkeypatch, kind):
    monkeypatch.setattr(updater, "_run_in_terminal", lambda cmd: False)
    monkeypatch.setattr(updater, "_aur_helper", lambda: ("yay", "-Syu", "refrain"))
    result = updater.apply_update(_release(updater), install_type=kind)
    assert result.success is False
    assert "refrain" in result.message.lower()


def test_the_appimage_for_this_machine_is_picked(monkeypatch, updater):
    base = "https://github.com/Rockykln/refrain/releases/download/v9.9.9/"
    payload = {
        "tag_name": "v9.9.9",
        "assets": [
            {
                "name": "Refrain-9.9.9-x86_64.AppImage",
                "browser_download_url": base + "Refrain-9.9.9-x86_64.AppImage",
                "size": 12345678,
            },
            {
                "name": "Refrain-9.9.9-aarch64.AppImage",
                "browser_download_url": base + "Refrain-9.9.9-aarch64.AppImage",
                "size": 12345679,
            },
            {"name": "SHA256SUMS", "browser_download_url": base + "SHA256SUMS", "size": 99},
            {"name": "SHA256SUMS.sig", "browser_download_url": base + "SHA256SUMS.sig"},
        ],
    }
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: _fake_response(payload))
    monkeypatch.setattr(updater.platform, "machine", lambda: "arm64")

    info = updater.check_latest_release()
    assert info.appimage_name == "Refrain-9.9.9-aarch64.AppImage"
    assert info.appimage_url == base + "Refrain-9.9.9-aarch64.AppImage"
    assert info.appimage_size == 12345679
    assert info.sha256sums_url == base + "SHA256SUMS"
    assert info.sha256sums_sig_url == base + "SHA256SUMS.sig"


def test_no_appimage_for_an_unbuilt_arch(monkeypatch, updater):
    payload = {
        "tag_name": "v9.9.9",
        "assets": [{"name": "Refrain-9.9.9-x86_64.AppImage", "browser_download_url": "x"}],
    }
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: _fake_response(payload))
    monkeypatch.setattr(updater.platform, "machine", lambda: "riscv64")
    assert updater.check_latest_release().appimage_url is None


_DL = "https://github.com/Rockykln/refrain/releases/download/v9.9.9/"
_NAME = "Refrain-9.9.9-x86_64.AppImage"
_NEW_APPIMAGE = b"\x7fELF" + b"12345" * 250_000


class _Body:
    def __init__(self, data):
        self._buf = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self._buf.read(*a)


@pytest.fixture
def serve(monkeypatch, updater):
    """Serve fixed bytes per URL through every way the updater could download."""
    files: dict[str, bytes] = {}
    fetched: list[str] = []

    def _open(url, *_a, **_kw):
        url = getattr(url, "full_url", url)
        fetched.append(url)
        if url not in files:
            raise OSError(f"not served: {url}")
        return _Body(files[url])

    monkeypatch.setattr(updater, "_open_download", _open, raising=False)
    monkeypatch.setattr(updater.urllib.request, "urlopen", _open)
    files["fetched"] = fetched
    return files


@pytest.fixture
def running_appimage(tmp_path, monkeypatch):
    target = tmp_path / _NAME
    target.write_bytes(b"old build")
    target.chmod(0o755)
    monkeypatch.setenv("APPIMAGE", str(target))
    return target


_SUMS = _DL + "SHA256SUMS"
_SIG = _DL + "SHA256SUMS.sig"


def _appimage_release(updater, *, url=_DL + _NAME, size=None, sums=_SUMS, sig=_SIG, name=_NAME):
    return updater.ReleaseInfo(
        tag="v9.9.9",
        version="9.9.9",
        name="Refrain v9.9.9",
        body="",
        html_url="https://github.com/Rockykln/refrain/releases/tag/v9.9.9",
        appimage_url=url,
        appimage_size=len(_NEW_APPIMAGE) if size is None else size,
        appimage_name=name,
        sha256sums_url=sums,
        sha256sums_sig_url=sig,
    )


def _sums_line(data=_NEW_APPIMAGE, name=_NAME):
    import hashlib

    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


def _publish(serve, sums, signature=None):
    serve[_SUMS] = sums
    serve[_SIG] = signature_for(sums) if signature is None else signature


def _refused_unverified(updater, result, serve, running_appimage):
    assert result.success is False
    assert updater.RELEASES_PAGE in result.message
    assert "could not verify" in result.message
    assert _DL + _NAME not in serve["fetched"]
    assert running_appimage.read_bytes() == b"old build"
    assert not running_appimage.with_name(_NAME + ".new").exists()


def test_appimage_update_with_matching_checksum(updater, serve, running_appimage):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, _sums_line(b"other", "refrain.tar.gz") + _sums_line())
    result = updater._apply_appimage(_appimage_release(updater))
    assert result.success is True
    assert running_appimage.read_bytes() == _NEW_APPIMAGE


def test_appimage_with_a_wrong_checksum_is_refused(updater, serve, running_appimage):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, _sums_line(b"tampered"))
    result = updater._apply_appimage(_appimage_release(updater))
    assert result.success is False
    assert "checksum" in result.message
    assert running_appimage.read_bytes() == b"old build"
    assert not running_appimage.with_name(_NAME + ".new").exists()


def test_appimage_missing_from_the_checksums_is_refused(updater, serve, running_appimage):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, _sums_line(_NEW_APPIMAGE, "Refrain-9.9.9-aarch64.AppImage"))
    result = updater._apply_appimage(_appimage_release(updater))
    _refused_unverified(updater, result, serve, running_appimage)
    assert "no entry" in result.message


def test_a_release_without_checksums_is_refused(updater, serve, running_appimage):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    result = updater._apply_appimage(_appimage_release(updater, sums=None, sig=None))
    _refused_unverified(updater, result, serve, running_appimage)
    assert "no SHA256SUMS" in result.message


def test_a_release_without_a_signature_is_refused(updater, serve, running_appimage):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    serve[_SUMS] = _sums_line()
    result = updater._apply_appimage(_appimage_release(updater, sig=None))
    _refused_unverified(updater, result, serve, running_appimage)
    assert "no SHA256SUMS.sig" in result.message


@pytest.mark.parametrize(
    "signature",
    [
        signature_for(b"another file"),
        signature_for(_sums_line(), seed=bytes(32)),
        b"not base64 at all!\n",
        b"",
        b"QUJD\n",
    ],
    ids=["other-file", "other-key", "garbage", "empty", "too-short"],
)
def test_a_bad_signature_is_refused(updater, serve, running_appimage, signature):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, _sums_line(), signature)
    result = updater._apply_appimage(_appimage_release(updater))
    _refused_unverified(updater, result, serve, running_appimage)
    assert "signature" in result.message


def test_checksums_changed_after_signing_are_refused(updater, serve, running_appimage):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, _sums_line(b"evil"), signature_for(_sums_line()))
    result = updater._apply_appimage(_appimage_release(updater))
    _refused_unverified(updater, result, serve, running_appimage)


@pytest.mark.parametrize("key", ["", "not hex", "ab" * 31])
def test_without_a_release_key_nothing_is_fetched(
    updater, serve, running_appimage, monkeypatch, key
):
    monkeypatch.setattr(updater, "RELEASE_PUBLIC_KEY", key)
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, _sums_line())
    result = updater._apply_appimage(_appimage_release(updater))
    _refused_unverified(updater, result, serve, running_appimage)
    assert serve["fetched"] == []


def test_the_shipped_key_is_the_one_releases_are_signed_with():
    """Swapping this key silently would let a foreign release pass the check."""
    import refrain.updater as u

    assert u.RELEASE_PUBLIC_KEY == (
        "b4ffe3f4c0e79c94d91b3c13ddc5d0b0e26159ab66a1f0a78ae35168ad2a516c"
    )
    assert len(bytes.fromhex(u.RELEASE_PUBLIC_KEY)) == 32


def test_an_older_signed_release_cannot_pose_as_a_newer_one(updater, serve, running_appimage):
    old = "Refrain-9.9.8-x86_64.AppImage"
    serve[_DL + old] = _NEW_APPIMAGE
    _publish(serve, _sums_line(_NEW_APPIMAGE, old))
    result = updater._apply_appimage(_appimage_release(updater, url=_DL + old, name=old))
    _refused_unverified(updater, result, serve, running_appimage)
    assert _DL + old not in serve["fetched"]


@pytest.mark.parametrize(("which", "limit"), [(_SUMS, 64 * 1024), (_SIG, 1024)])
def test_oversized_release_files_are_refused(updater, serve, running_appimage, which, limit):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, _sums_line())
    serve[which] = serve[which] + b"\n" * limit
    result = updater._apply_appimage(_appimage_release(updater))
    _refused_unverified(updater, result, serve, running_appimage)
    assert "larger than" in result.message


def test_a_signature_from_elsewhere_is_refused(updater, serve, running_appimage):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, _sums_line())
    result = updater._apply_appimage(
        _appimage_release(updater, sig="https://example.com/SHA256SUMS.sig")
    )
    _refused_unverified(updater, result, serve, running_appimage)
    assert "not served from the Refrain releases" in result.message


def test_a_signature_made_by_the_release_tool_is_accepted(
    updater, serve, running_appimage, tmp_path
):
    sums = tmp_path / "SHA256SUMS"
    sums.write_bytes(_sums_line())
    key = tmp_path / "ed25519.key"
    key.write_text(SEED.hex() + "\n")
    key.chmod(0o600)
    assert release_key.main(["--key", str(key), "sign", str(sums)]) == 0
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, sums.read_bytes(), (tmp_path / "SHA256SUMS.sig").read_bytes())
    assert updater._apply_appimage(_appimage_release(updater)).success is True


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/someone-else/refrain/releases/download/v9.9.9/" + _NAME,
        "http://github.com/Rockykln/refrain/releases/download/v9.9.9/" + _NAME,
        "https://example.com/Rockykln/refrain/releases/download/v9.9.9/" + _NAME,
    ],
)
def test_appimage_from_elsewhere_is_refused(updater, serve, running_appimage, url):
    serve[url] = _NEW_APPIMAGE
    result = updater._apply_appimage(_appimage_release(updater, url=url))
    assert result.success is False
    assert serve["fetched"] == []
    assert running_appimage.read_bytes() == b"old build"


@pytest.mark.parametrize("data", [b"", b"12345"])
def test_an_empty_or_tiny_appimage_is_refused(updater, serve, running_appimage, data):
    serve[_DL + _NAME] = data
    result = updater._apply_appimage(_appimage_release(updater, size=len(data)))
    assert result.success is False
    assert running_appimage.read_bytes() == b"old build"


@pytest.mark.parametrize("reported", [len(_NEW_APPIMAGE) - 1, len(_NEW_APPIMAGE) + 1])
def test_a_size_other_than_reported_is_refused(updater, serve, running_appimage, reported):
    serve[_DL + _NAME] = _NEW_APPIMAGE
    _publish(serve, _sums_line())
    result = updater._apply_appimage(_appimage_release(updater, size=reported))
    assert result.success is False
    assert running_appimage.read_bytes() == b"old build"


def test_redirects_to_plain_http_are_refused(updater):
    import urllib.error
    import urllib.request

    handler = updater._HttpsOnlyRedirects()
    req = urllib.request.Request(_DL + _NAME)
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://example.com/12345")
    followed = handler.redirect_request(
        req, None, 302, "Found", {}, "https://objects.example.com/12345"
    )
    assert followed.full_url == "https://objects.example.com/12345"


def test_a_pip_user_install_updates_with_user(monkeypatch, updater):
    _system_python(
        monkeypatch,
        updater,
        "/home/u/.local/lib/python3.14/site-packages/refrain/updater.py",
    )
    ran = []

    def run(cmd, **_kwargs):
        ran.append(cmd)
        return _Proc(0, "Successfully installed refrain-0.5.3")

    monkeypatch.setattr(updater.subprocess, "run", run)
    updater._apply_pip()
    assert ran[0][:5] == ["/usr/bin/python3", "-m", "pip", "install", "--user"]
