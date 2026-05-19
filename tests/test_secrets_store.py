"""Credential storage: keyring-first, 0600-file fallback.

Hermetic — the keyring path is forced off (no real KWallet / D-Bus
touched) so these exercise the fallback + the config/overlay logic.
The live Secret Service path is verified manually, not in CI (CI has
no D-Bus, and touching the user's real keyring in tests is a side
effect).
"""

from __future__ import annotations

import os
import stat

import pytest

from refrain import secrets_store
from refrain.config import Config, LastfmConfig
from refrain.secrets_store import (
    LASTFM_SESSION_KEY,
    LASTFM_SHARED_SECRET,
    SecretStore,
    load_into,
    save_from,
)


@pytest.fixture
def file_store(xdg_tmp, monkeypatch):
    """A SecretStore with the keyring forced unavailable → file mode,
    writing into the isolated XDG config dir."""
    monkeypatch.setattr(secrets_store, "_keyring_available", lambda _bus: False)
    return SecretStore(bus=object())  # dummy bus, never actually used


def test_file_roundtrip_and_0600(file_store, xdg_tmp):
    file_store.set(LASTFM_SHARED_SECRET, "s3cr3t")
    f = xdg_tmp["config"] / "refrain" / "secrets.json"
    assert f.exists()
    mode = stat.S_IMODE(os.stat(f).st_mode)
    assert mode == 0o600, oct(mode)  # owner-only, never world-readable
    assert file_store.get(LASTFM_SHARED_SECRET) == "s3cr3t"


def test_get_unknown_is_none(file_store):
    assert file_store.get("nope") is None


def test_delete_removes_key(file_store):
    file_store.set(LASTFM_SESSION_KEY, "tok")
    assert file_store.get(LASTFM_SESSION_KEY) == "tok"
    file_store.delete(LASTFM_SESSION_KEY)
    assert file_store.get(LASTFM_SESSION_KEY) is None


def test_keyring_ok_false_in_file_mode(file_store):
    assert file_store.keyring_ok() is False


def test_load_into_overlays_from_store(file_store):
    file_store.set(LASTFM_SHARED_SECRET, "SS")
    file_store.set(LASTFM_SESSION_KEY, "SK")
    cfg = LastfmConfig()
    load_into(cfg, store=file_store)
    assert cfg.shared_secret == "SS"
    assert cfg.session_key == "SK"


def test_load_into_migrates_legacy_plaintext(file_store):
    # Nothing in the store yet, but a legacy config.toml left plaintext
    # on the dataclass → it must be migrated into secure storage.
    cfg = LastfmConfig(shared_secret="legacy-secret", session_key="legacy-sk")
    load_into(cfg, store=file_store)
    assert file_store.get(LASTFM_SHARED_SECRET) == "legacy-secret"
    assert file_store.get(LASTFM_SESSION_KEY) == "legacy-sk"


def test_save_from_persists_and_clears(file_store):
    cfg = LastfmConfig(shared_secret="a", session_key="b")
    save_from(cfg, store=file_store)
    assert file_store.get(LASTFM_SHARED_SECRET) == "a"
    assert file_store.get(LASTFM_SESSION_KEY) == "b"
    # Emptying the fields (disconnect) must wipe them from storage.
    cfg.shared_secret = ""
    cfg.session_key = ""
    save_from(cfg, store=file_store)
    assert file_store.get(LASTFM_SHARED_SECRET) is None
    assert file_store.get(LASTFM_SESSION_KEY) is None


def test_config_to_dict_never_emits_secrets():
    c = Config()
    c.lastfm.shared_secret = "TOP-SECRET"
    c.lastfm.session_key = "SESSION-TOKEN"
    lastfm = c.to_dict()["lastfm"]
    assert lastfm["shared_secret"] == ""
    assert lastfm["session_key"] == ""
    # …and they must not appear anywhere in the serialised TOML.
    from refrain.config import _serialize

    text = _serialize(c.to_dict())
    assert "TOP-SECRET" not in text
    assert "SESSION-TOKEN" not in text


def test_config_save_blanks_secrets_and_is_0600(tmp_path):
    p = tmp_path / "config.toml"
    c = Config()
    c.lastfm.api_key = "public-api-key"
    c.lastfm.shared_secret = "MUST-NOT-PERSIST"
    c.lastfm.session_key = "MUST-NOT-PERSIST-2"
    c.save(p)
    text = p.read_text(encoding="utf-8")
    assert "MUST-NOT-PERSIST" not in text
    assert 'api_key = "public-api-key"' in text  # non-secret stays
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600, oct(mode)


def test_legacy_plaintext_config_is_scrubbed_on_next_save(tmp_path):
    # An old build wrote the secret into config.toml. Loading then
    # saving must blank that line (the value lives in the keyring now).
    p = tmp_path / "config.toml"
    p.write_text(
        '[lastfm]\nenabled = true\napi_key = "k"\n'
        'shared_secret = "OLD-PLAINTEXT"\nsession_key = "OLD-SK"\n',
        encoding="utf-8",
    )
    c = Config.load(p)
    assert c.lastfm.shared_secret == "OLD-PLAINTEXT"  # still read into memory
    c.save(p)
    text = p.read_text(encoding="utf-8")
    assert "OLD-PLAINTEXT" not in text
    assert "OLD-SK" not in text
    assert 'shared_secret = ""' in text
