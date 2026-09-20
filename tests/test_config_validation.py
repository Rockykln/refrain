"""Values of the right type that still make no sense are replaced on load, one warning each."""

from __future__ import annotations

import logging
import time

import pytest

from refrain.config import DEFAULT_BROWSER_HINTS, Config, shipped_languages

GOOD_ID = "1234567890123456789"

# (section, key, bad value, value used instead)
FIXED = [
    ("advanced", "poll_interval_ms", 5, 250),
    ("advanced", "poll_interval_ms", 999_999, 10_000),
    ("behavior", "notify_delay_ms", -1, 0),
    ("behavior", "notify_delay_ms", 60_000, 10_000),
    ("advanced", "position_stall_s", -3, 0),
    ("advanced", "position_stall_s", 100_000, 600),
    ("advanced", "idle_grace_s", -1, 0),
    ("advanced", "idle_grace_s", 999_999, 3600),
    ("history", "max_entries", 0, 1),
    ("history", "max_entries", 5000, 100),
    ("history", "window_width", -200, 0),
    ("history", "window_width", 90_000, 0),
    ("history", "window_height", -1, 0),
    ("history", "window_height", 90_000, 0),
    ("privacy", "mode", "loud", "minimal"),
    ("advanced", "log_level", "CHATTY", "INFO"),
    ("advanced", "language", "xx", "system"),
    ("advanced", "language", "de_DE", "de"),
    ("sources", "bluetooth_device", "my headphones", ""),
    ("sources", "bluetooth_device", "AA:BB:CC:DD:EE", ""),
    ("discord", "client_id", "x" * 40, ""),
    ("discord", "client_id_mpris", "12345 67890", ""),
    ("discord", "client_id_bluetooth", "123\n456", ""),
    ("sources", "browser_hints", ",, ,", ",".join(DEFAULT_BROWSER_HINTS)),
    ("sources", "browser_hints", "firefox,<script>,zen", "firefox,zen"),
    ("update", "last_check_ts", -5, 0),
    ("update", "last_check_ts", 99_999_999_999, 0),
]

KEPT = [
    ("advanced", "poll_interval_ms", 250),
    ("advanced", "poll_interval_ms", 10_000),
    ("behavior", "notify_delay_ms", 0),
    ("advanced", "position_stall_s", 0),
    ("advanced", "position_stall_s", 4),
    ("advanced", "idle_grace_s", 0),
    ("history", "max_entries", 1),
    ("history", "max_entries", 100),
    ("history", "window_width", 0),
    ("history", "window_width", 900),
    ("history", "window_height", 700),
    ("privacy", "mode", "full"),
    ("privacy", "mode", "minimal"),
    ("privacy", "mode", "off"),
    ("advanced", "log_level", "DEBUG"),
    ("advanced", "log_level", "ERROR"),
    ("advanced", "language", "system"),
    ("advanced", "language", "de"),
    ("advanced", "language", "zh_CN"),
    ("sources", "bluetooth_device", ""),
    ("sources", "bluetooth_device", "AA:BB:CC:DD:EE:FF"),
    ("sources", "bluetooth_device", "aa:bb:cc:dd:ee:0f"),
    ("discord", "client_id", ""),
    ("discord", "client_id", GOOD_ID),
    ("discord", "client_id_mpris", "12345678901234567"),
    ("discord", "client_id_bluetooth", "12345678901234567890"),
    ("sources", "browser_hints", "Firefox, zen ,google chrome"),
    ("update", "last_check_ts", 0),
    ("update", "last_check_ts", 1_700_000_000),
]


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize("section,key,bad,fixed", FIXED)
def test_a_bad_value_is_replaced_with_one_warning(caplog, section, key, bad, fixed):
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config.from_dict({section: {key: bad}})
    assert getattr(getattr(cfg, section), key) == fixed
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert f"{section}.{key}" in warnings[0].getMessage()


@pytest.mark.parametrize("section,key,good", KEPT)
def test_a_good_value_is_kept_without_a_warning(caplog, section, key, good):
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config.from_dict({section: {key: good}})
    assert getattr(getattr(cfg, section), key) == good
    assert _warnings(caplog) == []


@pytest.mark.parametrize(
    "section,key,raw,tidy",
    [
        ("privacy", "mode", " Minimal ", "minimal"),
        ("advanced", "log_level", "debug", "DEBUG"),
        ("advanced", "language", "", "system"),
        ("advanced", "language", "pt", "pt"),
        ("discord", "client_id", f"  {GOOD_ID} ", GOOD_ID),
        ("sources", "bluetooth_device", " AA:BB:CC:DD:EE:FF ", "AA:BB:CC:DD:EE:FF"),
    ],
)
def test_harmless_spelling_is_tidied_quietly(caplog, section, key, raw, tidy):
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config.from_dict({section: {key: raw}})
    assert getattr(getattr(cfg, section), key) == tidy
    assert _warnings(caplog) == []


@pytest.mark.parametrize(
    "near_miss", ["1234567890123456", "12345678901234567O", "123456789012345678901"]
)
def test_a_near_miss_client_id_stays_so_settings_can_show_it(caplog, near_miss):
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config.from_dict({"discord": {"client_id": near_miss}})
    assert cfg.discord.client_id == near_miss
    [warning] = _warnings(caplog)
    assert "kept as is" in warning.getMessage()


def test_a_slightly_wrong_clock_does_not_reset_the_last_check(caplog):
    soon = int(time.time()) + 3600
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config.from_dict({"update": {"last_check_ts": soon}})
    assert cfg.update.last_check_ts == soon
    assert _warnings(caplog) == []


def test_every_language_in_settings_passes():
    assert {"en", "de", "zh_CN", "ja"} <= shipped_languages()


def test_one_bad_field_leaves_the_rest_alone():
    cfg = Config.from_dict(
        {
            "discord": {"client_id": GOOD_ID},
            "advanced": {"poll_interval_ms": 1, "log_level": "WARNING", "language": "fr"},
            "history": {"max_entries": 50, "window_width": 800},
        }
    )
    assert cfg.advanced.poll_interval_ms == 250
    assert cfg.discord.client_id == GOOD_ID
    assert cfg.advanced.log_level == "WARNING"
    assert cfg.advanced.language == "fr"
    assert cfg.history.max_entries == 50
    assert cfg.history.window_width == 800


def test_validate_copes_with_values_set_in_code(caplog):
    cfg = Config()
    cfg.advanced.poll_interval_ms = "fast"
    cfg.privacy.mode = None
    cfg.update.last_check_ts = True
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        assert cfg.validate() is cfg
    assert cfg.advanced.poll_interval_ms == 500
    assert cfg.privacy.mode == "full"
    assert cfg.update.last_check_ts == 0
    assert len(_warnings(caplog)) == 3


def test_defaults_pass_untouched(caplog):
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config().validate()
    assert cfg == Config()
    assert _warnings(caplog) == []


def test_retired_keys_are_still_ignored_quietly(caplog):
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config.from_dict({"advanced": {"cover_cache_size": 50, "poll_interval_ms": 750}})
    assert cfg.advanced.poll_interval_ms == 750
    assert _warnings(caplog) == []


def test_loading_fixes_values_without_rewriting_the_file(tmp_path):
    path = tmp_path / "config.toml"
    text = (
        "# hand-edited\n"
        "[advanced]\n"
        "poll_interval_ms = 1\n"
        'log_level = "LOUD"\n'
        "[privacy]\n"
        'mode = "everything"\n'
    )
    path.write_text(text, encoding="utf-8")
    before = path.stat().st_mtime_ns
    cfg = Config.load(path)
    assert cfg.advanced.poll_interval_ms == 250
    assert cfg.advanced.log_level == "INFO"
    assert cfg.privacy.mode == "minimal"
    assert path.read_text(encoding="utf-8") == text
    assert path.stat().st_mtime_ns == before
    assert not path.with_name("config.toml.broken").exists()


def test_a_time_zone_of_the_wrong_type_falls_back_to_the_system(caplog):
    config = Config()
    config.advanced.time_zone = 42
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        config.validate()
    assert config.advanced.time_zone == ""
    assert "time-zone name" in caplog.text


def test_a_known_time_zone_is_kept():
    config = Config()
    config.advanced.time_zone = " Europe/Berlin "
    config.validate()
    assert config.advanced.time_zone == "Europe/Berlin"


def test_an_unknown_time_zone_is_reported_and_dropped(caplog):
    config = Config()
    config.advanced.time_zone = "Nowhere/City"
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        config.validate()
    assert config.advanced.time_zone == ""
    assert "not a known time zone" in caplog.text
