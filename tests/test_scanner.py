import json
import unittest
from pathlib import Path

from scanner import filters
from scanner.scan import Scanner

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
BL = CONFIG["title_blocklist"]


class TitleTests(unittest.TestCase):
    def test_clean_psa10(self):
        self.assertIsNone(filters.title_problem("2023 Pokemon 151 Charmander #168 PSA 10 GEM MINT", "10", BL))

    def test_gem_mt_wording(self):
        self.assertIsNone(filters.title_problem("Pikachu Promo PSA GEM MT 10", "10", BL))

    def test_keyword_spam_rejected(self):
        for title in [
            "Charizard ex PSA 10 candidate pack fresh",
            "Umbreon VMAX raw NM like PSA 10",
            "Pikachu Japanese PSA 10",
            "Mewtwo CGC 10 not PSA 10",
            "Gengar PSA 9 PSA 10 BGS",
            "Pokemon mystery slab PSA 10",
        ]:
            self.assertIsNotNone(filters.title_problem(title, "10", BL), title)

    def test_grade_mismatch(self):
        self.assertIsNotNone(filters.title_problem("Eevee PSA 9 MINT", "10", BL))
        self.assertIsNone(filters.title_problem("Eevee PSA 9 MINT", "9", BL))

    def test_word_boundaries(self):
        # "raw" must not match inside "Drawn" or "Crawdaunt"
        self.assertIsNone(filters.title_problem("Crawdaunt Drawn Art PSA 10", "10", BL))

    def test_grade_numbers(self):
        self.assertEqual(filters.clean_grade("10.0"), "10")
        self.assertEqual(filters.clean_grade("9"), "9")


class DetailTests(unittest.TestCase):
    ITEM = {
        "conditionDescriptors": [
            {"name": "Professional Grader", "values": [{"content": "Professional Sports Authenticator (PSA)"}]},
            {"name": "Grade", "values": [{"content": "10"}]},
            {"name": "Certification Number", "values": [{"content": "#12345678"}]},
        ],
        "localizedAspects": [{"name": "Language", "value": "English"}],
    }

    def test_parse(self):
        d = filters.item_details(self.ITEM)
        self.assertEqual(d["cert"], "12345678")
        self.assertIsNone(filters.details_problem(d, "10"))

    def test_other_grader(self):
        d = filters.item_details(self.ITEM)
        d["grader"] = "Certified Guaranty Company (CGC)"
        self.assertEqual(filters.details_problem(d, "10"), "graded by someone other than PSA")

    def test_missing_cert_allowed_language_checked(self):
        d = filters.item_details(self.ITEM)
        self.assertIsNone(filters.details_problem({**d, "cert": None}, "10"))  # allowed, just labeled
        self.assertIsNotNone(filters.details_problem({**d, "language": "Japanese"}, "10"))


class PsaTests(unittest.TestCase):
    CERT = {"CardGrade": "GEM MT 10", "Brand": "POKEMON SV 151", "Subject": "CHARMANDER", "CardNumber": "168"}

    def test_ok(self):
        self.assertIsNone(filters.psa_problem(self.CERT, "10", CONFIG["psa_brand_blocklist"]))

    def test_wrong_grade(self):
        self.assertIsNotNone(filters.psa_problem({**self.CERT, "CardGrade": "MINT 9"}, "10", []))

    def test_japanese(self):
        cert = {**self.CERT, "Brand": "POKEMON JAPANESE SV2A-POKEMON 151"}
        self.assertEqual(filters.psa_problem(cert, "10", CONFIG["psa_brand_blocklist"]),
                         "PSA says it is not an English card")

    def test_not_pokemon(self):
        cert = {**self.CERT, "Brand": "TOPPS CHROME", "Subject": "MIKE TROUT"}
        self.assertIsNotNone(filters.psa_problem(cert, "10", []))


class CardNumberTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(filters.card_numbers("Team Up #161 Full Art PSA 10"), {"161"})
        self.assertEqual(filters.card_numbers("Mega Charizard Y EX 294/217 PSA 9"), {"294"})
        self.assertEqual(filters.card_numbers("Jolteon VMAX #SWSH184 PSA 10"), {"swsh184"})
        self.assertEqual(filters.card_numbers("Gengar TG06/TG30 PSA 10"), {"tg6"})
        self.assertEqual(filters.card_numbers("Charizard VMAX #020 PSA 9"), {"20"})

    def test_year_is_not_a_number(self):
        self.assertEqual(filters.card_numbers("2021 Pokemon Celebrations PSA 10"), set())

    def test_cert_number_wins(self):
        self.assertEqual(filters.card_number("whatever", {"CardNumber": "020"}), "20")


class HoldScoreTests(unittest.TestCase):
    def test_iconic_vintage_beats_modern_common(self):
        from scanner.hold import hold_score
        zard, _ = hold_score("1999 Pokemon Base Set Shadowless Charizard #4 PSA 9", "9")
        common, _ = hold_score("2025 Pokemon Bulbasaur Reverse Holo PSA 9", "9")
        self.assertGreater(zard, 80)
        self.assertLess(common, 40)

    def test_reasons_and_bounds(self):
        from scanner.hold import hold_score
        score, why = hold_score("Umbreon VMAX Alt Art #215 PSA 10", "10",
                                {"Year": "2021", "TotalPopulation": 80})
        self.assertLessEqual(score, 100)
        self.assertTrue(any("Umbreon" in w for w in why))
        self.assertTrue(any("pop 80" in w for w in why))

    def test_common_pop_penalized(self):
        from scanner.hold import hold_score
        hi, _ = hold_score("Pikachu PSA 10", "10", {"TotalPopulation": 50})
        lo, _ = hold_score("Pikachu PSA 10", "10", {"TotalPopulation": 50000})
        self.assertGreater(hi, lo)


class MarketTests(unittest.TestCase):
    def test_median_ignores_outliers(self):
        self.assertEqual(filters.market_value([40, 45, 50, 900], 3), 47.5)

    def test_needs_enough_comps(self):
        self.assertIsNone(filters.market_value([40, 45], 3))


def summary(item_id, title, price, ship=0):
    return {"itemId": item_id, "title": title, "price": {"value": str(price)},
            "shippingOptions": [{"shippingCost": {"value": str(ship)}}],
            "buyingOptions": ["FIXED_PRICE"], "itemWebUrl": f"https://ebay.com/itm/{item_id}"}


class FakeEbay:
    calls = 0

    def __init__(self):
        self.listings = [
            summary("good", "2023 Pokemon 151 Charmander #168 PSA 10", 30, 4),
            summary("spam", "Charizard PSA 10 candidate raw", 5),
            summary("cgc", "Pikachu PSA 10", 20),
            summary("fakecert", "Mew PSA 10", 25),
        ]
        self.comps = [summary(f"c{i}", "Charmander #168 151 PSA 10", p) for i, p in enumerate([60, 65, 70, 999])]
        # Same set, different card: must not count toward Charmander's price
        self.comps += [summary(f"o{i}", "Charizard #183 151 PSA 10", 900) for i in range(5)]

    def search(self, q, aspects=None, **kw):
        return iter(self.listings if q.startswith("pokemon psa 10") else
                    [] if q.startswith("pokemon psa") else self.comps)

    def get_item(self, item_id):
        grader = "Certified Guaranty Company (CGC)" if item_id == "cgc" else "Professional Sports Authenticator (PSA)"
        cert = {"good": "111", "cgc": "222", "fakecert": "999"}[item_id]
        return {"conditionDescriptors": [
            {"name": "Professional Grader", "values": [{"content": grader}]},
            {"name": "Grade", "values": [{"content": "10"}]},
            {"name": "Certification Number", "values": [{"content": cert}]},
        ]}


class FakePsa:
    def lookup(self, cert):
        if cert == "111":
            return {"CardGrade": "GEM MT 10", "Brand": "POKEMON SV 151", "Subject": "CHARMANDER",
                    "CardNumber": "168", "TotalPopulation": 5000}
        return None


class ScannerTests(unittest.TestCase):
    def test_end_to_end(self):
        cfg = {**CONFIG, "grades": ["10"]}
        scanner = Scanner(cfg, FakeEbay(), FakePsa(), {})
        deals = scanner.run()
        self.assertEqual([d["id"] for d in deals], ["good"])
        deal = deals[0]
        self.assertEqual(deal["total"], 34)
        self.assertEqual(deal["market"], 67.5)  # median of 60,65,70,999
        self.assertAlmostEqual(deal["discount"], 0.496, places=3)
        self.assertEqual(deal["psa_status"], "verified")
        self.assertIn("hold", deal)
        self.assertEqual(sum(scanner.rejected.values()), 3)

    def test_early_auction_has_no_discount(self):
        ebay = FakeEbay()
        ebay.listings[0]["buyingOptions"] = ["AUCTION"]
        ebay.listings[0]["currentBidPrice"] = {"value": "1.00"}
        ebay.listings[0]["itemEndDate"] = "2099-01-01T00:00:00Z"
        deals = Scanner({**CONFIG, "grades": ["10"]}, ebay, FakePsa(), {}).run()
        self.assertEqual(deals[0]["total"], 5)
        self.assertIsNone(deals[0]["discount"])

    def test_psa_block_stops_lookups(self):
        from scanner.psa import PsaAuthError

        class BlockedPsa:
            calls = 0

            def lookup(self, cert):
                self.calls += 1
                raise PsaAuthError("PSA returned 403")

        psa = BlockedPsa()
        cache = {}
        Scanner({**CONFIG, "grades": ["10"]}, FakeEbay(), psa, cache).run()
        self.assertEqual(psa.calls, 1)
        self.assertEqual(cache["psa_usage"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()
