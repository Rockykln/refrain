"""Outside Plasma the platform theme can stay light while the desktop asks for dark."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtDBus import QDBusMessage, QDBusVariant  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain.ui import color_scheme as cs  # noqa: E402

_CALL = QDBusMessage.createMethodCall("a.b", "/c", "d.e", "ReadOne")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def palette(app):
    """Paint the application light or dark; always restored after."""
    before = QPalette(app.palette())

    def _set(window: str) -> None:
        changed = QPalette(app.palette())
        changed.setColor(QPalette.ColorRole.Window, QColor(window))
        app.setPalette(changed)

    yield _set
    app.setPalette(before)


@pytest.fixture
def asked(monkeypatch):
    """Record what Refrain asks Qt for instead of repainting the test run."""
    seen: list[Qt.ColorScheme] = []
    monkeypatch.setattr(
        cs.QGuiApplication.styleHints(), "setColorScheme", seen.append, raising=False
    )
    return seen


class _Bus:
    """A session bus that answers ReadOne with whatever the test wants."""

    def __init__(self, replies, connected=True):
        self.replies = list(replies)
        self.connected = connected
        self.calls: list[str] = []

    def call(self, message, _mode, _timeout):
        self.calls.append(message.member())
        return self.replies.pop(0)

    def connect(self, *_args):
        return self.connected


@pytest.fixture
def bus(monkeypatch):
    def _install(replies, connected=True):
        made = _Bus(replies, connected)
        monkeypatch.setattr(cs.QDBusConnection, "sessionBus", staticmethod(lambda: made))
        return made

    return _install


def _reply(value):
    return _CALL.createReply(QDBusVariant(value))


def _error():
    return _CALL.createErrorReply("org.freedesktop.DBus.Error.UnknownMethod", "no ReadOne here")


# what the portal's numbers mean


@pytest.mark.parametrize(
    ("value", "wanted"),
    [(1, Qt.ColorScheme.Dark), (2, Qt.ColorScheme.Light), (0, None), ("dark", None)],
)
def test_the_portals_numbers_name_a_scheme(app, bus, value, wanted):
    bus([_reply(value)])
    assert cs.SchemeFollower().read() == wanted


def test_read_falls_back_to_the_older_portal_method(app, bus):
    """ReadOne arrived in xdg-desktop-portal 1.17; before that it was Read."""
    made = bus([_error(), _reply(QDBusVariant(1))])
    assert cs.SchemeFollower().read() == Qt.ColorScheme.Dark
    assert made.calls == ["ReadOne", "Read"]


def test_no_portal_at_all_reads_as_nothing(app, bus):
    bus([_error(), _error()])
    assert cs.SchemeFollower().read() is None


def test_a_portal_reply_without_a_value_reads_as_nothing(app, bus):
    bus([_CALL.createReply()])
    assert cs.SchemeFollower().read() is None


# when Refrain steps in


def test_a_light_palette_against_a_dark_desktop_is_corrected(app, bus, palette, asked):
    bus([_reply(1)])
    palette("#ffffff")
    follower = cs.SchemeFollower()
    follower.apply(follower.read())
    assert asked == [Qt.ColorScheme.Dark]


def test_a_dark_palette_against_a_light_desktop_is_corrected(app, bus, palette, asked):
    bus([_reply(2)])
    palette("#101010")
    follower = cs.SchemeFollower()
    follower.apply(follower.read())
    assert asked == [Qt.ColorScheme.Light]


@pytest.mark.parametrize(("value", "window"), [(1, "#101010"), (2, "#ffffff")])
def test_a_palette_that_already_agrees_is_left_alone(app, bus, palette, asked, value, window):
    """Plasma's own dark colours beat Qt's generic dark palette."""
    bus([_reply(value)])
    palette(window)
    follower = cs.SchemeFollower()
    follower.apply(follower.read())
    assert asked == []


def test_no_preference_changes_nothing(app, bus, palette, asked):
    bus([_reply(0)])
    palette("#ffffff")
    follower = cs.SchemeFollower()
    follower.apply(follower.read())
    assert asked == []


# live changes


def test_a_changed_setting_is_followed(app, bus, palette, asked):
    bus([])
    palette("#ffffff")
    follower = cs.SchemeFollower()
    follower._on_setting_changed("org.freedesktop.appearance", "color-scheme", QDBusVariant(1))
    assert asked == [Qt.ColorScheme.Dark]


@pytest.mark.parametrize(
    ("namespace", "key"),
    [("org.gnome.desktop.interface", "color-scheme"), ("org.freedesktop.appearance", "accent")],
)
def test_other_settings_are_ignored(app, bus, palette, asked, namespace, key):
    bus([])
    palette("#ffffff")
    follower = cs.SchemeFollower()
    follower._on_setting_changed(namespace, key, QDBusVariant(1))
    assert asked == []


def test_a_bus_that_refuses_the_subscription_is_only_logged(app, bus, caplog):
    bus([], connected=False)
    with caplog.at_level("DEBUG", logger="refrain.ui.color_scheme"):
        cs.SchemeFollower()
    assert "not being delivered" in caplog.text


# who is in charge of the palette


@pytest.mark.parametrize("desktop", ["KDE", "kde", "pop:GNOME:KDE"])
def test_plasma_keeps_its_own_palette(app, bus, palette, asked, monkeypatch, desktop):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    bus([_reply(1)])
    palette("#ffffff")
    assert cs.follow_desktop(app) is None
    assert asked == []


@pytest.mark.parametrize("desktop", ["GNOME", "X-Cinnamon", ""])
def test_everywhere_else_the_portal_decides(app, bus, palette, asked, monkeypatch, desktop):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    bus([_reply(1)])
    palette("#ffffff")
    follower = cs.follow_desktop(app)
    assert follower is not None
    assert asked == [Qt.ColorScheme.Dark]


def test_the_subscription_is_spelled_the_way_the_binding_wants_it(app, monkeypatch):
    """Guards the real call: PySide's stub asks for bytes, which it then refuses.

    Every other test here hands the follower a stand-in bus, so only this
    one would notice the signature drifting.
    """
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent/refrain-test")
    cs.SchemeFollower()
