"""System Qt plugin-path augmentation must never break startup.

Regression test for the 0.4.2 pipx ship-blocker: ``_augment_qt_plugin_path``
compared only Qt's MAJOR.MINOR, so a PySide6 wheel bundling Qt 6.11.1 on a
distro carrying Qt 6.11.2 (a rolling distro one patch ahead) *prepended* the
system plugin tree via ``addLibraryPath``. Qt refuses a plugin built against
a newer Qt than the one running, so both the wayland and xcb platform
plugins were found, rejected, and — because the prepended path won — never
fell back to the wheel's own. The app aborted with "no Qt platform plugin
could be initialized".

Two independent guards are asserted here:
  * the version check rejects a system Qt newer than ours, and
  * augmentation appends rather than prepends, so even a wrongly accepted
    system tree can only cost the styles, never the ability to start.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")


# ---------------------------------------------------------------------------
# Version compatibility
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("bundled", "system", "loadable"),
    [
        # The shipped bug: wheel Qt 6.11.1 against Arch/CachyOS Qt 6.11.2.
        ("6.11.1", "6.11.2", False),
        # System older or equal is fine — Qt loads older plugins.
        ("6.11.2", "6.11.1", True),
        ("6.11.2", "6.11.2", True),
        ("6.11.2", "6.11", True),
        # Minor mismatches stay off-limits in both directions.
        ("6.11.0", "6.12.0", False),
        ("6.12.0", "6.11.0", False),
        ("6.11.0", "5.15.2", False),
        # Unparsable input must fail closed, not raise.
        ("6.11.1", "garbage", False),
        ("", "6.11.1", False),
    ],
)
def test_system_qt_plugins_loadable(bundled, system, loadable):
    from refrain.app import _system_qt_plugins_loadable

    assert _system_qt_plugins_loadable(bundled, system) is loadable


def test_qt_version_tuple_stops_at_non_numeric():
    from refrain.app import _qt_version_tuple

    assert _qt_version_tuple("6.11.2") == (6, 11, 2)
    assert _qt_version_tuple("6.11") == (6, 11)
    assert _qt_version_tuple("6.11.2rc1") == (6, 11, 2)
    assert _qt_version_tuple("garbage") == ()


# ---------------------------------------------------------------------------
# Augmentation behaviour
# ---------------------------------------------------------------------------
class _FakeQCoreApplication:
    """Records library-path mutations the way Qt would apply them."""

    def __init__(self, initial: list[str]) -> None:
        self._paths = list(initial)

    def libraryPaths(self) -> list[str]:
        return list(self._paths)

    def setLibraryPaths(self, paths) -> None:
        self._paths = list(paths)

    def addLibraryPath(self, path: str) -> None:
        # Qt prepends — the behaviour that caused the crash.
        self._paths.insert(0, str(path))


def _install_fakes(monkeypatch, tmp_path, *, bundled: str, system: str):
    """Point ``_augment_qt_plugin_path`` at a wheel-shaped bundled tree
    (no ``styles/``) and a fake system tree, and capture path mutations."""
    import refrain.app as app

    bundled_plugins = tmp_path / "wheel" / "PySide6" / "Qt" / "plugins"
    (bundled_plugins / "platforms").mkdir(parents=True)

    system_plugins = tmp_path / "usr" / "lib" / "qt6" / "plugins"
    (system_plugins / "styles").mkdir(parents=True)

    fake_info = SimpleNamespace(
        LibraryPath=SimpleNamespace(PluginsPath=object()),
        path=lambda _which: str(bundled_plugins),
        version=lambda: SimpleNamespace(toString=lambda: bundled),
    )
    monkeypatch.setattr(app, "QLibraryInfo", fake_info)
    monkeypatch.setattr(app, "_find_system_qt6_plugin_path", lambda: system_plugins)
    monkeypatch.setattr(app, "_detect_system_qt6_version", lambda: system)
    monkeypatch.setattr(app.sys, "platform", "linux")

    fake_qapp = _FakeQCoreApplication([str(bundled_plugins)])
    monkeypatch.setattr(app, "QCoreApplication", fake_qapp)
    return app, fake_qapp, bundled_plugins, system_plugins


def test_newer_system_qt_is_not_added(monkeypatch, tmp_path):
    """The exact shipped failure: system one patch ahead must be skipped."""
    app, qapp, bundled, _system = _install_fakes(
        monkeypatch, tmp_path, bundled="6.11.1", system="6.11.2"
    )

    app._augment_qt_plugin_path()

    assert qapp.libraryPaths() == [str(bundled)]


def test_compatible_system_qt_is_appended_not_prepended(monkeypatch, tmp_path):
    """When the versions do line up, the bundled tree keeps first claim on
    the platform plugin — the system tree may only be consulted after it."""
    app, qapp, bundled, system = _install_fakes(
        monkeypatch, tmp_path, bundled="6.11.2", system="6.11.2"
    )

    app._augment_qt_plugin_path()

    assert qapp.libraryPaths() == [str(bundled), str(system)]


def test_distro_pyside_short_circuits(monkeypatch, tmp_path):
    """Running against the distro PySide6 (its plugin dir already has
    ``styles/``) must leave the library paths completely alone."""
    import refrain.app as app

    plugins = tmp_path / "usr" / "lib" / "qt6" / "plugins"
    (plugins / "styles").mkdir(parents=True)

    fake_info = SimpleNamespace(
        LibraryPath=SimpleNamespace(PluginsPath=object()),
        path=lambda _which: str(plugins),
        version=lambda: SimpleNamespace(toString=lambda: "6.11.2"),
    )
    monkeypatch.setattr(app, "QLibraryInfo", fake_info)
    monkeypatch.setattr(app.sys, "platform", "linux")

    def _boom():  # pragma: no cover - must not be reached
        raise AssertionError("system plugin lookup should be short-circuited")

    monkeypatch.setattr(app, "_find_system_qt6_plugin_path", _boom)

    fake_qapp = _FakeQCoreApplication([str(plugins)])
    monkeypatch.setattr(app, "QCoreApplication", fake_qapp)

    app._augment_qt_plugin_path()

    assert fake_qapp.libraryPaths() == [str(plugins)]


def test_unknown_system_version_is_not_added(monkeypatch, tmp_path):
    app, qapp, bundled, _system = _install_fakes(
        monkeypatch, tmp_path, bundled="6.11.2", system="6.11.2"
    )
    monkeypatch.setattr(app, "_detect_system_qt6_version", lambda: None)

    app._augment_qt_plugin_path()

    assert qapp.libraryPaths() == [str(bundled)]


def test_non_linux_is_left_alone(monkeypatch, tmp_path):
    app, qapp, bundled, _system = _install_fakes(
        monkeypatch, tmp_path, bundled="6.11.2", system="6.11.2"
    )
    monkeypatch.setattr(app.sys, "platform", "darwin")

    app._augment_qt_plugin_path()

    assert qapp.libraryPaths() == [str(bundled)]


def test_real_augmentation_never_breaks_qapplication(monkeypatch, tmp_path):
    """Belt-and-braces: the real function against the real Qt must leave a
    constructible QApplication behind."""
    from PySide6.QtCore import QCoreApplication

    import refrain.app as app

    before = QCoreApplication.libraryPaths()
    app._augment_qt_plugin_path()
    after = QCoreApplication.libraryPaths()

    # Whatever it decided, the originally-first path stays first.
    assert after[: len(before)] == before
    assert Path(after[0]).is_dir()
