"""Fixtures for the MPRIS D-Bus integration tests: a real dbus-daemon, real dbus-python,
no mocks. Skips cleanly wherever dbus-python or the D-Bus session tools aren't installed."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

dbus = pytest.importorskip("dbus", reason="dbus-python not installed")
pytest.importorskip("PySide6.QtCore", reason="PySide6 not installed")

import refrain.sources.mpris as _mpris_module  # noqa: E402

HERE = Path(__file__).resolve().parent
FAKE_PLAYER = HERE / "fake_mpris_player.py"
_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"


@pytest.fixture(autouse=True)
def _real_dbus_in_mpris_module():
    """Guard against cross-file pollution: test_mpris_dispatch.py swaps `dbus`
    for a mock at import time and never rebinds refrain.sources.mpris's own
    reference to it afterwards, since it keeps using its own reloaded copy —
    which corrupts the module for the rest of the pytest process. These tests
    need the real dbus-python behaviour, so put it back for the duration.
    """
    original = _mpris_module.dbus
    _mpris_module.dbus = dbus
    yield
    _mpris_module.dbus = original


def _matches(current: dict, sent: dict) -> bool:
    """Whether a GetAll reply already reflects a command sent to the fake player."""
    if "status" in sent and str(current.get("PlaybackStatus")) != sent["status"]:
        return False
    if "metadata" in sent:
        title = sent["metadata"].get("xesam:title", "")
        if str(current.get("Metadata", {}).get("xesam:title", "")) != title:
            return False
    if "position_us" in sent and int(current.get("Position", -1)) != sent["position_us"]:
        return False
    return True


def _dbus_tools_available() -> bool:
    return shutil.which("dbus-run-session") is not None and shutil.which("dbus-daemon") is not None


def _wait_for_name(bus, name: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if name in bus.list_names():
            return True
        time.sleep(0.05)
    return False


def _wait_for_name_gone(bus, name: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if name not in bus.list_names():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture(scope="session")
def private_dbus_address():
    """A private session bus, started the documented way: ``dbus-run-session``.

    The wrapped program reports its own bus address, then waits for a QUIT
    on stdin and exits — letting dbus-run-session notice its program exit
    normally and tear the dbus-daemon down itself, same as it would for any
    program it wraps. A killed-process-group fallback covers the case where
    that handshake doesn't happen (e.g. the test run itself gets killed).
    """
    if not _dbus_tools_available():
        pytest.skip("dbus-run-session / dbus-daemon not installed")
    holder_code = (
        "import os, sys\n"
        "sys.stdout.write(os.environ['DBUS_SESSION_BUS_ADDRESS'] + chr(10))\n"
        "sys.stdout.flush()\n"
        "sys.stdin.readline()\n"
    )
    try:
        proc = subprocess.Popen(
            ["dbus-run-session", "--", sys.executable, "-c", holder_code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # so a stuck holder's dbus-daemon can be reaped by group
        )
    except OSError as e:
        pytest.skip(f"could not launch dbus-run-session: {e}")
    address = ""
    try:
        address = proc.stdout.readline().strip()
    except Exception:
        address = ""
    if not address:
        _kill_group(proc)
        pytest.skip("could not start a private D-Bus session")
    try:
        yield address
    finally:
        try:
            proc.stdin.write("QUIT\n")
            proc.stdin.flush()
            proc.wait(timeout=3)
        except Exception:
            pass
        finally:
            _kill_group(proc)


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill a process and anything it spawned (dbus-run-session's own dbus-daemon)."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


@pytest.fixture
def dbus_env(private_dbus_address, monkeypatch):
    """Point every dbus-python connection this test opens at the private bus."""
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", private_dbus_address)
    return private_dbus_address


class FakePlayer:
    """A running fake MPRIS player subprocess, talking real D-Bus."""

    def __init__(self, proc: subprocess.Popen, bus_name: str, calls_file: Path, props):
        self.proc = proc
        self.bus_name = bus_name
        self._calls_file = calls_file
        self._props = props  # org.freedesktop.DBus.Properties proxy, for update() to confirm on

    def update(self, timeout: float = 5.0, **kwargs) -> None:
        """Change status/metadata/position; the fake player emits PropertiesChanged.

        ``kwargs`` are the fake player's stdin command fields: ``status``,
        ``metadata``, ``position_us``. Blocks until a real read-back over
        D-Bus confirms the change landed, instead of guessing with a sleep —
        the subprocess can be scheduled late under a loaded CI runner.
        """
        self.proc.stdin.write(json.dumps(kwargs) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self._props.GetAll(_PLAYER_IFACE)
            if _matches(current, kwargs):
                return
            time.sleep(0.02)
        raise AssertionError(f"{self.bus_name} never reflected {kwargs!r} back over D-Bus")

    def calls(self) -> list[str]:
        """Method names the fake player actually received, in order."""
        if not self._calls_file.exists():
            return []
        return self._calls_file.read_text(encoding="utf-8").splitlines()

    def kill(self) -> None:
        """Take the player off the bus without a clean goodbye — a crashed/closed tab."""
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def stop(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            self.proc.stdin.write("QUIT\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=3)
        except Exception:
            pass
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=3)


@pytest.fixture
def spawn_player(dbus_env, tmp_path):
    """Factory fixture: spawn a fake MPRIS player on the private bus.

    Waits for it to actually own its bus name before handing it back, so
    tests never race the subprocess's startup. Always killed at teardown,
    even if the test raised.
    """
    players: list[FakePlayer] = []
    checker = dbus.SessionBus(private=True)

    def _spawn(suffix: str, identity: str, desktop_entry: str = "") -> FakePlayer:
        calls_file = tmp_path / f"calls-{suffix.replace('.', '_')}.log"
        bus_name = f"org.mpris.MediaPlayer2.{suffix}"
        proc = subprocess.Popen(
            [
                sys.executable,
                str(FAKE_PLAYER),
                "--suffix",
                suffix,
                "--identity",
                identity,
                "--desktop-entry",
                desktop_entry,
                "--calls-file",
                str(calls_file),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready = proc.stdout.readline()
            if not ready:
                raise RuntimeError((proc.stderr.read() or "fake player produced no output")[:2000])
            if not _wait_for_name(checker, bus_name):
                raise RuntimeError(f"{bus_name} never appeared on the private bus")
        except Exception:
            proc.kill()
            proc.wait(timeout=5)
            raise
        obj = checker.get_object(bus_name, "/org/mpris/MediaPlayer2", introspect=False)
        props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
        player = FakePlayer(proc, bus_name, calls_file, props)
        players.append(player)
        return player

    _spawn.wait_gone = lambda bus_name, timeout=5.0: _wait_for_name_gone(checker, bus_name, timeout)

    yield _spawn

    for player in players:
        player.stop()
    checker.close()
