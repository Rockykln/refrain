"""The opener every request shares: no redirect off HTTPS, no unbounded read."""

from __future__ import annotations

import io
import time
import urllib.error
import urllib.request

import pytest

from refrain import https_only


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_every_caller_gets_the_same_opener():
    assert https_only.opener() is https_only.opener()


def test_the_shared_opener_refuses_a_redirect_off_https():
    handler = next(
        h for h in https_only.opener().handlers if isinstance(h, https_only.HttpsOnlyRedirects)
    )
    with pytest.raises(urllib.error.URLError, match="refusing redirect"):
        handler.redirect_request(
            urllib.request.Request("https://example.com/a"),
            None,
            302,
            "Found",
            {},
            "http://example.com/b",
        )


def test_an_answer_exactly_at_the_limit_is_read():
    assert https_only.read_limited(_Body(b"12345678"), 8) == b"12345678"


def test_an_answer_past_the_limit_is_refused():
    with pytest.raises(https_only.ResponseTooLarge, match="larger than 8 bytes"):
        https_only.read_limited(_Body(b"123456789"), 8)


def test_an_answer_that_only_trickles_runs_out_of_time(monkeypatch):
    """The per-read timeout bounds nothing on its own: a server that sends a
    byte before each one would hold the worker for as long as it liked."""

    class _Trickle:
        def read(self, n=-1):
            time.sleep(0.01)
            return b"x"

    monkeypatch.setattr(https_only, "_CHUNK_BYTES", 1)
    began = time.monotonic()
    with pytest.raises(TimeoutError):
        https_only.read_limited(_Trickle(), 10_000_000, deadline=time.monotonic() + 0.2)
    assert time.monotonic() - began < 3.0  # generous: a slow runner must not fail this


def test_fetch_never_reads_unbounded(monkeypatch):
    class _Endless:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            if n is None:
                raise AssertionError("read() was called without a byte limit")
            return b"y" * n

    monkeypatch.setattr(https_only.opener(), "open", lambda *a, **kw: _Endless())
    with pytest.raises(https_only.ResponseTooLarge):
        https_only.fetch(urllib.request.Request("https://example.com/"), 16, 5)


def test_fetch_goes_out_through_the_shared_opener(monkeypatch):
    seen = []

    def _open(request, timeout):
        seen.append((request.full_url, timeout))
        return _Body(b"ok")

    monkeypatch.setattr(https_only.opener(), "open", _open)
    assert https_only.fetch(urllib.request.Request("https://example.com/x"), 64, 3) == b"ok"
    assert seen == [("https://example.com/x", 3)]
