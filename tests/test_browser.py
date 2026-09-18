"""Which browser a history link goes to; the picking is pure, so nothing is started."""

from __future__ import annotations

import pytest

import refrain.browser as browser
from refrain.browser import browser_for, open_url, running_processes

INSTALLED = {
    "chromium": "/usr/bin/chromium",
    "firefox": "/usr/bin/firefox",
    "zen-browser": "/usr/bin/zen-browser",
}


def _which(name):
    return INSTALLED.get(name)


def test_the_browser_that_played_it_while_it_runs():
    assert browser_for("Chromium", {"chromium", "zen-bin"}, _which) == "/usr/bin/chromium"


def test_a_closed_browser_leaves_it_to_the_default():
    assert browser_for("Chromium", {"zen-bin"}, _which) is None


def test_an_unknown_player_leaves_it_to_the_default():
    assert browser_for("Desk Speaker", {"chromium"}, _which) is None
    assert browser_for("", {"chromium"}, _which) is None


def test_running_but_not_installed_under_a_known_name():
    assert browser_for("Brave", {"brave"}, _which) is None


def test_firefox_forks_are_not_mistaken_for_firefox():
    # Zen reports itself with "Firefox" in some builds; the fork wins.
    assert browser_for("Zen Browser (Firefox)", {"zen-bin"}, _which) == "/usr/bin/zen-browser"


def test_running_processes_reads_proc(tmp_path):
    for pid, name in ((1, "systemd"), (42, "chromium")):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "comm").write_text(name + "\n", encoding="utf-8")
    (tmp_path / "self").mkdir()  # not a pid
    assert running_processes(tmp_path) == {"systemd", "chromium"}


@pytest.mark.parametrize("url", ["", "file:///etc/passwd", "--incognito", "http://x"])
def test_only_https_links_are_opened(url):
    assert open_url(url, "Chromium") is False


# --------------------------------------------------------------------------- #
# open_url: what is actually started                                           #
# --------------------------------------------------------------------------- #


class _Proc:
    started = []

    def __init__(self):
        self.program, self.args, self.env = None, None, None

    def setProgram(self, p):  # noqa: N802
        self.program = p

    def setArguments(self, a):  # noqa: N802
        self.args = a

    def setProcessEnvironment(self, e):  # noqa: N802
        self.env = e

    def startDetached(self):  # noqa: N802
        _Proc.started.append(self)
        return (self.ok, 4242)


def _patch_qt(monkeypatch, *, start_ok=True, default_ok=True):
    import PySide6.QtCore as core
    import PySide6.QtGui as gui

    _Proc.started = []
    _Proc.ok = start_ok
    opened = []
    monkeypatch.setattr(core, "QProcess", _Proc)
    monkeypatch.setattr(
        gui.QDesktopServices,
        "openUrl",
        staticmethod(lambda u: opened.append(u.toString()) or default_ok),
    )
    return opened


def test_the_playing_browser_gets_the_link_as_one_argument(monkeypatch):
    opened = _patch_qt(monkeypatch)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(browser, "running_processes", lambda: {"chrome"})
    monkeypatch.setattr(browser.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    monkeypatch.setenv("PYTHONPATH", "/somewhere/private")
    url = "https://music.apple.com/de/album/x/1?i=2&uo=4"
    assert browser.open_url(url, "Google Chrome") is True
    (proc,) = _Proc.started
    assert proc.program == "/usr/bin/google-chrome-stable"
    assert proc.args == [url], "no shell, the link untouched"
    assert "PYTHONPATH" not in proc.env.keys(), "Refrain's own environment stays out"
    assert opened == []


def test_a_browser_that_wont_start_falls_back_to_the_default(monkeypatch):
    opened = _patch_qt(monkeypatch, start_ok=False)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(browser, "running_processes", lambda: {"firefox"})
    monkeypatch.setattr(browser.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    assert browser.open_url("https://music.apple.com/x", "Firefox") is True
    assert opened == ["https://music.apple.com/x"]


def test_a_sandboxed_refrain_always_uses_the_default_browser(monkeypatch):
    opened = _patch_qt(monkeypatch)
    monkeypatch.setenv("FLATPAK_ID", "io.github.Rockykln.Refrain")
    monkeypatch.setattr(browser, "running_processes", lambda: {"firefox"})
    assert browser.open_url("https://music.apple.com/x", "Firefox") is True
    assert _Proc.started == [] and opened == ["https://music.apple.com/x"]
