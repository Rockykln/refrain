"""notify-send argv builder + --print-id parsing.

Covers the cover-replace path: a brand-fallback notification fired when
iTunes is slow, then re-issued with `--replace-id` once the cover lands
so it swaps into the same bubble instead of stacking a second popup.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from refrain.daemon import build_notify_argv, notify_over_dbus, parse_notify_id


def test_argv_with_image_passes_it_twice():
    argv = build_notify_argv("notify-send", "/tmp/cover.jpg", "Song", "Artist — Album")
    assert argv[0] == "notify-send"
    assert "-i" in argv
    assert argv[argv.index("-i") + 1] == "/tmp/cover.jpg"
    # Same file also goes through the freedesktop image-path hint so KDE
    # doesn't flash the brand badge while loading it.
    assert "--hint" in argv
    assert "string:image-path:file:///tmp/cover.jpg" in argv
    # Title + body are the trailing positionals.
    assert argv[-2:] == ["Song", "Artist — Album"]


def test_argv_without_image_uses_themed_name_and_no_hint():
    argv = build_notify_argv("notify-send", None, "Song", "")
    assert argv[argv.index("-i") + 1] == "refrain"
    assert "--hint" not in argv
    assert argv[-2:] == ["Song", ""]


def test_argv_replace_id_and_print_id():
    argv = build_notify_argv("notify-send", "/c.jpg", "S", "B", replace_id=42, print_id=True)
    assert "--replace-id" in argv
    assert argv[argv.index("--replace-id") + 1] == "42"
    assert "--print-id" in argv


def test_argv_no_replace_id_by_default():
    argv = build_notify_argv("notify-send", None, "S", "B")
    assert "--replace-id" not in argv
    assert "--print-id" not in argv


def test_parse_id_plain():
    assert parse_notify_id("42\n") == 42


def test_parse_id_takes_first_line():
    assert parse_notify_id("17\nsome warning on stderr-merged\n") == 17


def test_parse_id_empty_is_none():
    assert parse_notify_id("") is None
    assert parse_notify_id("   \n") is None


def test_parse_id_non_numeric_is_none():
    # libnotify build without --print-id support, or a wrapper that
    # prints something else — degrade to "no later swap" not a crash.
    assert parse_notify_id("not-an-id") is None


def test_a_title_starting_with_a_dash_is_not_an_option():
    argv = build_notify_argv("notify-send", None, "-u critical", "--app-name=12345")
    assert argv[-3:] == ["--", "-u critical", "--app-name=12345"]
    assert argv.index("--") == len(argv) - 3


class _FakeDbus:
    """Enough of the dbus module for the notification service call."""

    class UInt32(int):
        pass

    def __init__(self, calls, answer=7):
        self.calls = calls
        self.answer = answer

    def SessionBus(self):  # noqa: N802 — mirrors the dbus API
        return SimpleNamespace(get_object=lambda *a: object())

    def Interface(self, _obj, _name):  # noqa: N802 — mirrors the dbus API
        def notify(*args):
            self.calls.append(args)
            return self.answer

        return SimpleNamespace(Notify=notify)


def test_without_notify_send_the_service_is_asked_directly(monkeypatch):
    """Notifications must not need the notify-send binary to work at all."""
    calls: list[tuple] = []
    monkeypatch.setitem(sys.modules, "dbus", _FakeDbus(calls))

    got = notify_over_dbus("/tmp/cover.jpg", "Song", "Artist — Album", replace_id=4)

    assert got == 7
    (app, replaces, icon, summary, body, actions, hints, timeout) = calls[0]
    assert app == "Refrain"
    assert int(replaces) == 4
    assert icon == "/tmp/cover.jpg"
    assert summary == "Song" and body == "Artist — Album"
    assert hints == {"image-path": "file:///tmp/cover.jpg"}
    assert actions == [] and timeout == -1


def test_without_a_cover_the_themed_name_is_used(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setitem(sys.modules, "dbus", _FakeDbus(calls))
    notify_over_dbus(None, "Song", "")
    assert calls[0][2] == "refrain"
    assert calls[0][6] == {}


def test_a_service_that_is_not_there_is_not_an_error(monkeypatch):
    class _Broken(_FakeDbus):
        def SessionBus(self):  # noqa: N802
            raise RuntimeError("no session bus")

    monkeypatch.setitem(sys.modules, "dbus", _Broken([]))
    assert notify_over_dbus(None, "Song", "") is None
