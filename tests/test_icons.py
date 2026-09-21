"""Icon fallback chain: KDE-only Breeze names have no equivalent outside Plasma.

Builds tiny on-disk icon themes (no KDE names, only freedesktop-standard ones,
or none at all) and checks that `themed_icon` — and every UI element that
used to call `QIcon.fromTheme` directly — still ends up with a usable icon.
"""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, QPoint  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from refrain.history import HistoryEntry, HistorySnapshot  # noqa: E402
from refrain.paths import assets_dir  # noqa: E402
from refrain.service_status import DiscordStatus, LastfmStatus, StatusSnapshot  # noqa: E402
from refrain.ui import icons  # noqa: E402
from refrain.ui.history_window import HistoryWindow, _SongRow, _source_icon  # noqa: E402
from refrain.ui.status_window import StatusWindow  # noqa: E402
from refrain.ui.tray import TrayIcon  # noqa: E402

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    '<rect width="16" height="16" fill="#123456"/></svg>'
)

_INDEX_THEME = """[Icon Theme]
Name={name}
Comment=test fixture
Directories=16x16/actions

[16x16/actions]
Size=16
Context=Actions
Type=Fixed
"""


def _make_theme(tmp_path, names: list[str], seq: int) -> str:
    """A minimal on-disk icon theme, unique per test, holding only `names`."""
    theme_name = f"T{tmp_path.name}_{seq}"
    theme_dir = tmp_path / theme_name
    action_dir = theme_dir / "16x16" / "actions"
    action_dir.mkdir(parents=True)
    (theme_dir / "index.theme").write_text(_INDEX_THEME.format(name=theme_name), encoding="utf-8")
    for name in names:
        (action_dir / f"{name}.svg").write_text(_SVG, encoding="utf-8")
    return theme_name


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def icon_theme(tmp_path):
    """Activate a throwaway theme with the given names; always restored after."""
    search_paths = QIcon.themeSearchPaths()
    theme_name = QIcon.themeName()
    counter = [0]

    def _activate(names: list[str]) -> None:
        counter[0] += 1
        name = _make_theme(tmp_path, names, counter[0])
        QIcon.setThemeSearchPaths([str(tmp_path)])
        QIcon.setThemeName(name)

    yield _activate
    QIcon.setThemeSearchPaths(search_paths)
    QIcon.setThemeName(theme_name)


# --------------------------------------------------------------------------- #
# themed_icon() itself
# --------------------------------------------------------------------------- #


def test_kde_name_wins_when_the_theme_has_it(app, icon_theme):
    icon_theme(["configure", "preferences-system"])
    assert icons.themed_icon("configure").name() == "configure"


@pytest.mark.parametrize(
    ("kde_name", "fallback"),
    [
        ("configure", "preferences-system"),
        ("view-list-text", "view-list"),
        ("tools-report-bug", "dialog-warning"),
        ("chronometer", "appointment-soon"),
        ("view-media-track", "audio-x-generic"),
        ("view-media-artist", "avatar-default"),
        ("edit-clear-history", "edit-clear"),
        ("network-disconnect", "network-offline"),
        ("network-connect", "network-transmit-receive"),
    ],
)
def test_falls_back_to_the_standard_name_for_every_kde_only_icon(
    app, icon_theme, kde_name, fallback
):
    """The exact 9 KDE-only names found on Mint/Cinnamon, one by one."""
    icon_theme([fallback])
    icon = icons.themed_icon(kde_name)
    assert not icon.isNull()
    assert icon.name() == fallback


def test_falls_back_to_the_second_standard_name_in_a_chain(app, icon_theme):
    # "view-list" is missing too — only the second candidate is present.
    icon_theme(["format-justify-fill"])
    icon = icons.themed_icon("view-list-text")
    assert not icon.isNull()
    assert icon.name() == "format-justify-fill"

    icon_theme(["network-idle"])
    icon = icons.themed_icon("network-connect")
    assert not icon.isNull()
    assert icon.name() == "network-idle"


def test_caller_supplied_extra_fallbacks_are_tried(app, icon_theme):
    icon_theme(["applications-internet"])
    icon = icons.themed_icon("internet-web-browser", "applications-internet")
    assert not icon.isNull()
    assert icon.name() == "applications-internet"


def test_chronometer_prefers_the_standard_name_over_the_bundled_glyph(app, icon_theme):
    icon_theme(["appointment-soon"])
    icon = icons.themed_icon("chronometer")
    assert icon.name() == "appointment-soon"


def test_chronometer_falls_back_to_the_bundled_glyph(app, icon_theme):
    icon_theme([])  # neither "chronometer" nor "appointment-soon" anywhere
    icon = icons.themed_icon("chronometer")
    assert not icon.isNull()
    assert icon.name() == ""  # a file-based icon, not a theme lookup
    bundled = QIcon(str(assets_dir() / "icons" / "chronometer.svg"))
    assert icon.pixmap(16, 16).toImage() == bundled.pixmap(16, 16).toImage()


def test_a_name_with_no_bundled_glyph_and_no_match_stays_null(app, icon_theme):
    """Documents the actual contract: a bundled glyph only exists for
    "chronometer" — every other name relies on a standard name existing."""
    icon_theme([])
    assert icons.themed_icon("configure").isNull()


# --------------------------------------------------------------------------- #
# Real UI elements, under a theme with none of the 9 KDE-only names
# --------------------------------------------------------------------------- #

# What a reasonably complete non-KDE theme (GNOME/Mint-Y-like) ships: every
# standard name Refrain's UI falls back to, but neither the KDE names nor
# "appointment-soon" (chronometer's bundled glyph must carry that one) nor
# "internet-web-browser" (a name that happens to work today but isn't in the
# freedesktop spec, so a strict theme may not have it either).
_NON_KDE_THEME = [
    "preferences-system",
    "view-list",
    "format-justify-fill",
    "dialog-warning",
    "audio-x-generic",
    "avatar-default",
    "edit-clear",
    "network-offline",
    "network-transmit-receive",
    "network-idle",
    "applications-internet",
    "bluetooth",
    "edit-copy",
    "edit-delete",
    "media-playback-start",
    "media-playback-pause",
    "media-skip-backward",
    "media-skip-forward",
    "view-refresh",
    "document-open-recent",
    "applications-development",
]


def test_tray_menu_actions_all_have_icons_without_kde_names(app, icon_theme):
    icon_theme(_NON_KDE_THEME)
    tray = TrayIcon()
    for combo in (
        (DiscordStatus.NOT_SET_UP, LastfmStatus.NOT_CONNECTED),
        (DiscordStatus.NO_CLIENT, LastfmStatus.CONNECTED_OFF),
        (DiscordStatus.READY, LastfmStatus.SCROBBLING),
    ):
        tray.set_service_status(StatusSnapshot(discord=combo[0], lastfm=combo[1]))
        assert not tray._discord_action.icon().isNull()
        assert not tray._lastfm_action.icon().isNull()

    for attr in (
        "_title_action",
        "_artist_action",
        "_progress_action",
        "_developer_action",
        "_previous_action",
        "_play_pause_action",
        "_next_action",
        "_history_action",
        "_settings_action",
        "_log_action",
        "_restart_action",
    ):
        action = getattr(tray, attr)
        assert not action.icon().isNull(), f"{attr} has no icon"
    assert not tray._more_menu.icon().isNull()


def test_status_window_buttons_all_have_icons_without_kde_names(app, icon_theme):
    icon_theme(_NON_KDE_THEME)
    win = StatusWindow(QLocale("en_US"))
    try:
        for attr in ("previous_btn", "next_btn", "settings_btn", "play_btn", "sharing_btn"):
            button = getattr(win, attr)
            assert not button.icon().isNull(), f"{attr} has no icon"
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()


def test_history_row_source_icons_have_icons_without_kde_names(app, icon_theme):
    icon_theme(_NON_KDE_THEME)
    assert not _source_icon("mpris").isNull()
    assert not _source_icon("bluetooth").isNull()


def test_history_window_icons_all_have_icons_without_kde_names(app, icon_theme, monkeypatch):
    icon_theme(_NON_KDE_THEME)
    win = HistoryWindow(QLocale("en_US"))
    try:
        win.show()
        entries = (HistoryEntry(title="Song", artist="Artist", source="mpris", player="Chromium"),)
        win.set_snapshot(HistorySnapshot(entries=entries))
        win.reset_filters()
        assert not win.clear_btn.icon().isNull()

        row = win.findChildren(_SongRow)[0]
        captured: list = []

        class _Menu(QMenu):
            # PySide resolves QMenu.exec in C++, so the menu class itself is
            # swapped for one that records the actions and never blocks.
            def exec(self, *_args):
                captured.extend(self.actions())
                return None

        monkeypatch.setattr("refrain.ui.history_window.QMenu", _Menu)
        point = QPoint(5, 5)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, point)
        row.contextMenuEvent(event)

        named = [a for a in captured if a.text()]
        assert len(named) == 3  # Open in Apple Music, Copy, Remove
        for action in named:
            assert not action.icon().isNull(), action.text()
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()
