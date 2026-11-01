"""Scan eBay for underpriced PSA-graded English Pokemon cards.

Run:  python -m scanner.scan
Needs EBAY_APP_ID and EBAY_CERT_ID. PSA_TOKEN is optional but strongly
recommended (it confirms each cert number with PSA).
Writes site/deals.json for the deals page.
"""

import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import filters
from .hold import hold_score
from .ebay import EbayClient
from .psa import PsaAuthError, PsaClient, PsaLimitReached

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
CACHE = ROOT / "data" / "cache.json"
OUTPUT = ROOT / "site" / "deals.json"

DAY = 86400
ITEM_TTL = 3 * DAY
COMPS_TTL = 1 * DAY


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Scanner:
    def __init__(self, config, ebay, psa, cache):
        self.cfg = config
        self.ebay = ebay
        self.psa = psa
        self.cache = cache
        for key in ("items", "certs", "comps"):
            cache.setdefault(key, {})
        usage = cache.setdefault("psa_usage", {})
        if usage.get("date") != today():
            usage.update({"date": today(), "calls": 0})
        self.rejected = Counter()
        self.seen = 0
        self.detail_lookups = 0
        self.skipped = 0
        self.psa_out = False

    # ---- eBay search -------------------------------------------------
    def search_grade(self, grade):
        aspects = dict(self.cfg.get("aspect_filters") or {})
        aspects["Grade"] = grade
        kwargs = dict(
            q=f"pokemon psa {grade}",
            category_id=self.cfg["category_id"],
            max_price=self.cfg["scan_max_price"],
            max_items=self.cfg["max_listings_per_grade"],
        )
        results = list(self.ebay.search(aspects=aspects, **kwargs))
        if not results:
            # eBay's aspect names sometimes change; fall back to our own checks.
            print(f"  grade {grade}: no results with item-specific filters, retrying without them")
            results = list(self.ebay.search(aspects=None, **kwargs))
        return results

    # ---- per-listing checks ------------------------------------------
    def details_for(self, item_id):
        cached = self.cache["items"].get(item_id)
        if cached and time.time() - cached["at"] < ITEM_TTL:
            return cached["details"]
        if self.detail_lookups >= self.cfg["max_detail_lookups_per_run"]:
            return None
        self.detail_lookups += 1
        item = self.ebay.get_item(item_id)
        if item is None:
            return {"gone": True}
        details = filters.item_details(item)
        details["seller"] = (item.get("seller") or {}).get("username")
        details["feedback"] = (item.get("seller") or {}).get("feedbackPercentage")
        self.cache["items"][item_id] = {"at": time.time(), "details": details}
        return details

    def cert_for(self, cert_number):
        """Return (cert_dict, status) where status is verified/unverified/missing."""
        if cert_number in self.cache["certs"]:
            data = self.cache["certs"][cert_number]
            return data, "verified" if data else "missing"
        if not self.psa or self.psa_out:
            return None, "unverified"
        usage = self.cache["psa_usage"]
        if usage["calls"] >= self.cfg["psa_daily_cert_limit"]:
            self.psa_out = True
            return None, "unverified"
        try:
            data = self.psa.lookup(cert_number)
            usage["calls"] += 1
        except PsaLimitReached:
            self.psa_out = True
            return None, "unverified"
        except PsaAuthError as e:
            print(f"  PSA cert checks turned off for this scan: {e}")
            self.psa_out = True
            return None, "unverified"
        except Exception as e:  # PSA hiccup should not stop the whole scan
            print(f"  PSA lookup failed for {cert_number}: {e}")
            return None, "unverified"
        self.cache["certs"][cert_number] = data
        return data, "verified" if data else "missing"

    def market_for(self, query, grade, number):
        """Prices of other Buy It Now listings of the same card number and grade."""
        if not number:
            return []  # without a card number we can't be sure comps are the same card
        key = f"{grade}|{number}|{query.lower()}"
        cached = self.cache["comps"].get(key)
        if cached and time.time() - cached["at"] < COMPS_TTL:
            prices = cached["prices"]
        else:
            aspects = dict(self.cfg.get("aspect_filters") or {})
            aspects["Grade"] = grade
            prices = []
            for s in self.ebay.search(
                q=f"{query} psa {grade}" if number in query.lower() else f"{query} {number} psa {grade}",
                category_id=self.cfg["category_id"],
                aspects=aspects,
                max_items=self.cfg["comps_per_card"],
                buying="FIXED_PRICE",  # auction bids aren't real prices until the end
            ):
                if filters.title_problem(s.get("title", ""), grade, self.cfg["title_blocklist"]):
                    continue
                if number not in filters.card_numbers(s.get("title", "")):
                    continue
                price, ship = filters.listing_price(s)
                if price:
                    prices.append([s.get("itemId"), round(price + (ship or 0), 2)])
            self.cache["comps"][key] = {"at": time.time(), "prices": prices}
        return prices

    # ---- main --------------------------------------------------------
    def run(self):
        candidates = {}
        for grade in self.cfg["grades"]:
            results = self.search_grade(grade)
            print(f"  grade {grade}: {len(results)} listings from eBay")
            for s in results:
                self.seen += 1
                item_id = s.get("itemId")
                if not item_id or item_id in candidates:
                    continue
                problem = filters.title_problem(s.get("title", ""), grade, self.cfg["title_blocklist"])
                if problem:
                    self.rejected[filters.reason_group(problem)] += 1
                    continue
                price, ship = filters.listing_price(s)
                if price is None:
                    continue
                candidates[item_id] = (grade, s, price, ship)

        deals = []
        ordered = sorted(candidates.values(), key=lambda c: c[2] + (c[3] or 0))
        for grade, s, price, ship in ordered:
            item_id = s["itemId"]
            details = self.details_for(item_id)
            if details is None:
                self.skipped += 1
                continue
            if details.get("gone"):
                continue
            problem = filters.details_problem(details, grade)
            if problem:
                self.rejected[problem] += 1
                continue

            if details.get("cert"):
                cert, status = self.cert_for(details["cert"])
            else:
                # Many sellers skip the optional cert field; keep these but label them.
                cert, status = None, "no_cert"
            if status == "missing":
                self.rejected["cert number not found at PSA"] += 1
                continue
            if status == "verified":
                problem = filters.psa_problem(cert, grade, self.cfg["psa_brand_blocklist"])
                if problem:
                    self.rejected[problem] += 1
                    continue

            query = filters.comps_query(s.get("title", ""), cert)
            number = filters.card_number(s.get("title", ""), cert)
            comps = [p for cid, p in self.market_for(query, grade, number) if cid != item_id]
            market = filters.market_value(comps, self.cfg["min_comps"])
            total = round(price + (ship or 0), 2)
            is_auction = "AUCTION" in (s.get("buyingOptions") or [])
            discount = round(1 - total / market, 3) if market else None
            if is_auction and not filters.ending_soon(s.get("itemEndDate")):
                discount = None  # an early bid says nothing about the final price

            hold, hold_why = hold_score(s.get("title", ""), grade, cert)
            deals.append({
                "id": item_id,
                "title": s.get("title"),
                "url": s.get("itemWebUrl"),
                "image": (s.get("image") or {}).get("imageUrl")
                         or ((s.get("thumbnailImages") or [{}])[0]).get("imageUrl"),
                "grade": grade,
                "price": price,
                "shipping": ship,
                "total": total,
                "buying": "auction" if is_auction else "buy_now",
                "bids": s.get("bidCount"),
                "ends": s.get("itemEndDate"),
                "cert": details["cert"],
                "psa_status": status,
                "card": " ".join(filter(None, [
                    (cert or {}).get("Year"), (cert or {}).get("Brand"),
                    (cert or {}).get("Subject"),
                    f"#{cert['CardNumber']}" if cert and cert.get("CardNumber") else None,
                ])) or None,
                "hold": hold,
                "hold_why": hold_why,
                "population": (cert or {}).get("TotalPopulation"),
                "pop_higher": (cert or {}).get("PopulationHigher"),
                "market": market,
                "comps": len(comps),
                "discount": discount,
                "seller": details.get("seller"),
                "feedback": details.get("feedback"),
            })

        deals.sort(key=lambda d: (d["discount"] is None, -(d["discount"] or 0)))
        return deals

    def prune_cache(self):
        now = time.time()
        for key, ttl in (("items", ITEM_TTL), ("comps", COMPS_TTL)):
            self.cache[key] = {k: v for k, v in self.cache[key].items() if now - v["at"] < ttl}


def main():
    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    if not app_id or not cert_id:
        # Not an error: the page still publishes, it just has no deals yet.
        print("No eBay keys yet (EBAY_APP_ID / EBAY_CERT_ID). Skipping the scan; see README.")
        return
    psa_token = os.environ.get("PSA_TOKEN")
    if not psa_token:
        print("No PSA_TOKEN set: cert numbers will not be checked with PSA.")

    config = load_json(CONFIG, None)
    cache = load_json(CACHE, {})
    ebay = EbayClient(app_id, cert_id, config.get("marketplace", "EBAY_US"),
                      buyer_zip=str(config.get("buyer_zip") or "").strip() or None)
    psa = PsaClient(psa_token) if psa_token else None

    scanner = Scanner(config, ebay, psa, cache)
    print("Scanning eBay...")
    deals = scanner.run()
    scanner.prune_cache()

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scan_max_price": config["scan_max_price"],
        "grades": config["grades"],
        "stats": {
            "listings_seen": scanner.seen,
            "kept": len(deals),
            "skipped_until_next_scan": scanner.skipped,
            "rejected": dict(scanner.rejected.most_common()),
            "ebay_calls": ebay.calls,
            "psa_calls_today": cache["psa_usage"]["calls"],
        },
        "deals": deals,
    }
    save_json(OUTPUT, output)
    save_json(CACHE, cache)
    print(f"Done: {scanner.seen} listings seen, {len(deals)} kept, "
          f"{sum(scanner.rejected.values())} filtered out, {ebay.calls} eBay calls.")


if __name__ == "__main__":
    main()
