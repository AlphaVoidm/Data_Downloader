"""Tests for HGT-QF country readiness + geographic diversity."""
import unittest

from readiness import (
    CORE_NOT_READY,
    CORE_READY,
    diversity_region,
    evaluate_readiness,
    select_diverse_countries,
)


class ReadinessTest(unittest.TestCase):
    def test_egypt_core_ready(self):
        r = evaluate_readiness("EGY", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(r["core_readiness"], CORE_READY)
        self.assertEqual(r["demand_status"], "MONTHLY_SUFFICIENT")

    def test_botswana_core_not_ready(self):
        r = evaluate_readiness("BWA", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(r["core_readiness"], CORE_NOT_READY)
        self.assertEqual(r["demand_status"], "ANNUAL_ONLY")

    def test_kenya_core_ready(self):
        r = evaluate_readiness("KEN", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(r["core_readiness"], CORE_READY)

    def test_readiness_has_optional_coverage(self):
        r = evaluate_readiness("DEU", 2000, 2024, {"EMBER_API_KEY": "x", "CDS_API_KEY": "x"})
        self.assertIn("/", r["optional_feature_coverage"])


class DiversityTest(unittest.TestCase):
    def test_diversity_region_overrides(self):
        self.assertEqual(diversity_region("SAU", "Asia"), "Middle East")
        self.assertEqual(diversity_region("VNM", "Asia"), "Southeast Asia")
        self.assertEqual(diversity_region("MEX", "North America"), "Latin America")
        self.assertEqual(diversity_region("BRA", "South America"), "South America")
        self.assertEqual(diversity_region("EGY", "Africa"), "Africa")

    def test_select_diverse_spans_regions(self):
        rows = [
            {"iso3": c, "core_readiness": CORE_READY, "demand_status": "MONTHLY_SUFFICIENT",
             "demand_months": 200, "country": c, "optional_feature_coverage": "0/3"}
            for c in ["EGY", "USA", "BRA", "VNM", "SAU", "DEU", "AUS", "JPN", "KEN", "MEX", "ARG", "IDN"]
        ]
        selected = select_diverse_countries(rows, max_per_region=2)
        regions = {r["region"] for r in selected}
        self.assertGreaterEqual(len(regions), 5)

    def test_select_excludes_annual_only(self):
        rows = [
            {"iso3": "BWA", "core_readiness": CORE_NOT_READY, "demand_status": "ANNUAL_ONLY",
             "demand_months": 0, "country": "Botswana", "optional_feature_coverage": "0/3"},
            {"iso3": "EGY", "core_readiness": CORE_READY, "demand_status": "MONTHLY_SUFFICIENT",
             "demand_months": 300, "country": "Egypt", "optional_feature_coverage": "0/3"},
        ]
        selected = select_diverse_countries(rows, max_per_region=5)
        self.assertEqual([r["iso3"] for r in selected], ["EGY"])


if __name__ == "__main__":
    unittest.main()
