"""A urllib opener that refuses to follow a redirect off HTTPS, and the
CA bundle it needs to recognise one."""

from __future__ import annotations

import os
import ssl
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
