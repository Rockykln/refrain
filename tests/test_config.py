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
    assert {
        "client_id",
        "client_id_mpris",
        "client_id_bluetooth",
        "all_clients",
        # Cached display name for client_id, plus which id it describes
        # and when it was last confirmed — see refrain.discord_app.
        "app_name",
        "app_name_for_id",
        "app_name_checked_ts",
        "resolve_app_name",
    } == set(DiscordConfig.__dataclass_fields__)
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
        "idle_grace_s",
        "position_stall_s",
        "language",
    } == set(AdvancedConfig.__dataclass_fields__)
    from refrain.config import LastfmConfig

    assert {
        "enabled",
        "api_key",
        "shared_secret",
        "session_key",
        "username",
        "scrobble_now_playing",
    } == set(LastfmConfig.__dataclass_fields__)


def test_browser_hints_list_parses_csv():
    from refrain.config import SourcesConfig

    s = SourcesConfig(browser_hints="firefox, chrome ,Brave,,zen")
    assert s.browser_hints_list() == ["firefox", "chrome", "brave", "zen"]


def test_browser_hints_list_empty_when_blank():
    from refrain.config import SourcesConfig

    assert SourcesConfig(browser_hints="").browser_hints_list() == []
    assert SourcesConfig(browser_hints=" ,, ").browser_hints_list() == []


def test_serialize_escapes_newline_tab_cr():
    """Control characters are escaped, or tomllib rejects the file and every setting is lost."""
    from refrain.config import _format_value

    assert _format_value("line1\nline2") == '"line1\\nline2"'
    assert _format_value("col\tcol") == '"col\\tcol"'
    assert _format_value("car\rret") == '"car\\rret"'
    # Round-trip through tomllib to make sure the escapes are valid.
    rendered = "key = " + _format_value("multi\nline\twith\rweird")
    parsed = tomllib.loads(rendered)
    assert parsed["key"] == "multi\nline\twith\rweird"


def test_save_cleans_tmp_on_failure(tmp_path, monkeypatch):
    """A failed save leaves no stale .tmp file next to the config."""
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
    """A value of the wrong type is coerced or dropped; the rest of the section survives."""
    payload = {
        "advanced": {
            "poll_interval_ms": "750",  # numeric string — should coerce
            "log_level": False,  # bool, not str — should drop
            "position_stall_s": 5.5,  # float, not int — should coerce to 5
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
    assert c.advanced.position_stall_s == 5  # truncated from 5.5
    assert c.advanced.idle_grace_s == 30  # default after True rejected
    assert c.behavior.autostart is True
    assert c.behavior.notifications is False
    # Bad values produced warnings.
    msgs = [rec.message for rec in caplog.records]
    assert any("log_level" in m for m in msgs)
    assert any("idle_grace_s" in m for m in msgs)


def test_the_retired_cover_cache_size_is_ignored_quietly(caplog):
    """A config from an older version with cover_cache_size loads without a warning."""
    with caplog.at_level("WARNING", logger="refrain.config"):
        c = Config.from_dict({"advanced": {"cover_cache_size": 200, "poll_interval_ms": 750}})
    assert c.advanced.poll_interval_ms == 750
    assert not hasattr(c.advanced, "cover_cache_size")
    assert not caplog.records


def test_unknown_section_keys_dropped_not_fatal(caplog):
    """An unknown key is dropped on its own instead of resetting the whole file."""
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


def test_an_unreadable_config_is_kept_not_overwritten(xdg_tmp):
    from refrain.paths import config_path

    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text('[discord]\nclient_id = "1234567890123456789"\n[advanced\n', encoding="utf-8")
    cfg = Config.load()
    cfg.save()
    kept = path.with_name("config.toml.broken")
    assert "1234567890123456789" in kept.read_text(encoding="utf-8")
    assert Config.load().discord.client_id == ""


def test_an_endless_number_drops_only_that_value(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[discord]\nclient_id = "1234567890123456789"\n[advanced]\npoll_interval_ms = inf\n',
        encoding="utf-8",
    )
    cfg = Config.load(path)
    assert cfg.discord.client_id == "1234567890123456789"
    assert cfg.advanced.poll_interval_ms == Config().advanced.poll_interval_ms


def test_control_characters_survive_a_save(tmp_path):
    path = tmp_path / "config.toml"
    cfg = Config()
    cfg.discord.app_name = "Apple\x1b Music\x00\x7f"
    cfg.save(path)
    assert Config.load(path).discord.app_name == "Apple\x1b Music\x00\x7f"


def test_saves_from_two_threads_do_not_trip_over_the_shared_tmp_file(tmp_path, monkeypatch):
    import threading

    import refrain.config as cfg_module

    path = tmp_path / "config.toml"
    real_replace = cfg_module.os.replace
    first_in_replace = threading.Event()
    second_done = threading.Event()
    calls = []

    def slow_first_replace(src, dst):
        calls.append(src)
        if len(calls) == 1:
            first_in_replace.set()
            second_done.wait(0.5)
        real_replace(src, dst)

    monkeypatch.setattr(cfg_module.os, "replace", slow_first_replace)
    errors = []

    def save(app_name, done=None):
        cfg = Config()
        cfg.discord.app_name = app_name
        try:
            cfg.save(path)
        except Exception as e:
            errors.append(e)
        if done is not None:
            done.set()

    gui = threading.Thread(target=save, args=("GUI",))
    gui.start()
    assert first_in_replace.wait(2)
    worker = threading.Thread(target=save, args=("Worker", second_done))
    worker.start()
    gui.join(3)
    worker.join(3)

    assert errors == []
    assert Config.load(path).discord.app_name == "Worker"
