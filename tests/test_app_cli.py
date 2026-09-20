"""Command line: argument parsing, the one-shot commands and what they print."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from refrain import __version__, app, uninstall  # noqa: E402
from refrain.uninstall import UninstallReport  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    apps = tmp_path / "applications"
    icons = tmp_path / "icons"
    monkeypatch.setattr(app, "_user_apps_dir", lambda: apps)
    monkeypatch.setattr(app, "_user_icons_dir", lambda: icons)
    monkeypatch.setattr(app, "resolve_exec_line", lambda: "/opt/refrain/refrain")
    return {"desktop": apps / "refrain.desktop", "icon": icons / "refrain.svg"}


def test_defaults_are_all_off():
    args = app._parse_args([])
    assert not (args.silent or args.debug or args.uninstall or args.yes)
    assert not (args.install_desktop or args.uninstall_desktop)


def test_flags_are_parsed():
    args = app._parse_args(["--silent", "--debug", "--uninstall", "-y"])
    assert args.silent and args.debug and args.uninstall and args.yes


def test_version_prints_and_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        app._parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"refrain {__version__}"


def test_unknown_flag_is_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        app._parse_args(["--nope"])
    assert exc.value.code == 2
    assert "--nope" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "target"),
    [
        ("--install-desktop", "install_desktop_files"),
        ("--uninstall-desktop", "uninstall_desktop_files"),
    ],
)
def test_desktop_flags_run_their_command_and_stop(flag, target, monkeypatch):
    calls = []
    monkeypatch.setattr(app, target, lambda: calls.append(target) or 7)
    monkeypatch.setattr(app, "setup_logging", lambda *_a: pytest.fail("GUI path reached"))
    monkeypatch.setattr(sys, "argv", ["refrain", flag])
    assert app.main() == 7
    assert calls == [target]


@pytest.mark.parametrize(("extra", "assume_yes"), [([], False), (["--yes"], True)])
def test_uninstall_flag_passes_on_the_confirmation_choice(extra, assume_yes, monkeypatch):
    seen = []
    monkeypatch.setattr(app, "run_uninstall_cli", lambda assume_yes: seen.append(assume_yes) or 0)
    monkeypatch.setattr(app, "setup_logging", lambda *_a: pytest.fail("GUI path reached"))
    monkeypatch.setattr(sys, "argv", ["refrain", "--uninstall", *extra])
    assert app.main() == 0
    assert seen == [assume_yes]


def test_install_desktop_writes_entry_and_icon(home, capsys):
    assert app.install_desktop_files() == 0
    text = home["desktop"].read_text(encoding="utf-8")
    exec_lines = [line for line in text.splitlines() if line.startswith("Exec=")]
    assert exec_lines == ["Exec=/opt/refrain/refrain"]
    assert home["icon"].read_bytes() == (app.assets_dir() / "icons" / "refrain.svg").read_bytes()
    out = capsys.readouterr().out
    assert str(home["desktop"]) in out
    assert "--uninstall-desktop" in out


def test_uninstall_desktop_removes_what_install_wrote(home, capsys):
    app.install_desktop_files()
    capsys.readouterr()
    assert app.uninstall_desktop_files() == 0
    assert not home["desktop"].exists()
    assert not home["icon"].exists()
    assert "Removed:" in capsys.readouterr().out


def test_uninstall_desktop_with_nothing_installed_says_so(home, capsys):
    assert app.uninstall_desktop_files() == 0
    assert "Nothing to remove" in capsys.readouterr().out


@pytest.fixture
def uninstall_env(monkeypatch):
    monkeypatch.setattr(uninstall, "collect_paths", lambda: [])
    monkeypatch.setattr("refrain.updater.detect_install_type", lambda: "pip")
    monkeypatch.delenv("APPIMAGE", raising=False)
    purged = []

    def _purge(report=UninstallReport()):
        purged.append(True)
        return report

    monkeypatch.setattr(uninstall, "purge", _purge)
    return purged


def test_uninstall_cli_aborts_when_input_is_closed(uninstall_env, monkeypatch, capsys):
    def _eof(*_a):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert app.run_uninstall_cli() == 1
    assert uninstall_env == []
    out = capsys.readouterr().out
    assert "(no data files found)" in out
    assert "Aborted." in out


def test_uninstall_cli_accepts_yes_typed_in(uninstall_env, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_a: " YES ")
    assert app.run_uninstall_cli() == 0
    assert uninstall_env == [True]
    assert "Nothing to remove" in capsys.readouterr().out


def test_uninstall_cli_reports_secrets_and_failures(uninstall_env, monkeypatch, capsys):
    report = UninstallReport(removed=["/tmp/a"], failed=["/tmp/b"], keyring_cleared=True)
    monkeypatch.setattr(uninstall, "purge", lambda: report)
    assert app.run_uninstall_cli(assume_yes=True) == 0
    out = capsys.readouterr().out
    assert "Last.fm credentials cleared" in out
    assert "Could not remove" in out
    assert "  /tmp/b" in out
    assert "Nothing to remove" not in out
    assert uninstall.removal_command("pip") in out


def _fake_root(tmp_path, monkeypatch):
    """Resolve the hard-coded /usr/lib candidates inside ``tmp_path``."""
    real_path = app.Path
    monkeypatch.setattr(app, "Path", lambda p: real_path(tmp_path, str(p).lstrip("/")))
    lib = tmp_path / "usr" / "lib"
    lib.mkdir(parents=True)
    return lib


def test_system_qt_version_comes_from_the_library_symlink(tmp_path, monkeypatch):
    lib = _fake_root(tmp_path, monkeypatch)
    (lib / "libQt6Core.so.6.11.2").write_bytes(b"")
    (lib / "libQt6Core.so.6").symlink_to("libQt6Core.so.6.11.2")
    assert app._detect_system_qt6_version() == "6.11.2"


def test_an_unversioned_library_never_enables_system_plugins(tmp_path, monkeypatch):
    lib = _fake_root(tmp_path, monkeypatch)
    (lib / "libQt6Core.so.6").write_bytes(b"")
    assert not app._system_qt_plugins_loadable("6.11.2", app._detect_system_qt6_version())


def test_system_qt_version_is_none_without_qt(tmp_path, monkeypatch):
    _fake_root(tmp_path, monkeypatch)
    assert app._detect_system_qt6_version() is None


def test_system_plugin_path_needs_a_styles_dir(tmp_path, monkeypatch):
    plain = tmp_path / "a"
    styled = tmp_path / "b"
    plain.mkdir()
    (styled / "styles").mkdir(parents=True)
    monkeypatch.setattr(app, "_SYSTEM_QT6_PLUGIN_PATHS", (plain, styled))
    assert app._find_system_qt6_plugin_path() == styled
    monkeypatch.setattr(app, "_SYSTEM_QT6_PLUGIN_PATHS", (plain,))
    assert app._find_system_qt6_plugin_path() is None
