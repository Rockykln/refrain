"""Resolving a Discord Application ID to its display name.

The Client ID is the one setting a user cannot check by looking at it:
nineteen digits, and a wrong one fails silently — Refrain connects,
Discord rejects the application, and the status simply never appears.
These tests cover the three answers that distinction rests on, and the
cache that keeps Refrain from asking about the same ID all day.
"""

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
    cached_name_is_fresh,
    fetch_application_name,
    looks_like_application_id,
    refresh_application_name,
)

VALID_ID = "1425491225162809447"


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
    assert fetch_application_name("nonsense") == (UNKNOWN_ID, "")


# --------------------------------------------------------------------------- #
# fetch_application_name                                                       #
# --------------------------------------------------------------------------- #


def test_a_known_id_returns_the_name(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _respond({"id": VALID_ID, "name": "Apple Musik"}))
    assert fetch_application_name(VALID_ID) == (FOUND, "Apple Musik")


def test_a_404_means_discord_has_no_such_application(monkeypatch):
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", _raise(err))
    assert fetch_application_name(VALID_ID) == (UNKNOWN_ID, "")


def test_other_http_errors_are_not_a_verdict_on_the_id(monkeypatch):
    """A rate limit says nothing about whether the ID is right.

    Telling a user with a perfectly good ID that no such application
    exists would send them off editing a setting that was already
    correct.
    """
    for code in (429, 500, 503):
        err = urllib.error.HTTPError("u", code, "nope", {}, None)
        monkeypatch.setattr("urllib.request.urlopen", _raise(err))
        assert fetch_application_name(VALID_ID) == (UNREACHABLE, "")


def test_a_dead_network_is_unreachable_not_unknown(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _raise(OSError("no route to host")))
    assert fetch_application_name(VALID_ID) == (UNREACHABLE, "")


def test_a_nameless_application_counts_as_unknown(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _respond({"id": VALID_ID, "name": "   "}))
    assert fetch_application_name(VALID_ID) == (UNKNOWN_ID, "")


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
    """The worst outcome would be showing the old app's name on a new ID.

    That reads as confirmation, which is the exact opposite of what the
    line is for.
    """
    now = 1_000_000.0
    assert cached_name_is_fresh(VALID_ID, "9999999999999999999", now - 5, now) is False


def test_no_id_is_never_fresh():
    now = 1_000_000.0
    assert cached_name_is_fresh("", "", now - 5, now) is False


def test_a_timestamp_from_the_future_ages_out_rather_than_lasting_forever():
    now = 1_000_000.0
    assert cached_name_is_fresh(VALID_ID, VALID_ID, now + 10_000, now) is False
    assert cached_name_is_fresh(VALID_ID, VALID_ID, 0, now) is False


# --------------------------------------------------------------------------- #
# refresh_application_name — the whole job, including when not to do it        #
# --------------------------------------------------------------------------- #


@pytest.fixture
def cfg(xdg_tmp):
    """A config with the lookup deliberately switched on.

    It is opt-in, so every test that exercises the lookup has to say so
    — which is the point of the default and worth restating here.
    """
    c = Config()
    c.discord.client_id = VALID_ID
    c.discord.resolve_app_name = True
    return c


def test_the_lookup_is_off_until_asked_for(xdg_tmp, monkeypatch):
    """Out of the box, Refrain talks to no Discord server at all.

    The status goes to the local IPC socket; this is the one thing that
    would reach discord.com, so a fresh install must not do it until
    someone ticks the box.
    """
    c = Config()
    c.discord.client_id = VALID_ID
    assert c.discord.resolve_app_name is False
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application_name(c) == ""


def test_refresh_stores_the_name_and_stamps_the_time(cfg, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _respond({"name": "Apple Musik"}))
    before = time.time()
    assert refresh_application_name(cfg) == "Apple Musik"
    assert cfg.discord.app_name == "Apple Musik"
    assert cfg.discord.app_name_for_id == VALID_ID
    assert cfg.discord.app_name_checked_ts >= int(before)


def test_a_fresh_cache_is_not_re_fetched(cfg, monkeypatch):
    cfg.discord.app_name = "Apple Musik"
    cfg.discord.app_name_for_id = VALID_ID
    cfg.discord.app_name_checked_ts = int(time.time())
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application_name(cfg) == "Apple Musik"


def test_a_stale_cache_is_re_fetched(cfg, monkeypatch):
    cfg.discord.app_name = "Old Name"
    cfg.discord.app_name_for_id = VALID_ID
    cfg.discord.app_name_checked_ts = int(time.time()) - NAME_TTL_S - 1
    monkeypatch.setattr("urllib.request.urlopen", _respond({"name": "New Name"}))
    assert refresh_application_name(cfg) == "New Name"
    assert cfg.discord.app_name == "New Name"


def test_an_unreachable_discord_keeps_the_name_we_already_had(cfg, monkeypatch):
    """A flaky network must not turn a good ID into "no such application"."""
    cfg.discord.app_name = "Apple Musik"
    cfg.discord.app_name_for_id = VALID_ID
    cfg.discord.app_name_checked_ts = int(time.time()) - NAME_TTL_S - 1
    monkeypatch.setattr("urllib.request.urlopen", _raise(OSError("down")))
    assert refresh_application_name(cfg) == "Apple Musik"
    assert cfg.discord.app_name == "Apple Musik"


def test_switching_the_lookup_back_off_stops_it(cfg, monkeypatch):
    cfg.discord.resolve_app_name = False
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application_name(cfg) == ""


def test_privacy_off_stops_it_too(cfg, monkeypatch):
    """Privacy → Off means "do not talk to Discord", without exceptions."""
    cfg.privacy.mode = "off"
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application_name(cfg) == ""


def test_no_client_id_asks_nothing(cfg, monkeypatch):
    cfg.discord.client_id = ""
    monkeypatch.setattr("urllib.request.urlopen", _raise(AssertionError("should not fetch")))
    assert refresh_application_name(cfg) == ""
