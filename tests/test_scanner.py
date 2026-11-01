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

    def test_missing_cert_and_language(self):
        d = filters.item_details(self.ITEM)
        self.assertIsNotNone(filters.details_problem({**d, "cert": None}, "10"))
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
        self.comps = [summary(f"c{i}", "Charmander 168 151 PSA 10", p) for i, p in enumerate([60, 65, 70, 999])]

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
        self.assertEqual(sum(scanner.rejected.values()), 3)


if __name__ == "__main__":
    unittest.main()
