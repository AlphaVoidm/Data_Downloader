"""Tests for country normalization utilities."""
import unittest

from country_utils import get_country_coordinates, get_country_name, normalize_country


class NormalizeCountryTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(normalize_country("Germany"), "DEU")
        self.assertEqual(normalize_country("United States of America"), "USA")
        self.assertEqual(normalize_country("Egypt"), "EGY")

    def test_codes(self):
        self.assertEqual(normalize_country("deu"), "DEU")
        self.assertEqual(normalize_country("DE"), "DEU")
        self.assertEqual(normalize_country("GB"), "GBR")

    def test_aliases(self):
        self.assertEqual(normalize_country("Kosovo"), "XKX")
        self.assertEqual(normalize_country("Taiwan"), "TWN")

    def test_unknown(self):
        self.assertIsNone(normalize_country("Atlantis"))

    def test_non_country_words_do_not_match(self):
        # Regression: header words must not fuzzy-match a country.
        self.assertIsNone(normalize_country("Country"))
        self.assertIsNone(normalize_country("Continent"))

    def test_coordinates(self):
        self.assertIsNotNone(get_country_coordinates("EGY"))
        self.assertEqual(get_country_name("EGY"), "Egypt")


if __name__ == "__main__":
    unittest.main()
