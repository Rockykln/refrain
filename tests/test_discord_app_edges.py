"""discord_app.py: caching a confirmed name must survive the config write failing."""

from __future__ import annotations

import logging

from refrain.config import Config
from refrain.discord_app import remember_application_name

VALID_ID = "1234567890123456789"


def test_a_config_that_cannot_be_saved_still_updates_the_cache_in_memory(monkeypatch, caplog):
    cfg = Config()

    def refuse(self, *_a, **_kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(Config, "save", refuse)
    with caplog.at_level(logging.DEBUG, logger="refrain.discord_app"):
        remember_application_name(cfg, VALID_ID, "Apple Music")

    assert cfg.discord.app_name == "Apple Music"
    assert cfg.discord.app_name_for_id == VALID_ID
    assert cfg.discord.app_name_checked_ts > 0
    assert any("cached application name" in r.getMessage() for r in caplog.records)
