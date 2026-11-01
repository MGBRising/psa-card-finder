"""Looks up PSA cert numbers through PSA's public API."""

import json
import urllib.error
import urllib.request

API = "https://api.psacard.com/publicapi/cert/GetByCertNumber/"


class PsaLimitReached(Exception):
    pass


class PsaAuthError(Exception):
    """PSA refused the request (bad token or blocked client)."""


class PsaClient:
    def __init__(self, token):
        self.token = token
        self.calls = 0

    def lookup(self, cert_number):
        """Return the PSACert dict, or None if PSA has no such cert."""
        req = urllib.request.Request(
            API + cert_number,
            headers={
                "Authorization": f"bearer {self.token.strip()}",
                "Accept": "application/json",
                # PSA's firewall rejects Python's default user agent.
                "User-Agent": "Mozilla/5.0 (compatible; psa-card-finder/1.0)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self.calls += 1
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise PsaLimitReached() from e
            if e.code in (400, 404):
                return None
            if e.code in (401, 403):
                body = e.read().decode(errors="replace")[:200]
                raise PsaAuthError(f"PSA returned {e.code}: {body}") from e
            raise
        if not data:
            return None
        cert = data.get("PSACert") or data.get("psaCert")
        return cert or None
