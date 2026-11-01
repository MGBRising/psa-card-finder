"""Long-term "hold score": how good a card looks for a 10+ year hold.

A simple, explainable 0-100 score. Each rule adds or removes points and
records a short reason that the deals page shows next to the score.
"""

import re
import unicodedata
from datetime import datetime, timezone

# Pokémon that collectors have chased for decades. Points reflect demand.
ICONIC = {
    "charizard": 30, "pikachu": 25, "umbreon": 25,
    "mewtwo": 18, "mew": 18, "rayquaza": 18, "lugia": 18, "gengar": 18,
    "eevee": 15, "espeon": 15, "sylveon": 15, "glaceon": 12, "leafeon": 12,
    "vaporeon": 12, "jolteon": 12, "flareon": 12,
    "blastoise": 15, "venusaur": 12, "charmander": 12, "squirtle": 10, "bulbasaur": 10,
    "gyarados": 12, "dragonite": 12, "snorlax": 12, "greninja": 12, "lucario": 10,
    "giratina": 12, "arceus": 10, "ho-oh": 12, "celebi": 10, "jirachi": 10,
    "mimikyu": 10, "garchomp": 10, "tyranitar": 10, "darkrai": 10, "gardevoir": 10,
}

# Art and rarity words, best first; only the best match counts.
ART = [
    (r"special illustration rare|\bsir\b|alt(ernate)? art", 22, "alt art / special illustration"),
    (r"\b1st edition\b|first edition", 22, "1st Edition"),
    (r"shadowless", 20, "Shadowless"),
    (r"gold star|\bstar\b.*\bdelta\b", 20, "Gold Star"),
    (r"illustration rare|\bir\b", 15, "illustration rare"),
    (r"hyper rare|gold secret|rainbow rare|secret rare", 12, "secret / hyper rare"),
    (r"full art|\bfa\b|trainer gallery|\btg\d", 10, "full art"),
    (r"\bshiny\b|shining", 10, "shiny"),
    (r"\bholo\b", 4, "holo"),
]


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def card_year(title, cert=None):
    y = (cert or {}).get("Year")
    if y and str(y)[:4].isdigit():
        return int(str(y)[:4])
    m = re.search(r"\b(199[6-9]|20[0-3]\d)\b", title or "")
    return int(m.group(1)) if m else None


def hold_score(title, grade, cert=None, now=None):
    """Return (score 0-100, list of reasons)."""
    text = _norm(f"{title} {(cert or {}).get('Subject', '')} {(cert or {}).get('Variety', '')}")
    score, why = 20, []

    best = 0
    for name, pts in ICONIC.items():
        if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", text) and pts > best:
            best, top = pts, name
    if best:
        score += best
        why.append(f"{top.title()} (+{best})")

    for pattern, pts, label in ART:
        if re.search(pattern, text):
            score += pts
            why.append(f"{label} (+{pts})")
            break

    year = card_year(title, cert)
    this_year = (now or datetime.now(timezone.utc)).year
    if year:
        if year <= 2003:
            score += 20
            why.append(f"vintage {year} (+20)")
        elif year <= 2016:
            score += 10
            why.append(f"older card, {year} (+10)")
        elif year >= this_year - 1:
            score -= 5
            why.append(f"brand new set, prices still settling (-5)")

    pop = (cert or {}).get("TotalPopulation")
    try:
        pop = int(pop) if pop is not None else None
    except (TypeError, ValueError):
        pop = None
    if pop is not None:
        if pop <= 100:
            score += 20
            why.append(f"very low PSA pop {pop} (+20)")
        elif pop <= 500:
            score += 12
            why.append(f"low PSA pop {pop} (+12)")
        elif pop <= 2000:
            score += 5
            why.append(f"PSA pop {pop} (+5)")
        elif pop > 20000:
            score -= 10
            why.append(f"very common, PSA pop {pop:,} (-10)")

    if grade == "10":
        score += 10
        why.append("PSA 10 (+10)")

    return max(0, min(100, score)), why
