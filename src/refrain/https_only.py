"""A urllib opener that refuses to follow a redirect off HTTPS."""

from __future__ import annotations

import urllib.error
import urllib.request


class HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError(f"refusing redirect to non-https URL {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(HttpsOnlyRedirects)
