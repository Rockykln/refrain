"""Updater: install-type edge cases, release parsing, and the failure paths of each update route."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess

import pytest

import refrain.updater as updater

_DL = "https://github.com/Rockykln/refrain/releases/download/v9.9.9/"
_NAME = "Refrain-9.9.9-x86_64.AppImage"
_NEW = b"\x7fELF" + b"12345" * 250_000


class _Body:
    def __init__(self, data):
        self._buf = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self._buf.read(*a)


class _Proc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _system_python(monkeypatch, exe="/usr/bin/python3"):
    for var in ("APPIMAGE", "FLATPAK_ID", "container"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(updater.sys, "executable", exe)
    monkeypatch.setattr(updater.sys, "prefix", "/usr")
    monkeypatch.setattr(updater.sys, "base_prefix", "/usr")
    monkeypatch.setattr(updater, "__file__", "/usr/lib/python3.14/site-packages/refrain/updater.py")
    monkeypatch.setattr(
        updater.site, "getusersitepackages", lambda: "/home/u/.local/lib/python3.14/site-packages"
    )


def test_a_venv_python_resolved_to_the_system_is_still_pip(monkeypatch):
    _system_python(monkeypatch, exe="/srv/music/.venv/bin/python")
    assert updater.detect_install_type() == "pip"


def test_an_unknown_interpreter_location_is_treated_as_pip(monkeypatch):
    _system_python(monkeypatch, exe="/nix/store/12345-python3/bin/python3")
    assert updater.detect_install_type() == "pip"


def test_a_broken_user_site_lookup_does_not_count_as_a_user_install(monkeypatch):
    _system_python(monkeypatch)

    def broken():
        raise OSError("no home directory")

    monkeypatch.setattr(updater.site, "getusersitepackages", broken)
    monkeypatch.setattr(updater.shutil, "which", lambda _n: None)
    assert updater.detect_install_type() == "system"


@pytest.mark.parametrize(
    "answer",
    [
        _Proc(1, "", "error: No package owns /usr/bin/refrain"),
        subprocess.TimeoutExpired("pacman", 2),
    ],
    ids=["not-owned", "timeout"],
)
def test_pacman_not_claiming_refrain_means_system(monkeypatch, answer):
    _system_python(monkeypatch)

    def run(*_a, **_kw):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(updater.shutil, "which", lambda _n: "/usr/bin/pacman")
    monkeypatch.setattr(updater.subprocess, "run", run)
    assert updater.detect_install_type() == "system"


@pytest.mark.parametrize(
    ("remote", "local", "newer"),
    [
        ("0.5.3", "0.5.3.dev0", True),
        ("0.5.3rc1", "0.5.3", False),
        ("0.5.3-beta", "0.5.2", True),
        ("0.5.3+build.12345", "0.5.3", False),
        (" v1.0.0 ", "0.9.9", True),
        ("1.0", "0.9.9", False),
        ("latest", "0.9.9", False),
    ],
)
def test_version_edge_cases(remote, local, newer):
    assert updater.is_newer(remote, local) is newer


def _serve_json(monkeypatch, payload):
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *a, **kw: _Body(json.dumps(payload).encode()),
    )
    monkeypatch.setattr(updater.platform, "machine", lambda: "x86_64")


def test_a_release_without_a_tag_is_ignored(monkeypatch):
    _serve_json(monkeypatch, {"message": "API rate limit exceeded"})
    assert updater.check_latest_release() is None


def test_a_non_https_api_url_is_never_fetched(monkeypatch):
    fetched = []
    monkeypatch.setattr(updater, "RELEASES_API", "http://api.github.com/repos/x/releases")
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: fetched.append(a))
    assert updater.check_latest_release() is None
    assert fetched == []


def test_sparse_release_fields_fall_back_to_the_tag(monkeypatch):
    _serve_json(
        monkeypatch,
        {
            "tag_name": " v9.9.9 ",
            "name": None,
            "body": None,
            "assets": [
                {"name": "Refrain-9.9.9-X86_64.AppImage", "browser_download_url": _DL + "a"},
                {"name": _NAME, "browser_download_url": _DL + "b", "size": 12345},
                {"name": "SHA256SUMS", "browser_download_url": _DL + "SHA256SUMS"},
            ],
        },
    )
    release = updater.check_latest_release()
    assert (release.tag, release.version, release.name, release.body) == (
        "v9.9.9",
        "9.9.9",
        "v9.9.9",
        "",
    )
    assert release.appimage_url == _DL + "a"
    assert release.appimage_size == 0
    assert release.sha256sums_url == _DL + "SHA256SUMS"


def test_arm_machines_look_for_the_aarch64_appimage(monkeypatch):
    monkeypatch.setattr(updater.platform, "machine", lambda: "ARM64")
    assert updater._machine_arch() == "aarch64"


def test_release_notes_get_their_bare_links_wrapped():
    body = "See https://github.com/Rockykln/refrain/compare/v9.9.8...v9.9.9 and <https://x.example>"
    notes = updater.prepare_release_notes(body)
    link = "https://github.com/Rockykln/refrain/compare/v9.9.8...v9.9.9"
    assert f"[{link}]({link})" in notes
    assert "<https://x.example>" in notes
    assert updater.prepare_release_notes(None) == "_No release notes provided._"


@pytest.fixture
def serve(monkeypatch):
    files: dict[str, bytes] = {}

    def _open(url, _timeout):
        if url not in files:
            raise OSError(f"not served: {url}")
        return _Body(files[url])

    monkeypatch.setattr(updater, "_open_download", _open)
    return files


@pytest.fixture
def appimage(tmp_path, monkeypatch):
    target = tmp_path / _NAME
    target.write_bytes(b"old build")
    target.chmod(0o750)
    monkeypatch.setenv("APPIMAGE", str(target))
    return target


def _release(*, sums=None, url=_DL + _NAME, size=len(_NEW)):
    return updater.ReleaseInfo(
        tag="v9.9.9",
        version="9.9.9",
        name="Refrain v9.9.9",
        body="",
        html_url="https://github.com/Rockykln/refrain/releases/tag/v9.9.9",
        appimage_url=url,
        appimage_size=size,
        appimage_name=_NAME,
        sha256sums_url=sums,
    )


def _leftover(target):
    return target.with_name(target.name + ".new")


def test_the_new_appimage_keeps_the_old_file_mode(serve, appimage):
    serve[_DL + _NAME] = _NEW
    assert updater._apply_appimage(_release()).success is True
    assert appimage.stat().st_mode & 0o777 == 0o750
    assert not _leftover(appimage).exists()


def test_a_vanished_appimage_is_replaced_owner_only(serve, appimage):
    serve[_DL + _NAME] = _NEW
    appimage.unlink()
    assert updater._apply_appimage(_release()).success is True
    assert appimage.read_bytes() == _NEW
    assert appimage.stat().st_mode & 0o777 == 0o700


def test_checksums_with_binary_markers_and_upper_case_are_understood(serve, appimage):
    serve[_DL + _NAME] = _NEW
    digest = hashlib.sha256(_NEW).hexdigest().upper()
    serve[_DL + "SHA256SUMS"] = f"{digest} *{_NAME}\n".encode()
    assert updater._apply_appimage(_release(sums=_DL + "SHA256SUMS")).success is True
    assert appimage.read_bytes() == _NEW


def test_checksums_from_elsewhere_are_refused(serve, appimage):
    serve[_DL + _NAME] = _NEW
    result = updater._apply_appimage(_release(sums="https://example.com/SHA256SUMS"))
    assert result.success is False
    assert "not served from the Refrain releases" in result.message
    assert appimage.read_bytes() == b"old build"


def test_cancelling_mid_download_removes_the_partial_file(serve, appimage):
    serve[_DL + _NAME] = _NEW
    polls = []

    def cancelled():
        polls.append(1)
        return len(polls) > 3

    result = updater._apply_appimage(_release(), cancelled=cancelled)
    assert (result.success, result.cancelled) == (False, True)
    assert appimage.read_bytes() == b"old build"
    assert not _leftover(appimage).exists()


def test_cancelling_after_the_download_still_keeps_the_old_build(serve, appimage):
    serve[_DL + _NAME] = _NEW
    chunks = -(-len(_NEW) // (64 * 1024))
    polls = []

    def cancelled():
        polls.append(1)
        return len(polls) > chunks + 1

    result = updater._apply_appimage(_release(), cancelled=cancelled)
    assert result.cancelled is True
    assert appimage.read_bytes() == b"old build"
    assert not _leftover(appimage).exists()


def test_a_failed_replace_leaves_the_running_appimage_alone(serve, appimage, monkeypatch):
    serve[_DL + _NAME] = _NEW

    def refuse(_src, _dst):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(updater.os, "replace", refuse)
    result = updater._apply_appimage(_release())
    assert result.success is False
    assert "read-only file system" in result.message
    assert appimage.read_bytes() == b"old build"
    assert not _leftover(appimage).exists()


def test_without_the_appimage_variable_nothing_is_downloaded(serve, monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    result = updater.apply_update(_release(), install_type="appimage")
    assert result.success is False
    assert "APPIMAGE" in result.message


def test_a_release_without_an_appimage_for_this_machine_says_so(serve, appimage, monkeypatch):
    monkeypatch.setattr(updater.platform, "machine", lambda: "riscv64")
    result = updater._apply_appimage(_release(url=None))
    assert result.success is False
    assert "riscv64" in result.message


def test_a_leftover_that_cannot_be_removed_is_left_quietly(appimage, monkeypatch):
    leftover = _leftover(appimage)
    leftover.write_bytes(b"partial")

    def refuse(self, missing_ok=False):
        raise PermissionError("busy")

    monkeypatch.setattr(updater.Path, "unlink", refuse)
    updater.cleanup_orphan_downloads()
    assert leftover.exists()


def test_the_update_type_is_detected_when_not_given(monkeypatch):
    monkeypatch.setattr(updater, "detect_install_type", lambda: "dev")
    result = updater.apply_update(_release())
    assert result.success is False
    assert "git pull" in result.message


def test_an_unknown_install_type_is_left_to_the_package_manager():
    result = updater.apply_update(_release(), install_type="something-else")
    assert result.success is False
    assert "package manager" in result.message


def test_the_terminal_falls_back_to_the_next_emulator(monkeypatch):
    monkeypatch.setattr(
        updater.shutil, "which", lambda n: f"/usr/bin/{n}" if n in ("konsole", "kitty") else None
    )
    spawned = []

    def popen(argv, **_kw):
        if argv[0] == "konsole":
            raise OSError("no display")
        spawned.append(argv)

    monkeypatch.setattr(updater.subprocess, "Popen", popen)
    assert updater._run_in_terminal("flatpak update -y io.github.Rockykln.Refrain") is True
    assert spawned[0][:3] == ["kitty", "bash", "-c"]
    assert spawned[0][3].startswith("flatpak update -y io.github.Rockykln.Refrain; echo; read")


def test_without_any_terminal_the_caller_is_told(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda _n: None)
    assert updater._run_in_terminal("yay -Syu refrain") is False


def test_pipx_that_cannot_start_is_reported(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda _n: "/usr/bin/pipx")

    def run(*_a, **_kw):
        raise subprocess.TimeoutExpired("pipx", 300)

    monkeypatch.setattr(updater.subprocess, "run", run)
    result = updater._apply_pipx()
    assert result.success is False
    assert result.message.startswith("pipx invocation failed")


def test_a_failing_pipx_shows_its_exit_code_and_output(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda _n: "/usr/bin/pipx")
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *a, **kw: _Proc(1, None, "Package refrain is not installed\n"),
    )
    result = updater._apply_pipx()
    assert result.success is False
    assert "code 1" in result.message
    assert "Package refrain is not installed" in result.message


@pytest.fixture
def venv_pip(monkeypatch):
    monkeypatch.setattr(updater.sys, "executable", "/srv/music/.venv/bin/python")
    monkeypatch.setattr(updater.sys, "prefix", "/srv/music/.venv")
    monkeypatch.setattr(updater.sys, "base_prefix", "/usr")
    ran = []

    def answer(proc):
        def run(cmd, **_kw):
            ran.append(cmd)
            if isinstance(proc, Exception):
                raise proc
            return proc

        monkeypatch.setattr(updater.subprocess, "run", run)
        return ran

    return answer


def test_pip_in_a_venv_upgrades_without_user(venv_pip):
    ran = venv_pip(_Proc(0, "Successfully installed refrain-9.9.9\n"))
    result = updater._apply_pip()
    assert result.success is True
    assert ran[0] == ["/srv/music/.venv/bin/python", "-m", "pip", "install", "--upgrade", "refrain"]


def test_pip_that_cannot_start_is_reported(venv_pip):
    venv_pip(FileNotFoundError("python vanished"))
    result = updater._apply_pip()
    assert result.success is False
    assert "python vanished" in result.message


def test_a_failing_pip_shows_its_error(venv_pip):
    venv_pip(_Proc(1, "", "ERROR: Could not find a version that satisfies refrain\n"))
    result = updater._apply_pip()
    assert result.success is False
    assert "code 1" in result.message
    assert "Could not find a version" in result.message


def test_pip_already_satisfied_is_not_an_update(venv_pip):
    venv_pip(_Proc(0, "Requirement already satisfied: refrain in ./lib (9.9.8)\n"))
    result = updater._apply_pip()
    assert (result.success, result.needs_restart) == (False, False)
    assert "--force-reinstall" in result.message


@pytest.mark.parametrize(
    ("stdout", "shown"),
    [("Looking in indexes: https://pypi.org/simple\n\n", "Looking in indexes"), ("", "<empty>")],
)
def test_pip_without_a_known_marker_still_asks_for_a_restart(venv_pip, stdout, shown):
    venv_pip(_Proc(0, stdout))
    result = updater._apply_pip()
    assert (result.success, result.needs_restart) == (True, True)
    assert shown in result.message


def test_downloads_identify_refrain_and_pass_the_timeout(monkeypatch):
    opened = []
    monkeypatch.setattr(
        updater._download_opener, "open", lambda req, timeout: opened.append((req, timeout))
    )
    updater._open_download(_DL + _NAME, 60)
    req, timeout = opened[0]
    assert (req.full_url, timeout) == (_DL + _NAME, 60)
    assert req.get_header("User-agent").startswith("Refrain/")
