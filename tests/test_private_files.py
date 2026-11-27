"""The log and the cover cache are readable by the owner only."""

from __future__ import annotations

import logging
import stat

import pytest


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in root.handlers:
        if h not in handlers:
            h.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def test_log_file_and_backups_are_owner_only(xdg_tmp, restore_root_logger):
    from refrain.logging_setup import setup_logging
    from refrain.paths import log_path

    log = log_path()
    log.parent.mkdir(parents=True)
    old_backup = log.with_name(log.name + ".1")
    old_backup.write_text("older session\n")
    old_backup.chmod(0o644)

    setup_logging("INFO")
    logging.getLogger("refrain.test").info("hello")

    assert _mode(log) == 0o600
    assert _mode(old_backup) == 0o600


def test_rotated_log_stays_owner_only(xdg_tmp, restore_root_logger):
    from refrain.logging_setup import setup_logging
    from refrain.paths import log_path

    setup_logging("INFO")
    handler = next(h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler))
    handler.doRollover()

    assert _mode(log_path()) == 0o600
    assert _mode(log_path().with_name("refrain.log.1")) == 0o600


def test_an_existing_log_is_tightened(xdg_tmp, restore_root_logger):
    from refrain.logging_setup import setup_logging
    from refrain.paths import log_path

    log = log_path()
    log.parent.mkdir(parents=True)
    log.write_text("")
    log.chmod(0o644)

    setup_logging("INFO")

    assert _mode(log) == 0o600


def test_cover_cache_dir_is_owner_only(xdg_tmp):
    from refrain import cover_art
    from refrain.paths import cover_cache_dir

    cover_art._write_cache("12345", cover_art.TrackLookup("https://x/c.jpg", "", 0))
    assert _mode(cover_cache_dir()) == 0o700


def test_an_existing_cover_cache_dir_is_tightened(xdg_tmp, tmp_path):
    from refrain import cover_art
    from refrain.paths import cover_cache_dir

    cover_cache_dir().mkdir(parents=True, mode=0o755)
    cover_cache_dir().chmod(0o755)
    url = "https://example.com/12345.jpg"
    (tmp_path / cover_art.image_name(url)).write_bytes(b"\xff\xd8jpeg")

    cover_art.keep_cover_images({url}, [tmp_path])
    assert cover_art.image_path_for_url(url).exists()
    assert _mode(cover_cache_dir()) == 0o700


def test_a_kept_cover_is_owner_only(xdg_tmp, tmp_path):
    """It came out 0644: the copy wrote through shutil, not write_private."""
    from refrain import cover_art

    url = "https://example.com/12345.jpg"
    (tmp_path / cover_art.image_name(url)).write_bytes(b"\xff\xd8jpeg")
    cover_art.keep_cover_images({url}, [tmp_path])
    kept = cover_art.image_path_for_url(url)
    assert kept.read_bytes() == b"\xff\xd8jpeg"
    assert _mode(kept) == 0o600


def test_the_window_geometry_is_owner_only(xdg_tmp):
    import os
    import sys

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from refrain.ui.status_window import StatusWindow, geometry_path

    QApplication.instance() or QApplication(sys.argv)
    window = StatusWindow()
    try:
        window._save_geometry()
    finally:
        window.deleteLater()
    assert _mode(geometry_path()) == 0o600


def test_the_crash_report_marker_is_owner_only(xdg_tmp, tmp_path):
    from refrain import app
    from refrain.paths import state_dir

    report = tmp_path / "crash.log"
    report.write_text("--- start ---\nboom\n", encoding="utf-8")
    app._remember_crash_reports(report)
    assert _mode(state_dir() / "crash-seen") == 0o600
