"""Small client for eBay's Browse API (active listings only)."""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.ebay.com"
SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayError(Exception):
    pass


class EbayClient:
    def __init__(self, app_id, cert_id, marketplace="EBAY_US"):
        self.app_id = app_id
        self.cert_id = cert_id
        self.marketplace = marketplace
        self._token = None
        self._token_expires = 0
        self.calls = 0

    def _get_token(self):
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        creds = base64.b64encode(f"{self.app_id}:{self.cert_id}".encode()).decode()
        body = urllib.parse.urlencode(
            {"grant_type": "client_credentials", "scope": SCOPE}
        ).encode()
        req = urllib.request.Request(
            f"{API}/identity/v1/oauth2/token",
            data=body,
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        data = self._send(req)
        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 7200))
        return self._token

    def _send(self, req, retries=3):
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self.calls += 1
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                detail = e.read().decode(errors="replace")[:500]
                raise EbayError(f"eBay returned {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                if attempt < retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise EbayError(f"Could not reach eBay: {e}") from e

    def _get(self, path, params=None):
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
                "Accept": "application/json",
            },
        )
        return self._send(req)

    def search(self, q, category_id, max_price=None, aspects=None,
               limit=200, max_items=600, graded_only=True):
        """Yield item summaries for a search, following pages up to max_items."""
        filters = ["priceCurrency:USD", "buyingOptions:{FIXED_PRICE|AUCTION}"]
        if max_price is not None:
            filters.append(f"price:[..{max_price}]")
        if graded_only:
            filters.append("conditionIds:{2750}")  # 2750 = Graded
        params = {
            "q": q,
            "category_ids": category_id,
            "filter": ",".join(filters),
            "limit": min(limit, 200),
        }
        if aspects:
            parts = [f"categoryId:{category_id}"]
            parts += [f"{name}:{{{value}}}" for name, value in aspects.items()]
            params["aspect_filter"] = ",".join(parts)

        offset = 0
        seen = 0
        while seen < max_items:
            params["offset"] = offset
            data = self._get("/buy/browse/v1/item_summary/search", params) or {}
            items = data.get("itemSummaries") or []
            for item in items:
                yield item
                seen += 1
                if seen >= max_items:
                    return
            total = data.get("total", 0)
            offset += len(items)
            if not items or offset >= total or offset >= 10000:
                return

    def get_item(self, item_id):
        return self._get(f"/buy/browse/v1/item/{urllib.parse.quote(item_id, safe='')}")
