"""Follow the desktop's dark mode where the platform theme does not."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import SLOT, QObject, Qt, Slot
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage, QDBusVariant
from PySide6.QtGui import QGuiApplication, QPalette

log = logging.getLogger(__name__)

_SERVICE = "org.freedesktop.portal.Desktop"
_PATH = "/org/freedesktop/portal/desktop"
_IFACE = "org.freedesktop.portal.Settings"
_NAMESPACE = "org.freedesktop.appearance"
_KEY = "color-scheme"
# Two calls at worst (ReadOne, then Read), and both run before the first
# window. A portal that is simply not there answers at once; one that
# hangs must not hold the start up for four seconds.
_TIMEOUT_MS = 1000

# The portal's numbers. 0 is "no preference": the theme's own palette
# decides again, which Qt spells `Unknown`.
_SCHEMES = {
    0: Qt.ColorScheme.Unknown,
    1: Qt.ColorScheme.Dark,
    2: Qt.ColorScheme.Light,
}


def palette_follows_desktop() -> bool:
    """Does the platform theme already hand Qt a palette for the right mode?

    Plasma's does. The gtk3 theme builds its palette from the GTK3 theme,
    and GNOME's dark mode only covers GTK4/libadwaita, so there the
    window stays light however the desktop is set.
    """
    return "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


def _unwrap(value: object) -> object:
    """Peel the variants off a portal reply: `ReadOne` wraps once, `Read` twice."""
    for _ in range(2):
        if not isinstance(value, QDBusVariant):
            break
        value = value.variant()
    return value


class SchemeFollower(QObject):
    """Sets Qt's colour scheme from the portal, and again on every change.

    Only ever corrects a palette that contradicts the desktop: a theme
    that got it right keeps its own colours, which stay closer to the
    rest of the desktop than Qt's built-in dark palette.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # SLOT() marks the signature the way Qt's macro does. PySide's stub
        # asks for bytes here, but the binding itself rejects those.
        connected = QDBusConnection.sessionBus().connect(  # type: ignore[call-overload]
            _SERVICE,
            _PATH,
            _IFACE,
            "SettingChanged",
            self,
            SLOT("_on_setting_changed(QString,QString,QDBusVariant)"),
        )
        if not connected:
            log.debug("Portal setting changes are not being delivered")

    @Slot(str, str, QDBusVariant)
    def _on_setting_changed(self, namespace: str, key: str, value: QDBusVariant) -> None:
        if (namespace, key) != (_NAMESPACE, _KEY):
            return
        self.apply(_as_scheme(_unwrap(value)))

    def read(self) -> Qt.ColorScheme | None:
        bus = QDBusConnection.sessionBus()
        for method in ("ReadOne", "Read"):
            message = QDBusMessage.createMethodCall(_SERVICE, _PATH, _IFACE, method)
            message.setArguments([_NAMESPACE, _KEY])
            reply = bus.call(message, QDBus.CallMode.Block, _TIMEOUT_MS)
            if reply.type() == QDBusMessage.MessageType.ReplyMessage:
                arguments = reply.arguments()
                return _as_scheme(_unwrap(arguments[0])) if arguments else None
            # ReadOne arrived in xdg-desktop-portal 1.17; older ones have Read.
            log.debug("Portal %s failed: %s", method, reply.errorName())
        return None

    def apply(self, wanted: Qt.ColorScheme | None) -> None:
        if wanted is None:
            return
        if wanted == Qt.ColorScheme.Unknown:
            # Handing the choice back, rather than leaving a scheme this
            # followed forced until the next start.
            if QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Unknown:
                log.info("Desktop has no preference; leaving the palette to the theme")
                QGuiApplication.styleHints().unsetColorScheme()
            return
        if (wanted == Qt.ColorScheme.Dark) == _palette_is_dark():
            return
        log.info("Desktop asks for the %s palette; Qt's theme gave the other one", wanted.name)
        QGuiApplication.styleHints().setColorScheme(wanted)


def _as_scheme(value: object) -> Qt.ColorScheme | None:
    return _SCHEMES.get(value) if isinstance(value, int) else None


def _palette_is_dark() -> bool:
    window = QGuiApplication.palette().color(QPalette.ColorRole.Window)
    return window.lightnessF() < 0.5


def follow_desktop(parent: QObject) -> SchemeFollower | None:
    """Start following the desktop's setting, unless the theme has it covered."""
    if palette_follows_desktop():
        return None
    follower = SchemeFollower(parent)
    follower.apply(follower.read())
    return follower
