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
    assert {"client_id", "client_id_mpris", "client_id_bluetooth"} == set(
        DiscordConfig.__dataclass_fields__
    )
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
        "first_run_complete",
    } == set(BehaviorConfig.__dataclass_fields__)
    assert {
        "poll_interval_ms",
        "log_level",
        "cover_cache_size",
        "idle_grace_s",
        "language",
    } == set(AdvancedConfig.__dataclass_fields__)


def test_browser_hints_list_parses_csv():
    from refrain.config import SourcesConfig

    s = SourcesConfig(browser_hints="firefox, chrome ,Brave,,zen")
    assert s.browser_hints_list() == ["firefox", "chrome", "brave", "zen"]


def test_browser_hints_list_empty_when_blank():
    from refrain.config import SourcesConfig

    assert SourcesConfig(browser_hints="").browser_hints_list() == []
    assert SourcesConfig(browser_hints=" ,, ").browser_hints_list() == []


def test_serialize_escapes_newline_tab_cr():
    """Hand-edited TOML with stray newlines in a string value would
    otherwise produce broken output that tomllib rejects on next
    load, tripping the 'config unreadable, using defaults' fallback
    and silently losing every setting."""
    from refrain.config import _format_value

    assert _format_value("line1\nline2") == '"line1\\nline2"'
    assert _format_value("col\tcol") == '"col\\tcol"'
    assert _format_value("car\rret") == '"car\\rret"'
    # Round-trip through tomllib to make sure the escapes are valid.
    rendered = "key = " + _format_value("multi\nline\twith\rweird")
    parsed = tomllib.loads(rendered)
    assert parsed["key"] == "multi\nline\twith\rweird"


def test_save_cleans_tmp_on_failure(tmp_path, monkeypatch):
    """Disk full / permission denied during save must not leave a
    stale .tmp sibling next to the real config — the next save would
    work, but `ls ~/.config/refrain/` would show clutter."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[discord]\nclient_id = "old"\n', encoding="utf-8")

    cfg = Config()
    cfg.discord.client_id = "new"

    # Simulate os.replace failing (e.g., target on read-only fs).
    import refrain.config as cfg_module

    def _boom(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(cfg_module.os, "replace", _boom)
    try:
        cfg.save(cfg_path)
    except OSError:
        pass

    # The tmp must not survive the failed save.
    assert not (tmp_path / "config.toml.tmp").exists()


def test_wrong_type_value_dropped_not_fatal(caplog):
    """A hand-edited config that puts a bool where an int is expected
    (e.g. ``log_level = false`` or ``poll_interval_ms = true``) used
    to either crash on .upper() / arithmetic or be silently accepted
    and crash deeper. _coerce_value should drop the bad value with a
    warning and the rest of the section survives."""
    payload = {
        "advanced": {
            "poll_interval_ms": "750",  # numeric string — should coerce
            "log_level": False,  # bool, not str — should drop
            "cover_cache_size": 200.5,  # float, not int — should coerce to 200
            "idle_grace_s": True,  # bool, not int — should drop
        },
        "behavior": {
            "autostart": "true",  # string "true" — should coerce
            "notifications": "false",  # string "false" — should coerce
        },
    }
    with caplog.at_level("WARNING", logger="refrain.config"):
        c = Config.from_dict(payload)
    assert c.advanced.poll_interval_ms == 750  # coerced from "750"
    assert c.advanced.log_level == "INFO"  # default kept after False rejected
    assert c.advanced.cover_cache_size == 200  # truncated from 200.5
    assert c.advanced.idle_grace_s == 30  # default after True rejected
    assert c.behavior.autostart is True
    assert c.behavior.notifications is False
    # Bad values produced warnings.
    msgs = [rec.message for rec in caplog.records]
    assert any("log_level" in m for m in msgs)
    assert any("idle_grace_s" in m for m in msgs)


def test_unknown_section_keys_dropped_not_fatal(caplog):
    """Forward/backward-compat: a key the current code doesn't know
    (e.g. written by a newer Refrain that the user has downgraded from,
    or a hand-edit typo) must NOT make Config.from_dict fall back to
    defaults for the *whole* file. Only the offending key is dropped."""
    payload = {
        "discord": {
            "client_id": "123456789012345678",
            "client_id_youtube": "777",  # not a real field — user downgraded
        },
        "advanced": {
            "poll_interval_ms": 750,
            "frobnicate_level": 11,  # typo
        },
    }
    with caplog.at_level("WARNING", logger="refrain.config"):
        c = Config.from_dict(payload)
    assert c.discord.client_id == "123456789012345678"
    assert c.advanced.poll_interval_ms == 750
    # The two stray keys should produce diagnostic warnings.
    assert any("client_id_youtube" in rec.message for rec in caplog.records)
    assert any("frobnicate_level" in rec.message for rec in caplog.records)
