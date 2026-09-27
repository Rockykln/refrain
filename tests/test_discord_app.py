"""Resolving a Discord Application ID to its display name, and caching the answer.
A wrong ID fails silently in Discord, so the name is the user's only check."""

from __future__ import annotations

import io
import json
import time
import urllib.error

import pytest

from refrain.config import Config
from refrain.discord_app import (
    FOUND,
    NAME_TTL_S,
    UNKNOWN_ID,
    UNREACHABLE,
    application_icon_url,
    cached_name_is_fresh,
    fetch_application,
    looks_like_application_id,
    refresh_application,
    remember_application_name,
)

VALID_ID = "1234567890123456789"


def _respond(payload: dict):
    def fake_urlopen(req, timeout=None):
        body = io.BytesIO(json.dumps(payload).encode())
        body.__enter__ = lambda: body
        body.__exit__ = lambda *a: None
        return body

    return fake_urlopen


def _raise(exc):
    def fake_urlopen(req, timeout=None):
        raise exc

    return fake_urlopen


# --------------------------------------------------------------------------- #
# looks_like_application_id — the checks worth making without the network      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,ok",
    [
        (VALID_ID, True),
        ("12345678901234567", True),  # 17 digits, the low end
        ("12345678901234567890", True),  # 20, the high end
        ("  " + VALID_ID + " ", True),  # pasted with whitespace
        ("1234567890123456", False),  # 16 — too short
        ("123456789012345678901", False),  # 21 — too long
        ("", False),
        ("not-an-id", False),
        ("1234567890123456789a", False),
    ],
)
def test_obvious_non_ids_cost_no_request(value, ok):
    assert looks_like_application_id(value) is ok


def test_a_local_reject_never_reaches_the_network(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _raise(AssertionError("should not have been called"))
    )
    assert fetch_application("nonsense") == (UNKNOWN_ID, "", "")


# --------------------------------------------------------------------------- #
# fetch_application                                                            #
# --------------------------------------------------------------------------- #


def test_a_known_id_returns_the_name(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _respond({"id": VALID_ID, "name": "Apple Musik"}))
    assert fetch_application(VALID_ID) == (FOUND, "Apple Musik", "")


def test_a_404_means_discord_has_no_such_application(monkeypatch):
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", _raise(err))
    assert fetch_application(VALID_ID) == (UNKNOWN_ID, "", "")


def test_other_http_errors_are_not_a_verdict_on_the_id(monkeypatch):
    """A rate limit or other HTTP error says nothing about whether the ID is right."""
    for code in (429, 500, 503):
        err = urllib.error.HTTPError("u", code, "nope", {}, None)
        monkeypatch.setattr("urllib.request.urlopen", _raise(err))
        assert fetch_application(VALID_ID) == (UNREACHABLE, "", "")


def test_a_dead_network_is_unreachable_not_unknown(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _raise(OSError("no route to host")))
    assert fetch_application(VALID_ID) == (UNREACHABLE, "", "")


def test_a_nameless_application_counts_as_unknown(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _respond({"id": VALID_ID, "name": "   "}))
    assert fetch_application(VALID_ID) == (UNKNOWN_ID, "", "")


# --------------------------------------------------------------------------- #
# cached_name_is_fresh                                                         #
# --------------------------------------------------------------------------- #


def test_a_recent_check_of_the_same_id_is_fresh():
    now = 1_000_000.0
    assert cached_name_is_fresh(VALID_ID, VALID_ID, now - 60, now) is True


def test_the_cache_ages_out():
    now = 1_000_000.0
    assert cached_name_is_fresh(VALID_ID, VALID_ID, now - NAME_TTL_S - 1, now) is False


def test_a_name_cached_for_a_different_id_is_never_fresh():
    """The old app's name on a new ID would read as confirmation."""
    now = 1_000_000.0
    assert cached_name_is_fresh(VALID_ID, "1234567890123456780", now - 5, now) is False


def test_no_id_is_never_fresh():
    now = 1_000_000.0
    assert cached_name_is_fresh("", "", now - 5, now) is False


def test_a_timestamp_from_the_future_ages_out_rather_than_lasting_forever():
    now = 1_000_000.0
    assert cached_name_is_fresh(VALID_ID, VALID_ID, now + 10_000, now) is False
    assert cached_name_is_fresh(VALID_ID, VALID_ID, 0, now) is False


# --------------------------------------------------------------------------- #
# refresh_application — the whole job, including when not to do it            #
# --------------------------------------------------------------------------- #


@pytest.fixture
def cfg(xdg_tmp):
    """A config with the opt-in lookup switched on."""
    c = Config()
    c.discord.client_id = VALID_ID
    c.discord.resolve_app_name = True
    return c


def test_the_lookup_is_off_until_asked_for(xdg_tmp, monkeypatch):
    """A fresh install never contacts discord.com; the status only uses the local IPC socket."""
    c = Config()
    c.discord.client_id = VALID_ID
    assert c.discord.resolve_app_name is False
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application(c) == ""


def test_refresh_stores_the_name_and_stamps_the_time(cfg, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _respond({"name": "Apple Musik"}))
    before = time.time()
    assert refresh_application(cfg) == "Apple Musik"
    assert cfg.discord.app_name == "Apple Musik"
    assert cfg.discord.app_name_for_id == VALID_ID
    assert cfg.discord.app_name_checked_ts >= int(before)


def test_a_fresh_cache_is_not_re_fetched(cfg, monkeypatch):
    cfg.discord.app_name = "Apple Musik"
    cfg.discord.app_name_for_id = VALID_ID
    cfg.discord.app_icon_for_id = VALID_ID
    cfg.discord.app_name_checked_ts = int(time.time())
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application(cfg) == "Apple Musik"


def test_a_stale_cache_is_re_fetched(cfg, monkeypatch):
    cfg.discord.app_name = "Old Name"
    cfg.discord.app_name_for_id = VALID_ID
    cfg.discord.app_name_checked_ts = int(time.time()) - NAME_TTL_S - 1
    monkeypatch.setattr("urllib.request.urlopen", _respond({"name": "New Name"}))
    assert refresh_application(cfg) == "New Name"
    assert cfg.discord.app_name == "New Name"


def test_an_unreachable_discord_keeps_the_name_we_already_had(cfg, monkeypatch):
    """A flaky network must not turn a good ID into "no such application"."""
    cfg.discord.app_name = "Apple Musik"
    cfg.discord.app_name_for_id = VALID_ID
    cfg.discord.app_name_checked_ts = int(time.time()) - NAME_TTL_S - 1
    monkeypatch.setattr("urllib.request.urlopen", _raise(OSError("down")))
    assert refresh_application(cfg) == "Apple Musik"
    assert cfg.discord.app_name == "Apple Musik"


def test_switching_the_lookup_back_off_stops_it(cfg, monkeypatch):
    cfg.discord.resolve_app_name = False
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application(cfg) == ""


def test_privacy_off_stops_it_too(cfg, monkeypatch):
    """Privacy → Off means "do not talk to Discord", without exceptions."""
    cfg.privacy.mode = "off"
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application(cfg) == ""


def test_no_client_id_asks_nothing(cfg, monkeypatch):
    cfg.discord.client_id = ""
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application(cfg) == ""


# --------------------------------------------------------------------------- #
# The application's icon — same request, same cache, its own ID                #
# --------------------------------------------------------------------------- #


def test_the_answer_carries_the_icon_hash(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _respond({"name": "Apple Musik", "icon": "abc123"})
    )
    assert fetch_application(VALID_ID) == (FOUND, "Apple Musik", "abc123")


def test_an_application_without_an_icon_reports_none(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _respond({"name": "Apple Musik", "icon": None}))
    assert fetch_application(VALID_ID) == (FOUND, "Apple Musik", "")


@pytest.mark.parametrize(
    "client_id,icon,expected",
    [
        (VALID_ID, "abc123", f"https://cdn.discordapp.com/app-icons/{VALID_ID}/abc123.png"),
        (VALID_ID, "", ""),  # the application has no icon
        ("", "abc123", ""),  # no application to address it under
    ],
)
def test_the_icon_address_needs_both_halves(client_id, icon, expected):
    assert application_icon_url(client_id, icon) == expected


def test_refresh_stores_the_icon_beside_the_name(cfg, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _respond({"name": "Apple Musik", "icon": "abc123"})
    )
    refresh_application(cfg)
    assert cfg.discord.app_icon == "abc123"
    assert cfg.discord.app_icon_for_id == VALID_ID


def test_a_config_from_before_the_icon_existed_asks_once(cfg, monkeypatch):
    """Upgrading leaves a fresh name and no icon; that has to be worth one request."""
    cfg.discord.app_name = "Apple Musik"
    cfg.discord.app_name_for_id = VALID_ID
    cfg.discord.app_name_checked_ts = int(time.time())
    monkeypatch.setattr(
        "urllib.request.urlopen", _respond({"name": "Apple Musik", "icon": "abc123"})
    )
    refresh_application(cfg)
    assert cfg.discord.app_icon_for_id == VALID_ID


def test_looking_the_name_up_alone_leaves_the_cached_icon_alone(xdg_tmp):
    """Settings resolves names, not icons; it must not blank one it never asked for."""
    c = Config()
    c.discord.app_icon, c.discord.app_icon_for_id = "abc123", VALID_ID
    remember_application_name(c, VALID_ID, "Apple Musik")
    assert c.discord.app_icon == "abc123"
