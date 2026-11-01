"""Rules that decide whether a listing is a real PSA-graded English Pokemon card.

Every function returns a short human-readable reason when a listing fails,
or None when it passes. The reasons are counted and shown on the deals page.
"""

import re
import statistics
from datetime import datetime, timezone
import unicodedata

GRADE_RE = re.compile(r"\bpsa\s*(?:gem\s*(?:mint|mt)\s*|mint\s*)?(\d{1,2}(?:\.\d)?)\b", re.I)


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def grades_in_title(title):
    """All PSA grades written in a title, e.g. 'PSA 10' and 'PSA GEM MT 10' -> {'10'}."""
    return {clean_grade(g) for g in GRADE_RE.findall(_norm(title))}


def clean_grade(g):
    """'10.0' -> '10', '9' -> '9', '8.5' -> '8.5'."""
    try:
        return f"{float(g):g}"
    except (TypeError, ValueError):
        return g


def title_problem(title, grade, blocklist):
    t = _norm(title)
    for word in blocklist:
        if re.search(r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?![a-z0-9])", t):
            return f'title says "{word}"'
    found = grades_in_title(title)
    if not found:
        return "title never says PSA + grade"
    if grade not in found:
        return f"title grade {'/'.join(sorted(found))} does not match"
    if len(found) > 1:
        return "title lists several grades (keyword stuffing)"
    return None


def reason_group(problem):
    """Collapse detailed title reasons into a few buckets for the stats panel."""
    if problem.startswith("title says"):
        return "red-flag word in title (raw, candidate, Japanese, other grader...)"
    if problem.startswith("title grade"):
        return "title grade does not match"
    return problem


def item_details(item):
    """Pull grader, grade, cert and language out of an eBay item."""
    details = {"grader": None, "grade": None, "cert": None, "language": None}
    for d in item.get("conditionDescriptors") or []:
        name = _norm(d.get("name"))
        values = [v.get("content", "") for v in d.get("values") or []]
        value = values[0].strip() if values else None
        if "grader" in name:
            details["grader"] = value
        elif name == "grade":
            details["grade"] = value
        elif "cert" in name:
            details["cert"] = value
    for a in item.get("localizedAspects") or []:
        name = _norm(a.get("name"))
        value = (a.get("value") or "").strip()
        if name == "language":
            details["language"] = value
        elif "grader" in name and not details["grader"]:
            details["grader"] = value
        elif name == "grade" and not details["grade"]:
            details["grade"] = value
        elif "certification" in name and not details["cert"]:
            details["cert"] = value
    if details["cert"]:
        digits = re.sub(r"\D", "", details["cert"])
        details["cert"] = digits or None
    if details["grade"]:
        m = re.search(r"\d{1,2}(?:\.\d)?", details["grade"])
        details["grade"] = clean_grade(m.group(0)) if m else details["grade"]
    return details


def details_problem(details, grade):
    grader = _norm(details.get("grader"))
    if not grader:
        return "no grading company listed"
    if "psa" not in grader and "professional sports authenticator" not in grader:
        return "graded by someone other than PSA"
    if details.get("grade") != grade:
        return "listed grade does not match"
    lang = _norm(details.get("language"))
    if lang and "english" not in lang:
        return "not an English card"
    return None


def psa_grade(cert):
    m = re.search(r"(\d{1,2}(?:\.\d)?)\s*$", (cert.get("CardGrade") or "").strip())
    return clean_grade(m.group(1)) if m else None


def psa_problem(cert, grade, brand_blocklist):
    if cert is None:
        return "cert number not found at PSA"
    if psa_grade(cert) != grade:
        return "PSA says a different grade"
    brand = _norm(cert.get("Brand")).upper()
    subject = _norm(cert.get("Subject")).upper()
    category = _norm(cert.get("Category")).upper()
    if "POKEMON" not in f"{brand} {subject} {category}":
        return "PSA says it is not a Pokemon card"
    for word in brand_blocklist:
        if word in brand:
            return "PSA says it is not an English card"
    return None


NUM_PATTERNS = [
    re.compile(r"(?<![a-z0-9])([a-z]{0,4}\d{1,3})\s*/\s*[a-z]{0,4}\d{1,3}(?![a-z0-9])"),  # 161/181, TG06/TG30
    re.compile(r"#\s*([a-z]{0,5}\d{1,4})(?![a-z0-9])"),                                    # #161, #SWSH184
    re.compile(r"(?<![a-z0-9])((?:swsh|sm|xy|bw|svp|sv|tg|gg|rc|hgss|dp|smp)\d{1,3})(?![a-z0-9])"),  # SWSH184
]


def _norm_number(n):
    m = re.match(r"([a-z]*)0*(\d+)$", n.lower())
    return (m.group(1) + m.group(2)) if m else n.lower()


def card_numbers(title):
    """Card numbers written in a title, normalized: '#020' -> '20', 'TG06/TG30' -> 'tg6'."""
    t = _norm(title)
    found = set()
    for i, pat in enumerate(NUM_PATTERNS):
        if i == 1:  # drop set totals ("/181") so they aren't read as card numbers
            t = re.sub(r"/\s*[a-z]{0,4}\d{1,3}", " ", t)
        for n in pat.findall(t):
            if re.fullmatch(r"(19|20)\d\d", n):  # a year, not a card number
                continue
            found.add(_norm_number(n))
    return found


def card_number(title, cert=None):
    """The one card number for this listing, or None if unclear."""
    if cert and cert.get("CardNumber"):
        return _norm_number(re.sub(r"[^a-z0-9]", "", _norm(cert["CardNumber"])))
    nums = card_numbers(title)
    return nums.pop() if len(nums) == 1 else None


def comps_query(title, cert=None):
    """Search words that find other copies of the same card."""
    if cert and cert.get("Subject"):
        parts = [cert.get("Subject"), cert.get("CardNumber") or ""]
        return " ".join(p for p in parts if p).strip()
    t = GRADE_RE.sub(" ", _norm(title))
    t = re.sub(r"\b(gem|mint|mt|pokemon|card|tcg|graded|slab|holo|rare|nm|psa)\b", " ", t)
    t = re.sub(r"[^a-z0-9/ ]", " ", t)
    words = t.split()
    return " ".join(words[:6])


def ending_soon(end_iso, hours=24):
    """True if an auction ends within the next `hours`."""
    if not end_iso:
        return False
    try:
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    left = (end - datetime.now(timezone.utc)).total_seconds()
    return 0 < left <= hours * 3600


def market_value(prices, min_comps):
    """Typical price of a card from comparable listings.

    Uses the median, so a few crazy-high asking prices do not inflate it.
    """
    prices = sorted(p for p in prices if p and p > 0)
    if len(prices) < min_comps:
        return None
    return round(statistics.median(prices), 2)


def listing_price(summary):
    """Item price plus cheapest shipping, in USD."""
    is_auction = "AUCTION" in (summary.get("buyingOptions") or [])
    if is_auction and summary.get("currentBidPrice"):
        price_info = summary["currentBidPrice"]
    else:
        price_info = summary.get("price") or {}
    try:
        price = float(price_info.get("value"))
    except (TypeError, ValueError):
        return None, None
    shipping = None
    for opt in summary.get("shippingOptions") or []:
        cost = (opt.get("shippingCost") or {}).get("value")
        if cost is not None:
            cost = float(cost)
            shipping = cost if shipping is None else min(shipping, cost)
    return price, shipping
