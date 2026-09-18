"""Credential store failure paths: a broken keyring or file never loses or leaks a secret."""

from __future__ import annotations

import json
import logging
import os

import pytest

from refrain import secrets_store
from refrain.config import LastfmConfig
from refrain.secrets_store import (
    LASTFM_SESSION_KEY,
    LASTFM_SHARED_SECRET,
    SecretStore,
    load_into,
    save_from,
)
from tests.test_secrets_store import _SecretService


@pytest.fixture
def secrets_file(xdg_tmp):
    return xdg_tmp["config"] / "refrain" / "secrets.json"


@pytest.fixture
def service(xdg_tmp, monkeypatch):
    import dbus

    monkeypatch.setattr(dbus, "Interface", lambda obj, _iface: obj)
    return _SecretService()


class _NoDefaultAlias(_SecretService):
    def ReadAlias(self, _name):  # noqa: N802
        return "/"

    def get_object(self, bus, path, **kw):
        self.opened = getattr(self, "opened", [])
        self.opened.append(str(path))
        return super().get_object(bus, path, **kw)


class _BrokenService(_SecretService):
    def OpenSession(self, _alg, _input):  # noqa: N802
        raise RuntimeError("org.freedesktop.DBus.Error.ServiceUnknown")


class _ExplodingItems(_SecretService):
    def CreateItem(self, props, secret, replace):  # noqa: N802
        raise ValueError("unexpected reply")

    def SearchItems(self, attrs):  # noqa: N802
        raise ValueError("unexpected reply")


def test_without_a_session_bus_the_store_uses_the_file(secrets_file):
    store = SecretStore()
    store.set(LASTFM_SESSION_KEY, "sk-12345")
    assert store.keyring_ok() is False
    assert json.loads(secrets_file.read_text()) == {LASTFM_SESSION_KEY: "sk-12345"}
    assert store.get(LASTFM_SESSION_KEY) == "sk-12345"


def test_a_failing_availability_probe_means_no_keyring(secrets_file):
    class _DeadBus:
        def get_object(self, *_a, **_kw):
            raise RuntimeError("bus gone")

    store = SecretStore(bus=_DeadBus())
    assert store.keyring_ok() is False
    store.set(LASTFM_SHARED_SECRET, "shared-12345")
    assert store.get(LASTFM_SHARED_SECRET) == "shared-12345"


def test_a_missing_default_alias_uses_the_login_collection(xdg_tmp, monkeypatch):
    import dbus

    monkeypatch.setattr(dbus, "Interface", lambda obj, _iface: obj)
    svc = _NoDefaultAlias()
    store = SecretStore(bus=svc)
    store.set(LASTFM_SESSION_KEY, "sk-12345")
    assert secrets_store._LOGIN_COLLECTION in svc.opened
    assert store.get(LASTFM_SESSION_KEY) == "sk-12345"


def test_a_keyring_error_falls_back_to_the_file(xdg_tmp, monkeypatch, secrets_file, caplog):
    import dbus

    monkeypatch.setattr(dbus, "Interface", lambda obj, _iface: obj)
    store = SecretStore(bus=_BrokenService())
    with caplog.at_level(logging.WARNING, logger="refrain.secrets_store"):
        store.set(LASTFM_SESSION_KEY, "sk-12345")
    assert json.loads(secrets_file.read_text()) == {LASTFM_SESSION_KEY: "sk-12345"}
    assert store.get(LASTFM_SESSION_KEY) == "sk-12345"
    assert "sk-12345" not in caplog.text


def test_unexpected_keyring_failures_never_raise(xdg_tmp, monkeypatch, secrets_file, caplog):
    import dbus

    monkeypatch.setattr(dbus, "Interface", lambda obj, _iface: obj)
    store = SecretStore(bus=_ExplodingItems())
    with caplog.at_level(logging.ERROR, logger="refrain.secrets_store"):
        store.set(LASTFM_SHARED_SECRET, "shared-12345")
        assert store.get(LASTFM_SHARED_SECRET) == "shared-12345"
        store.delete(LASTFM_SHARED_SECRET)
    assert store.get(LASTFM_SHARED_SECRET) is None
    assert "shared-12345" not in caplog.text
    assert json.loads(secrets_file.read_text()) == {}


def test_a_silently_unlockable_keyring_still_serves_the_secret(service, secrets_file):
    store = SecretStore(bus=service)
    store.set(LASTFM_SESSION_KEY, "sk-12345")
    service.locked, service.prompt = True, "/"
    assert store.get(LASTFM_SESSION_KEY) == "sk-12345"
    assert not secrets_file.exists()


def test_a_locked_item_that_needs_a_prompt_reads_the_file_copy(service, secrets_file):
    store = SecretStore(bus=service)
    store.set(LASTFM_SESSION_KEY, "sk-keyring")
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text(json.dumps({LASTFM_SESSION_KEY: "sk-file"}))
    calls = {"n": 0}

    def unlock(paths):
        calls["n"] += 1
        return (paths, "/" if calls["n"] == 1 else "/prompt/1")

    service.locked = True
    service.Unlock = unlock
    assert store.get(LASTFM_SESSION_KEY) == "sk-file"


@pytest.fixture
def file_store_mode(xdg_tmp, monkeypatch):
    monkeypatch.setattr(secrets_store, "_keyring_available", lambda _bus: False)


@pytest.mark.parametrize("content", ["{not json", "[1, 2, 3]", ""])
def test_a_damaged_secrets_file_reads_as_empty(file_store_mode, secrets_file, content):
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text(content)
    store = SecretStore(bus=object())
    assert store.get(LASTFM_SESSION_KEY) is None
    store.set(LASTFM_SESSION_KEY, "sk-12345")
    assert json.loads(secrets_file.read_text()) == {LASTFM_SESSION_KEY: "sk-12345"}


def test_a_failed_write_leaves_no_temp_file_behind(file_store_mode, secrets_file, monkeypatch):
    secrets_file.parent.mkdir(parents=True)
    secrets_file.write_text(json.dumps({LASTFM_SESSION_KEY: "sk-old"}))

    def refuse(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(secrets_store.os, "replace", refuse)
    assert secrets_store._file_write_all({LASTFM_SESSION_KEY: "sk-new"}) is False
    assert not secrets_file.with_suffix(".tmp").exists()
    assert json.loads(secrets_file.read_text()) == {LASTFM_SESSION_KEY: "sk-old"}
    assert sorted(os.listdir(secrets_file.parent)) == ["secrets.json"]


class _RaisingStore:
    def get(self, *_args):
        raise RuntimeError("store broken")

    set = delete = get


def test_load_into_keeps_the_config_when_the_store_breaks(caplog):
    cfg = LastfmConfig(shared_secret="legacy-12345", session_key="sk-12345")
    with caplog.at_level(logging.ERROR, logger="refrain.secrets_store"):
        load_into(cfg, store=_RaisingStore())
    assert cfg.shared_secret == "legacy-12345"
    assert cfg.session_key == "sk-12345"
    assert "Loading Last.fm secrets failed" in caplog.text


def test_save_from_does_not_raise_when_the_store_breaks(caplog):
    cfg = LastfmConfig(shared_secret="shared-12345", session_key="sk-12345")
    with caplog.at_level(logging.ERROR, logger="refrain.secrets_store"):
        save_from(cfg, store=_RaisingStore(), clear_missing=True)
    assert "Saving Last.fm secrets failed" in caplog.text
    assert "sk-12345" not in caplog.text
