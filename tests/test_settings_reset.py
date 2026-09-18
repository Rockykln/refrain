"""A settings Reset keeps the Discord IDs, the Last.fm account and the finished welcome wizard.
The wizard is a record that setup happened, not a setting."""

from __future__ import annotations

from refrain.config import Config
from refrain.ui.settings_window import reset_to_defaults


def _configured() -> Config:
    c = Config()
    c.discord.client_id = "111"
    c.discord.client_id_mpris = "222"
    c.discord.client_id_bluetooth = "333"
    c.lastfm.api_key = "AK"
    c.lastfm.shared_secret = "SS"
    c.lastfm.session_key = "SK"
    c.lastfm.username = "someone"
    c.behavior.first_run_complete = True
    c.behavior.notifications = False
    c.advanced.poll_interval_ms = 1500
    c.privacy.mode = "off"
    return c


def test_discord_identity_survives():
    out = reset_to_defaults(_configured())
    assert (
        out.discord.client_id,
        out.discord.client_id_mpris,
        out.discord.client_id_bluetooth,
    ) == (
        "111",
        "222",
        "333",
    )


def test_lastfm_account_survives():
    out = reset_to_defaults(_configured())
    assert (out.lastfm.api_key, out.lastfm.shared_secret) == ("AK", "SS")
    assert (out.lastfm.session_key, out.lastfm.username) == ("SK", "someone")


def test_the_welcome_wizard_does_not_come_back():
    assert reset_to_defaults(_configured()).behavior.first_run_complete is True


def test_a_user_who_never_finished_setup_still_gets_the_wizard():
    c = _configured()
    c.behavior.first_run_complete = False
    assert reset_to_defaults(c).behavior.first_run_complete is False


def test_everything_else_really_does_go_back_to_defaults():
    out = reset_to_defaults(_configured())
    default = Config()
    assert out.behavior.notifications == default.behavior.notifications
    assert out.advanced.poll_interval_ms == default.advanced.poll_interval_ms
    assert out.privacy.mode == default.privacy.mode


# --------------------------------------------------------------------------- #
# The line beside the Client ID                                                #
# --------------------------------------------------------------------------- #


def test_an_empty_client_id_says_nothing():
    """An empty field is the default, not a mistake."""
    from refrain.ui.settings_window import application_name_status

    assert application_name_status("", "", "") == ("", False)
    assert application_name_status("   ", "", "") == ("", False)


def test_a_confirmed_name_is_shown_and_marked_good():
    from refrain.discord_app import FOUND
    from refrain.ui.settings_window import application_name_status

    text, ok = application_name_status("1234567890123456789", FOUND, "Apple Music")
    assert "Apple Music" in text
    assert ok is True


def test_the_three_unhappy_answers_are_distinguishable():
    """A malformed or rejected ID is the user's to fix; an unreachable Discord is not."""
    from refrain.discord_app import UNKNOWN_ID, UNREACHABLE
    from refrain.ui.settings_window import application_name_status

    malformed, _ = application_name_status("123", "", "")
    unknown, _ = application_name_status("1234567890123456789", UNKNOWN_ID, "")
    unreachable, _ = application_name_status("1234567890123456789", UNREACHABLE, "")

    assert len({malformed, unknown, unreachable}) == 3
    assert all(t for t in (malformed, unknown, unreachable))
    for text, _ in (
        application_name_status("123", "", ""),
        application_name_status("1234567890123456789", UNKNOWN_ID, ""),
        application_name_status("1234567890123456789", UNREACHABLE, ""),
    ):
        assert text  # never silent about a problem the user can see
