"""Song notifications are off for new installs; a file from before keeps what its user had."""

from __future__ import annotations

from refrain.config import Config


def test_a_new_install_starts_without_song_notifications(xdg_tmp):
    assert Config().behavior.notifications is False
    first = Config.load()
    assert first.behavior.notifications is False
    assert "notifications = false" in (xdg_tmp["config"] / "refrain" / "config.toml").read_text()
    assert Config.load().behavior.notifications is False


def test_a_file_without_the_key_keeps_the_old_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[behavior]\nautostart = true\n\n[discord]\nclient_id = ""\n')
    loaded = Config.load(path)
    assert loaded.behavior.notifications is True
    assert loaded.behavior.autostart is True
    loaded.save(path)
    assert "notifications = true" in path.read_text()


def test_a_file_without_a_behavior_section_keeps_the_old_default():
    assert Config.from_dict({"privacy": {"mode": "full"}}).behavior.notifications is True


def test_what_the_user_chose_wins(tmp_path):
    for value in (True, False):
        path = tmp_path / f"{value}.toml"
        path.write_text(f"[behavior]\nnotifications = {str(value).lower()}\n")
        assert Config.load(path).behavior.notifications is value


def test_an_odd_behavior_value_is_left_to_the_usual_checks():
    from refrain.config import _with_legacy_defaults

    assert _with_legacy_defaults({"behavior": "nonsense"}) == {"behavior": "nonsense"}
