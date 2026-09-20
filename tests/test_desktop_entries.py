"""The desktop entries: one set of valid categories, the same everywhere."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from refrain.autostart import _DESKTOP_ENTRY_TEMPLATE

ROOT = Path(__file__).resolve().parent.parent
ENTRIES = {
    "menu": (ROOT / "src/refrain/assets/refrain.desktop").read_text(encoding="utf-8"),
    "flatpak": (ROOT / "packaging/flatpak/io.github.Rockykln.Refrain.desktop").read_text(
        encoding="utf-8"
    ),
    "autostart": _DESKTOP_ENTRY_TEMPLATE.format(exec_line="refrain --silent"),
}
# freedesktop's main categories: a menu files an app under each one it names.
MAIN_CATEGORIES = {
    "AudioVideo",
    "Development",
    "Education",
    "Game",
    "Graphics",
    "Network",
    "Office",
    "Science",
    "Settings",
    "System",
    "Utility",
}


def _categories(entry: str) -> list[str]:
    line = next(line for line in entry.splitlines() if line.startswith("Categories="))
    return [c for c in line.removeprefix("Categories=").split(";") if c]


def test_every_entry_has_the_same_categories():
    assert len({tuple(_categories(e)) for e in ENTRIES.values()}) == 1


@pytest.mark.parametrize("name", ENTRIES)
def test_one_main_category_so_the_app_is_listed_once(name):
    assert [c for c in _categories(ENTRIES[name]) if c in MAIN_CATEGORIES] == ["AudioVideo"]


@pytest.mark.skipif(not shutil.which("desktop-file-validate"), reason="desktop-file-utils")
@pytest.mark.parametrize("name", ENTRIES)
def test_desktop_file_validate_accepts_it(name, tmp_path):
    path = tmp_path / f"{name}.desktop"
    path.write_text(ENTRIES[name], encoding="utf-8")
    result = subprocess.run(
        ["desktop-file-validate", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "error" not in result.stdout + result.stderr


def test_an_installed_entry_keeps_backslashes_in_its_exec_line(tmp_path, monkeypatch):
    import refrain.app as app

    monkeypatch.setattr(app, "_user_apps_dir", lambda: tmp_path / "apps")
    monkeypatch.setattr(app, "_user_icons_dir", lambda: tmp_path / "icons")
    monkeypatch.setattr(app, "resolve_exec_line", lambda: '"/opt/my\\\\\\\\apps/refrain"')
    assert app.install_desktop_files() == 0
    text = (tmp_path / "apps" / "refrain.desktop").read_text(encoding="utf-8")
    assert 'Exec="/opt/my\\\\\\\\apps/refrain"' in text


def test_desktop_files_follow_xdg_data_home(tmp_path, monkeypatch):
    import refrain.app as app

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert app._user_apps_dir() == tmp_path / "data" / "applications"
    assert app._user_icons_dir() == tmp_path / "data" / "icons" / "hicolor" / "scalable" / "apps"
