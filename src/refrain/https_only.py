"""A urllib opener that refuses to follow a redirect off HTTPS, the CA bundle
it needs to recognise one, and the size-limited read every answer goes through."""

from __future__ import annotations

import os
import ssl
import time
import urllib.error
import urllib.request

# Where distributions keep their CA bundle (Debian/Ubuntu/Arch, Fedora, openSUSE, Alpine).
_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/ssl/cert.pem",
)


def use_system_certificates(candidates: tuple[str, ...] = _CA_BUNDLES) -> None:
    """Point OpenSSL at the host's CA bundle when its built-in path is missing.

    The AppImage's OpenSSL looks in Ubuntu's /usr/lib/ssl, which other
    distributions don't have, so every HTTPS request failed there.
    """
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return
    paths = ssl.get_default_verify_paths()
    if (paths.cafile and os.path.isfile(paths.cafile)) or (
        paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath)
    ):
        return
    for bundle in candidates:
        if os.path.isfile(bundle):
            os.environ["SSL_CERT_FILE"] = bundle
            return


class HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError(f"refusing redirect to non-https URL {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener() -> urllib.request.OpenerDirector:
    # urllib fixes the TLS context while the opener is built, not on the
    # first request: the bundle has to be found before that, whoever else
    # may get around to it later.
    use_system_certificates()
    return urllib.request.build_opener(HttpsOnlyRedirects)


class ResponseTooLarge(Exception):
    """The answer is longer than the caller set aside for it."""


_opener: urllib.request.OpenerDirector | None = None


def opener() -> urllib.request.OpenerDirector:
    """The opener every request Refrain sends goes through."""
    global _opener
    if _opener is None:
        _opener = build_opener()
    return _opener


# Read in pieces rather than in one call, so a deadline can be checked
# between them.
_CHUNK_BYTES = 64 * 1024
# A whole answer may take this many times the per-read timeout. The timeout
# alone bounds nothing: a server that sends a byte before each one can hold
# the worker for as long as it likes.
_DEADLINE_FACTOR = 4


def read_limited(response, limit: int, deadline: float | None = None) -> bytes:
    """Read ``response``, refusing an answer past ``limit`` bytes.

    Reading one byte past the cap tells "exactly at the limit" from
    "cut off", so an endpoint cannot fill memory by answering forever.
    ``deadline`` is a ``time.monotonic()`` value the whole read must
    finish by.
    """
    pieces: list[bytes] = []
    read = 0
    while read <= limit:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError(f"the answer did not arrive within {_DEADLINE_FACTOR}x the timeout")
        piece = response.read(min(_CHUNK_BYTES, limit + 1 - read))
        if not piece:
            break
        pieces.append(piece)
        read += len(piece)
    if read > limit:
        raise ResponseTooLarge(f"the answer is larger than {limit} bytes")
    return b"".join(pieces)


def deadline_for(timeout: float) -> float:
    """When a request started now has to be finished, whole."""
    return time.monotonic() + timeout * _DEADLINE_FACTOR


def fetch(request: urllib.request.Request, limit: int, timeout: float) -> bytes:
    """Send ``request`` through the shared opener, at most ``limit`` bytes back."""
    deadline = deadline_for(timeout)
    # Set before the connection, not after it, so the deadline covers the
    # whole request rather than only what follows the first byte.
    with opener().open(request, timeout=timeout) as r:  # nosec B310
        return read_limited(r, limit, deadline)
