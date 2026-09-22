"""Updater: download timeout and read-limit safety on the security-critical fetches.

Reviewed mutmut survivors (21.09) that weren't equivalent mutants: a missing
timeout on `_open_download` would hang forever, a missing read limit would
load an unbounded file into memory, and the `> limit` boundary needs a file
of exactly `limit` bytes to prove it isn't `>=`.
"""

from __future__ import annotations

import hashlib
import io

import pytest

import refrain.updater as updater
from tests.release_signing import PUBLIC_KEY_HEX, signature_for

_DL = "https://github.com/Rockykln/refrain/releases/download/v9.9.9/"
_SUMS = _DL + "SHA256SUMS"
_SIG = _DL + "SHA256SUMS.sig"


class _RecordingBody:
    """Fakes what `_open_download` returns; records the exact `read()` argument."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)
        self.read_calls: list[int | None] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        self.read_calls.append(n)
        return self._buf.read() if n is None else self._buf.read(n)


def test_fetch_release_file_reads_with_a_bounded_limit(monkeypatch):
    body = _RecordingBody(b"x" * 10)
    monkeypatch.setattr(updater, "_open_download", lambda *a, **k: body)
    updater._fetch_release_file(_SUMS, "SHA256SUMS", 64 * 1024)
    assert body.read_calls == [64 * 1024 + 1]


def test_fetch_release_file_never_reads_unbounded(monkeypatch):
    """A read() without an explicit limit could load an arbitrarily large file into memory."""

    class _UnboundedBody:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            if n is None:
                raise AssertionError("read() was called without a byte limit")
            return b"y" * n

    monkeypatch.setattr(updater, "_open_download", lambda *a, **k: _UnboundedBody())
    with pytest.raises(updater.UnverifiedReleaseError, match="larger than"):
        updater._fetch_release_file(_SUMS, "SHA256SUMS", 64 * 1024)


def test_fetch_release_file_accepts_exactly_the_limit(monkeypatch):
    limit = 64 * 1024
    body = _RecordingBody(b"a" * limit)
    monkeypatch.setattr(updater, "_open_download", lambda *a, **k: body)
    data = updater._fetch_release_file(_SUMS, "SHA256SUMS", limit)
    assert len(data) == limit


def test_fetch_release_file_rejects_one_byte_over_the_limit(monkeypatch):
    limit = 64 * 1024
    body = _RecordingBody(b"a" * (limit + 1))
    monkeypatch.setattr(updater, "_open_download", lambda *a, **k: body)
    with pytest.raises(updater.UnverifiedReleaseError, match="larger than"):
        updater._fetch_release_file(_SUMS, "SHA256SUMS", limit)


def test_expected_sha256_fetches_use_a_real_timeout(monkeypatch):
    timeouts = {}

    def fake_open(url, timeout):
        timeouts[url] = timeout
        return _RecordingBody(b"")

    monkeypatch.setattr(updater, "_open_download", fake_open)
    monkeypatch.setattr(updater, "RELEASE_PUBLIC_KEY", PUBLIC_KEY_HEX)
    release = updater.ReleaseInfo(
        tag="v9.9.9",
        version="9.9.9",
        name="x",
        body="",
        html_url="",
        appimage_name="Refrain-9.9.9-x86_64.AppImage",
        sha256sums_url=_SUMS,
        sha256sums_sig_url=_SIG,
    )
    with pytest.raises(updater.UnverifiedReleaseError):
        updater._expected_sha256(release)
    assert timeouts == {_SUMS: updater._TIMEOUT_S, _SIG: updater._TIMEOUT_S}


def test_apply_appimage_download_uses_a_real_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "RELEASE_PUBLIC_KEY", PUBLIC_KEY_HEX)
    name = "Refrain-9.9.9-x86_64.AppImage"
    appimage_bytes = b"\x7fELF" + b"0" * 2_000_000
    sums = f"{hashlib.sha256(appimage_bytes).hexdigest()}  {name}\n".encode()
    files = {_DL + name: appimage_bytes, _SUMS: sums, _SIG: signature_for(sums)}
    timeouts = {}

    def fake_open(url, timeout):
        timeouts[url] = timeout
        return _RecordingBody(files[url])

    monkeypatch.setattr(updater, "_open_download", fake_open)
    target = tmp_path / name
    target.write_bytes(b"old build")
    monkeypatch.setenv("APPIMAGE", str(target))
    release = updater.ReleaseInfo(
        tag="v9.9.9",
        version="9.9.9",
        name="x",
        body="",
        html_url="",
        appimage_url=_DL + name,
        appimage_size=len(appimage_bytes),
        appimage_name=name,
        sha256sums_url=_SUMS,
        sha256sums_sig_url=_SIG,
    )

    result = updater._apply_appimage(release)

    assert result.success is True
    assert timeouts == {_SUMS: updater._TIMEOUT_S, _SIG: updater._TIMEOUT_S, _DL + name: 60}
