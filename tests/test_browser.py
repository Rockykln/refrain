"""Which browser a history link goes to.

The picking is pure — player name, running processes and a ``which``
stand-in go in, an executable (or None, meaning the default browser)
comes out — so none of this starts anything.
"""

from __future__ import annotations

import pytest

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
    assert browser_for("WH-1000XM5", {"chromium"}, _which) is None
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
