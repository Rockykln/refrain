"""One locale for the whole window, not two.

Refrain's own strings honoured `advanced.language`; Qt's built-in
translations for stock widgets read `QLocale.system()` regardless. On a
German desktop with the language set to English, that put Qt's
"Abbrechen" next to Refrain's "Cancel" in the same button row.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale  # noqa: E402

from refrain.app import ui_locale  # noqa: E402


def test_system_follows_the_desktop():
    assert ui_locale("system").name() == QLocale.system().name()
    assert ui_locale("").name() == QLocale.system().name()


@pytest.mark.parametrize("code", ["de", "es", "fr", "pt", "it", "ru", "pl", "ja", "zh_CN"])
def test_every_shipped_language_still_finds_its_catalog(code):
    """The override goes through QLocale now, so "pt" arrives as "pt_BR".

    Catalog lookup tries the full name first and the language prefix
    second, which is what keeps the shipped `refrain_pt.qm` reachable.
    """
    name = ui_locale(code).name()
    assert code in (name, name.split("_", 1)[0])


def test_an_explicit_pick_does_not_fall_back_to_the_desktop(monkeypatch):
    # Whatever the desktop speaks, an explicit choice wins — for Qt's
    # own translations just as much as for Refrain's.
    assert ui_locale("ja").language() == QLocale.Japanese
    assert ui_locale("en").language() == QLocale.English
