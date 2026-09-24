"""Config edge cases: raw type coercion and save/load failure paths not covered elsewhere."""

from __future__ import annotations

import logging

import pytest

from refrain.config import Config, _coerce_value

# --------------------------------------------------------------------------- #
# _coerce_value — every branch, called directly rather than through a section  #
# --------------------------------------------------------------------------- #


def test_a_bool_field_accepts_a_truthy_or_falsy_number():
    assert _coerce_value(bool, "flag", 1) is True
    assert _coerce_value(bool, "flag", 0) is False
    assert _coerce_value(bool, "flag", 2.5) is True


def test_a_bool_field_rejects_a_value_with_no_sane_meaning():
    with pytest.raises(TypeError):
        _coerce_value(bool, "flag", [1, 2])


def test_an_int_field_rejects_a_non_numeric_string():
    with pytest.raises(TypeError):
        _coerce_value(int, "count", "not-a-number")


def test_a_str_field_accepts_a_hand_edited_number():
    assert _coerce_value(str, "client_id", 1234567890123456789) == "1234567890123456789"


def test_a_str_field_rejects_a_value_with_no_sane_meaning():
    with pytest.raises(TypeError):
        _coerce_value(str, "client_id", [1, 2, 3])


def test_an_unannotated_field_is_accepted_as_is():
    sentinel = object()
    assert _coerce_value(None, "anything", sentinel) is sentinel


# --------------------------------------------------------------------------- #
# Those coercions as they play out through a real section                      #
# --------------------------------------------------------------------------- #


def test_a_numeric_bool_from_a_hand_edit_is_coerced(caplog):
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config.from_dict({"behavior": {"notifications": 1}})
    assert cfg.behavior.notifications is True
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_an_unusable_bool_value_is_dropped_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config.from_dict({"behavior": {"notifications": [1, 2]}})
    assert cfg.behavior.notifications is False  # default kept
    assert any("notifications" in r.getMessage() for r in caplog.records)


def test_a_junk_string_int_is_dropped_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = Config.from_dict({"advanced": {"poll_interval_ms": "fast"}})
    assert cfg.advanced.poll_interval_ms == 500  # default kept
    assert any("poll_interval_ms" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# Config.load — the broken-file rename itself failing                         #
# --------------------------------------------------------------------------- #


def test_when_the_broken_copy_cannot_be_kept_the_original_is_named_instead(
    tmp_path, monkeypatch, caplog
):
    """A config so mangled it can't even be parsed must never disappear silently."""
    path = tmp_path / "config.toml"
    path.write_text("not valid toml [[[", encoding="utf-8")

    import refrain.config as cfg_module

    def fail_replace(self, _target):
        raise OSError("cross-device link")

    monkeypatch.setattr(cfg_module.Path, "replace", fail_replace)
    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        cfg = cfg_module.Config.load(path)

    assert cfg == cfg_module.Config()  # falls back to defaults
    assert path.exists()  # never moved, and not lost
    assert path.read_text(encoding="utf-8") == "not valid toml [[["
    assert any(str(path) in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# Config.save — the comment-preserving merge failing                          #
# --------------------------------------------------------------------------- #


def test_a_config_that_turns_unreadable_mid_save_still_saves(tmp_path, monkeypatch, caplog):
    path = tmp_path / "config.toml"
    path.write_text('[discord]\nclient_id = "old"\n', encoding="utf-8")
    cfg = Config()
    cfg.discord.client_id = "1234567890123456789"

    import refrain.config as cfg_module

    def boom(self, *_a, **_kw):
        raise OSError("read failed")

    with caplog.at_level(logging.WARNING, logger="refrain.config"):
        with monkeypatch.context() as m:
            m.setattr(cfg_module.Path, "read_text", boom)
            cfg.save(path)

    text = path.read_text(encoding="utf-8")
    assert 'client_id = "1234567890123456789"' in text
    assert any("comment-preserving save" in r.getMessage() for r in caplog.records)


def test_a_merge_failure_falls_back_to_a_clean_rewrite(tmp_path, monkeypatch, caplog):
    path = tmp_path / "config.toml"
    path.write_text('[discord]\nclient_id = "old"\n# a hand-written note\n', encoding="utf-8")
    cfg = Config()
    cfg.discord.client_id = "1234567890123456789"

    import refrain.config as cfg_module

    def boom(_existing, _data):
        raise ValueError("corrupt merge state")

    monkeypatch.setattr(cfg_module, "_merge_into_existing", boom)
    with caplog.at_level(logging.ERROR, logger="refrain.config"):
        cfg.save(path)

    text = path.read_text(encoding="utf-8")
    assert "# a hand-written note" not in text, "rewritten from scratch, so the comment is gone"
    assert 'client_id = "1234567890123456789"' in text
    assert any("merge failed" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# Config.load — the very first save failing                                   #
# --------------------------------------------------------------------------- #


def test_a_first_start_without_a_writable_config_dir_still_starts(tmp_path, caplog):
    """There is no window yet at this point, so a raised error would be a silent exit."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger="refrain.config"):
            cfg = Config.load(locked / "config.toml")
        assert cfg == Config()
        assert not (locked / "config.toml").exists()
        assert any("defaults" in r.getMessage() for r in caplog.records)
    finally:
        locked.chmod(0o700)
