"""Updater: exact subprocess invocations for the update-execution paths.

Existing tests pinned the returned UpdateResult but not the literal
subprocess.run call (args/kwargs) or which asset check_latest_release picks
when the payload has duplicate matches — both were open mutmut survivors.
"""

from __future__ import annotations

import io
import json

import refrain.updater as updater


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _capture_run(monkeypatch, result):
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(updater.subprocess, "run", run)
    return calls


def test_apply_pip_subprocess_call_is_pinned(monkeypatch):
    monkeypatch.setattr(updater.sys, "executable", "/srv/music/.venv/bin/python")
    monkeypatch.setattr(updater.sys, "prefix", "/srv/music/.venv")
    monkeypatch.setattr(updater.sys, "base_prefix", "/usr")
    calls = _capture_run(monkeypatch, _Proc(0, "Successfully installed refrain-9.9.9\n"))

    updater._apply_pip()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (
        ["/srv/music/.venv/bin/python", "-m", "pip", "install", "--upgrade", "refrain"],
    )
    assert kwargs == {"capture_output": True, "text": True, "timeout": 300}


def test_apply_pipx_subprocess_call_is_pinned(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda _n: "/usr/bin/pipx")
    calls = _capture_run(monkeypatch, _Proc(0, "upgraded package refrain from 0.2.7 to 0.3.0\n"))

    updater._apply_pipx()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (["/usr/bin/pipx", "upgrade", "refrain"],)
    assert kwargs == {"capture_output": True, "text": True, "timeout": 300}


def test_pacman_ownership_check_is_pinned(monkeypatch):
    for var in ("APPIMAGE", "FLATPAK_ID", "container"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(updater.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(updater.sys, "prefix", "/usr")
    monkeypatch.setattr(updater.sys, "base_prefix", "/usr")
    monkeypatch.setattr(updater, "__file__", "/usr/lib/python3.14/site-packages/refrain/updater.py")
    monkeypatch.setattr(
        updater.site, "getusersitepackages", lambda: "/home/u/.local/lib/python3.14/site-packages"
    )
    monkeypatch.setattr(updater.shutil, "which", lambda _n: "/usr/bin/pacman")
    calls = _capture_run(monkeypatch, _Proc(0, "/usr/bin/refrain is owned by refrain 0.5.3-1"))

    updater.detect_install_type()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (["pacman", "-Qo", "/usr/bin/refrain"],)
    assert kwargs == {"capture_output": True, "text": True, "timeout": 2}


class _JSONBody:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self._buf.read(*a)


def test_apply_pipx_prefers_a_real_upgrade_even_if_output_also_says_latest(monkeypatch):
    """The "already at latest" no-op check must require BOTH markers, not either."""
    monkeypatch.setattr(updater.shutil, "which", lambda _n: "/usr/bin/pipx")
    out = (
        "upgraded package refrain from 0.2.7 to 0.3.0\nrefrain is already at latest version 0.3.0\n"
    )
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **kw: _Proc(0, out))

    result = updater._apply_pipx()

    assert result.success is True
    assert result.needs_restart is True


def test_the_terminal_script_ends_with_a_pause_before_closing(monkeypatch):
    monkeypatch.setattr(
        updater.shutil, "which", lambda n: "/usr/bin/konsole" if n == "konsole" else None
    )
    spawned = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda argv, **_kw: spawned.append(argv))

    updater._run_in_terminal(updater._FLATPAK_UPDATE)

    assert spawned[0][4] == (
        "flatpak update -y io.github.Rockykln.Refrain; echo; read -rp 'Press Enter to close…'"
    )


def test_first_matching_appimage_asset_wins_over_a_duplicate(monkeypatch):
    """A duplicate arch match must not overwrite the first pick with a later asset."""
    base = "https://github.com/Rockykln/refrain/releases/download/v9.9.9/"
    payload = {
        "tag_name": "v9.9.9",
        "assets": [
            {
                "name": "Refrain-9.9.9-x86_64.AppImage",
                "browser_download_url": base + "first",
                "size": 1,
            },
            {
                "name": "refrain-9.9.9-x86_64.appimage",
                "browser_download_url": base + "second",
                "size": 2,
            },
        ],
    }
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: _JSONBody(payload))
    monkeypatch.setattr(updater.platform, "machine", lambda: "x86_64")

    info = updater.check_latest_release()

    assert info.appimage_url == base + "first"
    assert info.appimage_size == 1
