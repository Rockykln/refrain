"""Full-uninstall core: collect_paths / removal_command / purge + CLI.

STRICTLY HERMETIC. The desktop/icon paths use ``Path.home()`` (not
XDG-redirectable), so every test monkeypatches the two home-path
helpers into the tmp tree — a non-hermetic run here would delete the
developer's real menu entry (it did, once; never again).
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from refrain import uninstall


@pytest.fixture
def iso(xdg_tmp, monkeypatch, tmp_path):
    """Isolate config/state/cache (xdg_tmp) AND the home-based
    desktop/icon paths into the per-test tmp tree."""
    apps = tmp_path / "applications" / "refrain.desktop"
    icon = tmp_path / "icons" / "refrain.svg"
    monkeypatch.setattr(uninstall, "_user_apps_desktop", lambda: apps)
    monkeypatch.setattr(uninstall, "_user_icon_svg", lambda: icon)
    return {"xdg": xdg_tmp, "apps": apps, "icon": icon}


class FakeStore:
    def __init__(self):
        self.deleted: list[str] = []

    def delete(self, key):
        self.deleted.append(key)


def _seed(iso):
    """Create one real file/dir in every location uninstall targets."""
    from refrain.paths import autostart_path, cache_dir, config_dir, state_dir

    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "config.toml").write_text("x", encoding="utf-8")
    state_dir().mkdir(parents=True, exist_ok=True)
    (state_dir() / "refrain.log").write_text("l", encoding="utf-8")
    cache_dir().mkdir(parents=True, exist_ok=True)
    (cache_dir() / "a.jpg").write_text("c", encoding="utf-8")
    autostart_path().parent.mkdir(parents=True, exist_ok=True)
    autostart_path().write_text("d", encoding="utf-8")
    iso["apps"].parent.mkdir(parents=True, exist_ok=True)
    iso["apps"].write_text("desktop", encoding="utf-8")
    iso["icon"].parent.mkdir(parents=True, exist_ok=True)
    iso["icon"].write_text("svg", encoding="utf-8")


# --------------------------------------------------------------------------- #
# collect_paths                                                               #
# --------------------------------------------------------------------------- #


def test_collect_paths_only_existing(iso):
    assert uninstall.collect_paths() == []  # nothing seeded yet
    _seed(iso)
    found = {p.name for p in uninstall.collect_paths()}
    assert {"refrain", "refrain.desktop", "refrain.svg"} <= found
    # config/state/cache dirs are all named "refrain"
    assert sum(p.name == "refrain" for p in uninstall.collect_paths()) == 3


# --------------------------------------------------------------------------- #
# removal_command                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "install_type,needle",
    [
        ("pip", "pip uninstall refrain"),
        ("pipx", "pipx uninstall refrain"),
        ("flatpak", "flatpak uninstall io.github.Rockykln.Refrain"),
        ("aur", "-R refrain"),
        ("system", "package manager"),
        ("dev", "source checkout"),
    ],
)
def test_removal_command(install_type, needle):
    assert needle in uninstall.removal_command(install_type)


def test_removal_command_appimage_uses_path():
    assert uninstall.removal_command("appimage", "/x/Refrain.AppImage") == "rm /x/Refrain.AppImage"
    assert "<the Refrain" in uninstall.removal_command("appimage", None)


# --------------------------------------------------------------------------- #
# purge                                                                       #
# --------------------------------------------------------------------------- #


def test_purge_removes_everything_and_is_idempotent(iso):
    _seed(iso)
    fake = FakeStore()
    rep = uninstall.purge(secret_store=fake)
    # 3 xdg dirs (config/state/cache) + autostart file + desktop + icon
    assert len(rep.removed) == 6
    assert rep.failed == []
    assert rep.secrets_purged is True
    assert fake.deleted == ["lastfm_shared_secret", "lastfm_session_key"]
    assert uninstall.collect_paths() == []  # actually gone
    # Second run: nothing left, still no error.
    rep2 = uninstall.purge(secret_store=fake)
    assert rep2.removed == [] and rep2.failed == []


def test_purge_tolerates_unremovable_path(iso, monkeypatch):
    _seed(iso)
    real_rmtree = uninstall.shutil.rmtree

    def boom(path, *a, **kw):
        if path.name == "refrain" and "config" in str(path):
            raise OSError("permission denied")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(uninstall.shutil, "rmtree", boom)
    rep = uninstall.purge(secret_store=FakeStore())
    assert any("permission denied" in f for f in rep.failed)
    assert rep.removed  # the other paths still went


def test_purge_secret_failure_is_non_fatal(iso, monkeypatch):
    _seed(iso)

    class Raising:
        def delete(self, k):
            raise RuntimeError("keyring down")

    rep = uninstall.purge(secret_store=Raising())
    assert rep.secrets_purged is False
    assert rep.removed  # file removal still succeeded


# --------------------------------------------------------------------------- #
# CLI wrapper (refrain --uninstall)                                           #
# --------------------------------------------------------------------------- #


def test_cli_aborts_without_confirmation(iso, monkeypatch):
    _seed(iso)
    from refrain.app import run_uninstall_cli

    monkeypatch.setattr("builtins.input", lambda *_a: "n")
    out = io.StringIO()
    with redirect_stdout(out):
        rc = run_uninstall_cli(assume_yes=False)
    assert rc == 1
    assert "Aborted" in out.getvalue()
    assert uninstall.collect_paths()  # nothing was deleted


def test_cli_assume_yes_purges(iso, monkeypatch):
    _seed(iso)
    from refrain.app import run_uninstall_cli

    # Don't touch the real keyring even though keyring may exist here.
    monkeypatch.setattr(uninstall, "_purge_secrets", lambda store=None: True)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = run_uninstall_cli(assume_yes=True)
    assert rc == 0
    text = out.getvalue()
    assert "Removed:" in text
    assert "remove the program itself" in text
    assert uninstall.collect_paths() == []
