"""Config schema, defaults, and TOML round-trip."""

from __future__ import annotations

import tomllib

from refrain.config import (
    AdvancedConfig,
    BehaviorConfig,
    Config,
    DiscordConfig,
    PrivacyConfig,
    SourcesConfig,
    _serialize,
)


def test_defaults_are_sensible():
    c = Config()
    # discord.client_id intentionally empty by default — every user
    # registers their own Discord app and pastes the ID in settings.
    assert c.discord.client_id == ""
    assert c.sources.mpris_enabled is True
    assert c.sources.bluetooth_enabled is True
    assert c.sources.bluetooth_device == ""
    assert c.privacy.mode == "full"
    assert c.behavior.cover_art is True
    assert c.behavior.show_buttons is True
    assert c.behavior.autostart is False
    assert c.advanced.poll_interval_ms >= 250


def test_roundtrip_via_toml():
    original = Config()
    original.privacy.mode = "minimal"
    original.sources.bluetooth_device = "AA:BB:CC:DD:EE:FF"
    original.advanced.poll_interval_ms = 750

    text = _serialize(original.to_dict())
    parsed = tomllib.loads(text)
    restored = Config.from_dict(parsed)

    assert restored.privacy.mode == "minimal"
    assert restored.sources.bluetooth_device == "AA:BB:CC:DD:EE:FF"
    assert restored.advanced.poll_interval_ms == 750
    assert restored.discord.client_id == original.discord.client_id


def test_partial_dict_falls_back_to_defaults():
    """An older config missing newer sections must still load."""
    cfg = Config.from_dict({"discord": {"client_id": "999"}})
    assert cfg.discord.client_id == "999"
    assert cfg.privacy.mode == "full"  # default
    assert cfg.behavior.cover_art is True


def test_serializer_quotes_strings_and_lowercases_bools():
    text = _serialize(
        {
            "section": {"name": "hello", "flag": True, "n": 42},
        }
    )
    assert 'name = "hello"' in text
    assert "flag = true" in text
    assert "n = 42" in text


def test_serializer_escapes_backslash_and_quote():
    text = _serialize({"x": {"v": 'a\\b"c'}})
    parsed = tomllib.loads(text)
    assert parsed["x"]["v"] == 'a\\b"c'


def test_save_and_load_roundtrip(xdg_tmp):
    """Config.save() to XDG_CONFIG_HOME, then Config.load() reads it back."""
    # Re-import paths so it picks up the patched env vars
    import importlib

    import refrain.paths

    importlib.reload(refrain.paths)
    import refrain.config as cfgmod

    importlib.reload(cfgmod)

    c = cfgmod.Config()
    c.privacy.mode = "off"
    c.behavior.notifications = False
    c.save()

    loaded = cfgmod.Config.load()
    assert loaded.privacy.mode == "off"
    assert loaded.behavior.notifications is False


def test_dataclasses_have_expected_fields():
    """Schema contract for downstream serialization helpers."""
    assert {"client_id"} == set(DiscordConfig.__dataclass_fields__)
    assert {"mpris_enabled", "bluetooth_enabled", "bluetooth_device", "browser_hints"} == set(
        SourcesConfig.__dataclass_fields__
    )
    assert {"mode"} == set(PrivacyConfig.__dataclass_fields__)
    assert {
        "autostart",
        "notifications",
        "cover_art",
        "show_buttons",
        "notify_delay_ms",
    } == set(BehaviorConfig.__dataclass_fields__)
    assert {"poll_interval_ms", "log_level", "cover_cache_size"} == set(
        AdvancedConfig.__dataclass_fields__
    )


def test_browser_hints_list_parses_csv():
    from refrain.config import SourcesConfig

    s = SourcesConfig(browser_hints="firefox, chrome ,Brave,,zen")
    assert s.browser_hints_list() == ["firefox", "chrome", "brave", "zen"]


def test_browser_hints_list_empty_when_blank():
    from refrain.config import SourcesConfig

    assert SourcesConfig(browser_hints="").browser_hints_list() == []
    assert SourcesConfig(browser_hints=" ,, ").browser_hints_list() == []
