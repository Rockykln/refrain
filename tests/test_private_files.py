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
