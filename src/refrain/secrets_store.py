"""Credential storage that keeps secrets off plaintext disk.

The Last.fm **shared secret** and **session key** are the only real
credentials Refrain holds (the Discord client_id and the Last.fm
api_key are public application identifiers, not secrets). They are
*never* written to ``config.toml``. They go to the OS keyring instead:

1. **freedesktop Secret Service** (KWallet / GNOME Keyring) over the
   *session* D-Bus bus — encrypted at rest by the backend, unlocked
   with the user's login session. Hand-rolled on ``dbus-python``
   (already a runtime dependency) so no ``keyring``/``secretstorage``
   dependency is added.
2. **Fallback**: a `0600` (owner-only) JSON file under the config dir,
   used *only* when no Secret Service is reachable (headless box,
   no keyring daemon). Clearly separate from ``config.toml``.

Threat model / "stays on the PC": the secret is transmitted only over
the local D-Bus Unix socket (to the keyring) and, when actually
scrobbling, to Last.fm over HTTPS — which is unavoidable, that *is*
the feature. Nothing else ever reads or forwards it. The Secret
Service "plain" session means the value crosses the *local* socket
unencrypted (then the backend encrypts it at rest); negotiating a DH
session would only defend against another process already running as
the same user, which could read the keyring anyway — so "plain" is
the accepted approach (it's what libsecret/`keyring` default to).

Secrets are never logged anywhere in Refrain.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path

from refrain.paths import config_dir

log = logging.getLogger(__name__)

# Logical names of the secrets we manage. Stable — they're the keyring
# attribute + fallback-file keys.
LASTFM_SHARED_SECRET = "lastfm_shared_secret"
LASTFM_SESSION_KEY = "lastfm_session_key"

_SS_BUS = "org.freedesktop.secrets"
_SS_PATH = "/org/freedesktop/secrets"
_SS_SERVICE_IFACE = "org.freedesktop.Secret.Service"
_SS_COLLECTION_IFACE = "org.freedesktop.Secret.Collection"
_SS_ITEM_IFACE = "org.freedesktop.Secret.Item"
_PROPS_IFACE = "org.freedesktop.DBus.Properties"
_LOGIN_COLLECTION = "/org/freedesktop/secrets/collection/login"
_APP_ATTR = "io.github.Rockykln.Refrain"


def _fallback_path() -> Path:
    return config_dir() / "secrets.json"


# --------------------------------------------------------------------------- #
# Secret Service (keyring) backend                                             #
# --------------------------------------------------------------------------- #


class _KeyringUnavailable(Exception):
    """Secret Service not reachable / not usable — caller falls back."""


def _session_bus():
    import dbus  # local import: keeps this module importable without a bus

    return dbus.SessionBus()


def _keyring_available(bus) -> bool:
    try:
        import dbus

        dbus_obj = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
        return bool(dbus.Interface(dbus_obj, "org.freedesktop.DBus").NameHasOwner(_SS_BUS))
    except Exception as e:
        log.debug("Secret Service availability probe failed: %s", e)
        return False


def _open(bus):
    """Return ``(service_iface, session_path, collection_path)`` or raise
    ``_KeyringUnavailable``. The collection must be unlocked without an
    interactive prompt — at desktop login it normally already is; if a
    prompt would be required we bail to the file fallback rather than
    hang on a headless / locked session."""
    import dbus

    try:
        svc = bus.get_object(_SS_BUS, _SS_PATH)
        svc_iface = dbus.Interface(svc, _SS_SERVICE_IFACE)
        _out, session = svc_iface.OpenSession("plain", dbus.String("", variant_level=1))

        coll_path = svc_iface.ReadAlias("default")
        if str(coll_path) in ("", "/"):
            coll_path = _LOGIN_COLLECTION

        coll = bus.get_object(_SS_BUS, coll_path)
        locked = bool(dbus.Interface(coll, _PROPS_IFACE).Get(_SS_COLLECTION_IFACE, "Locked"))
        if locked:
            _unlocked, prompt = svc_iface.Unlock([coll_path])
            if str(prompt) != "/":
                # An interactive unlock dialog is required; we can't
                # drive that from here safely. Fall back to the file.
                raise _KeyringUnavailable("keyring locked, prompt required")
        return svc_iface, session, coll_path
    except _KeyringUnavailable:
        raise
    except Exception as e:
        raise _KeyringUnavailable(str(e)) from e


def _keyring_set(bus, name: str, value: str) -> bool:
    import dbus

    svc_iface, session, coll_path = _open(bus)
    coll = bus.get_object(_SS_BUS, coll_path)
    coll_iface = dbus.Interface(coll, _SS_COLLECTION_IFACE)
    attrs = dbus.Dictionary({"application": _APP_ATTR, "key": name}, signature="ss")
    props = dbus.Dictionary(
        {
            "org.freedesktop.Secret.Item.Label": dbus.String(f"Refrain — {name}", variant_level=1),
            "org.freedesktop.Secret.Item.Attributes": dbus.Dictionary(
                attrs, signature="ss", variant_level=1
            ),
        },
        signature="sv",
    )
    secret = dbus.Struct(
        (
            session,
            dbus.Array([], signature="y"),  # no parameters for "plain"
            dbus.ByteArray(value.encode("utf-8")),
            "text/plain; charset=utf8",
        ),
        signature="oayays",
    )
    coll_iface.CreateItem(props, secret, True)  # replace=True
    return True


def _keyring_get(bus, name: str) -> str | None:
    import dbus

    svc_iface, session, _coll_path = _open(bus)
    attrs = dbus.Dictionary({"application": _APP_ATTR, "key": name}, signature="ss")
    unlocked, locked = svc_iface.SearchItems(attrs)
    items = list(unlocked) or list(locked)
    if not items:
        return None
    if not list(unlocked) and list(locked):
        _u, prompt = svc_iface.Unlock(list(locked))
        if str(prompt) != "/":
            raise _KeyringUnavailable("item locked, prompt required")
    item = bus.get_object(_SS_BUS, items[0])
    secret = dbus.Interface(item, _SS_ITEM_IFACE).GetSecret(session)
    return bytes(secret[2]).decode("utf-8")


def _keyring_delete(bus, name: str) -> bool:
    import dbus

    svc_iface, _session, _coll = _open(bus)
    attrs = dbus.Dictionary({"application": _APP_ATTR, "key": name}, signature="ss")
    unlocked, locked = svc_iface.SearchItems(attrs)
    for path in list(unlocked) + list(locked):
        with contextlib.suppress(Exception):
            item = bus.get_object(_SS_BUS, path)
            dbus.Interface(item, _SS_ITEM_IFACE).Delete()
    return True


# --------------------------------------------------------------------------- #
# 0600 file fallback                                                           #
# --------------------------------------------------------------------------- #


def _file_read_all() -> dict[str, str]:
    p = _fallback_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        log.warning("Secrets fallback file unreadable (%s)", e)
        return {}


def _file_write_all(data: dict[str, str]) -> bool:
    p = _fallback_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        # Create with 0600 from the start (umask-independent) so the
        # secret is never briefly world-readable between write and chmod.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(data).encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
        os.chmod(p, 0o600)
        return True
    except OSError as e:
        log.warning("Could not persist secrets fallback file (%s)", e)
        with contextlib.suppress(OSError):
            if tmp.exists():
                tmp.unlink()
        return False


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #


class SecretStore:
    """Keyring-first, 0600-file-fallback credential store.

    Every method is failure-tolerant: a keyring hiccup degrades to the
    file (and is logged once), never raises into the app. ``bus`` is
    injectable for tests; default opens the real session bus lazily.
    """

    def __init__(self, bus=None) -> None:
        self._bus = bus
        self._bus_tried = bus is not None

    def _get_bus(self):
        if not self._bus_tried:
            self._bus_tried = True
            with contextlib.suppress(Exception):
                self._bus = _session_bus()
        return self._bus

    def keyring_ok(self) -> bool:
        bus = self._get_bus()
        return bus is not None and _keyring_available(bus)

    def set(self, name: str, value: str) -> None:
        bus = self._get_bus()
        if bus is not None and _keyring_available(bus):
            try:
                _keyring_set(bus, name, value)
                # Belt-and-braces: if a legacy plaintext copy ever
                # landed in the fallback file, drop it now that the
                # keyring holds the value.
                self._file_forget(name)
                return
            except _KeyringUnavailable as e:
                log.warning("Keyring set failed (%s) — using 0600 file fallback", e)
            except Exception:
                log.exception("Keyring set unexpectedly failed — using file fallback")
        data = _file_read_all()
        data[name] = value
        _file_write_all(data)

    def get(self, name: str) -> str | None:
        bus = self._get_bus()
        if bus is not None and _keyring_available(bus):
            try:
                val = _keyring_get(bus, name)
                if val is not None:
                    return val
            except _KeyringUnavailable as e:
                log.debug("Keyring get failed (%s) — checking file fallback", e)
            except Exception:
                log.exception("Keyring get unexpectedly failed — checking file")
        return _file_read_all().get(name) or None

    def delete(self, name: str) -> None:
        bus = self._get_bus()
        if bus is not None and _keyring_available(bus):
            with contextlib.suppress(Exception):
                _keyring_delete(bus, name)
        self._file_forget(name)

    @staticmethod
    def _file_forget(name: str) -> None:
        data = _file_read_all()
        if name in data:
            del data[name]
            _file_write_all(data)


# Process-wide default instance (real session bus, lazy).
_default = SecretStore()


def load_into(lastfm, store: SecretStore | None = None) -> None:
    """Overlay the real shared_secret / session_key onto a LastfmConfig
    from secure storage, and migrate any legacy plaintext.

    A legacy ``config.toml`` (written before secrets moved out of it)
    may still carry the values — Config.load puts them on the
    dataclass. If so we push them into secure storage immediately; the
    next Config.save() blanks the on-disk copy. Keyring/file value
    wins when present. ``store`` is injectable for tests.
    """
    store = store or _default
    try:
        stored_secret = store.get(LASTFM_SHARED_SECRET)
        stored_session = store.get(LASTFM_SESSION_KEY)

        if stored_secret is not None:
            lastfm.shared_secret = stored_secret
        elif lastfm.shared_secret:
            # Legacy plaintext from an old config.toml → migrate out.
            store.set(LASTFM_SHARED_SECRET, lastfm.shared_secret)
            log.info("Migrated Last.fm shared secret into secure storage")

        if stored_session is not None:
            lastfm.session_key = stored_session
        elif lastfm.session_key:
            store.set(LASTFM_SESSION_KEY, lastfm.session_key)
            log.info("Migrated Last.fm session key into secure storage")
    except Exception:
        log.exception("Loading Last.fm secrets failed; scrobbling may need a reconnect")


def save_from(lastfm, store: SecretStore | None = None, *, clear_missing: bool = False) -> None:
    """Persist the Last.fm secrets from a LastfmConfig into secure storage.
    Called on Settings Apply, alongside Config.save().

    An empty field is **not** treated as "delete this" unless
    ``clear_missing`` is set. The two are indistinguishable in the config
    object but mean opposite things: the user pressing Disconnect, versus
    ``load_into`` having failed to read the value back at startup. The
    latter happens whenever the keyring goes away between sessions — a
    KWallet that got disabled, a locked collection, a login keyring that
    never got unlocked — and deleting on it turned a temporary read
    failure into permanent credential loss. Callers that genuinely mean
    "forget this account" pass ``clear_missing=True``.

    ``store`` is injectable for tests."""
    store = store or _default
    try:
        if lastfm.shared_secret:
            store.set(LASTFM_SHARED_SECRET, lastfm.shared_secret)
        elif clear_missing:
            store.delete(LASTFM_SHARED_SECRET)
        else:
            log.debug("Last.fm shared secret empty; keeping the stored copy")

        if lastfm.session_key:
            store.set(LASTFM_SESSION_KEY, lastfm.session_key)
        elif clear_missing:
            store.delete(LASTFM_SESSION_KEY)
        else:
            log.debug("Last.fm session key empty; keeping the stored copy")
    except Exception:
        log.exception("Saving Last.fm secrets failed")
