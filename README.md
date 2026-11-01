# psa-card-finder

Finds underpriced PSA-graded English Pokémon cards on eBay and shows them on a
simple web page with your own settings (max price, grades, minimum discount).

## How it works

Every 6 hours a GitHub Action runs the scanner (`scanner/`):

1. **Search eBay** (official Browse API) for graded Pokémon cards listed as PSA 10 or PSA 9,
   up to `scan_max_price` in `config.json`.
2. **Throw out keyword spam.** A listing is dropped if its title has red-flag words
   (raw, candidate, "like PSA 10", Japanese, CGC/BGS…), lists several grades, or
   doesn't match the grade being searched.
3. **Check the item specifics** the seller filled in: grader must be PSA, grade must
   match, a cert number must be present, language must be English.
4. **Check the cert number with PSA.** PSA must confirm the same grade, a Pokémon card,
   and a non-Japanese/other-language set. PSA allows about 100 lookups a day; results
   are cached forever, so coverage grows each day. Not-yet-checked cards are labeled.
5. **Estimate the typical price** from other listings of the same card and grade
   (the median, so a few crazy asking prices don't skew it), and work out how far
   below that the listing is.
6. Save everything to `site/deals.json`. The page (`site/index.html`) filters it with your settings,
   which are remembered in your browser.

Note: PSA doesn't give 9.5 grades (that's BGS/CGC), so the grades here are 10 and 9.

## Setup

Add these under **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Where to get it |
| --- | --- |
| `EBAY_APP_ID` | developer.ebay.com → Application Keys → Production → App ID (Client ID) |
| `EBAY_CERT_ID` | same place → Cert ID (Client Secret) |
| `PSA_TOKEN` | psacard.com/publicapi → access token |

Then run it once by hand: **Actions → Scan eBay → Run workflow**.

To publish the page with GitHub Pages, set **Settings → Pages → Source** to "GitHub Actions"
and add a repository *variable* `PUBLISH_PAGE` = `true`.

## Running locally

```
python -m unittest discover -s tests -t .
EBAY_APP_ID=... EBAY_CERT_ID=... PSA_TOKEN=... python -m scanner.scan
cd site && python -m http.server   # open http://localhost:8000
```

## Limits to know about

- "Typical price" comes from **current asking prices**, not sold prices, because eBay's
  sold-price data needs special approval. A paid sold-price source can be added later.
- eBay allows 5,000 Browse API calls a day; a scan uses a few hundred.
