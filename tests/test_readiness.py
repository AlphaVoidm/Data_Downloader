"""Tests for HGT-QF three-tier readiness + geographic diversity."""
import unittest

from research_config import ResearchConfig
from readiness import (
    RESEARCH_NOT_READY,
    RESEARCH_READY,
    diversity_region,
    evaluate_feature_coverage,
    evaluate_readiness,
    evaluate_research_readiness,
    evaluate_target_readiness,
    select_diverse_countries,
)


class TargetReadinessTest(unittest.TestCase):
    def test_egypt_monthly_sufficient(self):
        t = evaluate_target_readiness("EGY", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(t["status"], "MONTHLY_SUFFICIENT")
        self.assertTrue(t["target_ready"])
        self.assertEqual(t["expected_months"], 300)
        self.assertEqual(t["longest_continuous_run"], 300)

    def test_botswana_annual_only(self):
        t = evaluate_target_readiness("BWA", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(t["status"], "ANNUAL_ONLY")
        self.assertFalse(t["target_ready"])
        self.assertEqual(t["observed_months"], 0)
        self.assertEqual(t["longest_continuous_run"], 0)


class FeatureCoverageTest(unittest.TestCase):
    def test_egypt_core_coverage(self):
        c = evaluate_feature_coverage("EGY", 2000, 2024, {"EMBER_API_KEY": "x", "CDS_API_KEY": "x"})
        self.assertEqual(c["core"]["total"], 13)
        self.assertEqual(c["core_coverage"], "13/13")
        self.assertEqual(c["extended_coverage"], "6/6")

    def test_optional_not_required(self):
        # Optional coverage varies but never gates readiness.
        c = evaluate_feature_coverage("EGY", 2000, 2024, {"EMBER_API_KEY": "x", "CDS_API_KEY": "x"})
        self.assertLessEqual(c["optional"]["available"], c["optional"]["total"])


class ResearchReadinessTest(unittest.TestCase):
    def test_egypt_research_ready(self):
        r = evaluate_research_readiness("EGY", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(r["target_status"], "MONTHLY_SUFFICIENT")
        self.assertEqual(r["research_ready"], RESEARCH_READY)

    def test_botswana_not_ready_annual_only(self):
        r = evaluate_research_readiness("BWA", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(r["research_ready"], RESEARCH_NOT_READY)
        self.assertIn("annual", r["reason"].lower())

    def test_optional_requirement_flips_ready(self):
        cfg = ResearchConfig(require_optional_features=True)
        r = evaluate_research_readiness("EGY", 2000, 2024, {"EMBER_API_KEY": "x"}, cfg)
        self.assertEqual(r["research_ready"], RESEARCH_NOT_READY)

    def test_min_history_threshold_flips_ready(self):
        cfg = ResearchConfig(min_history_months=600, min_consecutive_months=600)
        r = evaluate_research_readiness("EGY", 2000, 2024, {"EMBER_API_KEY": "x"}, cfg)
        self.assertEqual(r["research_ready"], RESEARCH_NOT_READY)

    def test_min_core_coverage_gates(self):
        cfg = ResearchConfig(min_core_coverage=1.0)
        r = evaluate_research_readiness("EGY", 2000, 2024, {"EMBER_API_KEY": "x"}, cfg)
        self.assertEqual(r["research_ready"], RESEARCH_READY)  # 14/14 satisfies 100%

    def test_readiness_has_evidence_metrics(self):
        r = evaluate_readiness("EGY", 2000, 2024, {"EMBER_API_KEY": "x"})
        for key in ("first_month", "last_month", "expected_months", "observed_months",
                    "longest_continuous_run", "gap_count", "core_coverage"):
            self.assertIn(key, r)


class DiversityTest(unittest.TestCase):
    def test_diversity_region_overrides(self):
        self.assertEqual(diversity_region("SAU", "Asia"), "Middle East")
        self.assertEqual(diversity_region("VNM", "Asia"), "Southeast Asia")
        self.assertEqual(diversity_region("MEX", "North America"), "Latin America")
        self.assertEqual(diversity_region("BRA", "South America"), "South America")
        self.assertEqual(diversity_region("EGY", "Africa"), "Africa")

    def test_select_diverse_spans_regions(self):
        rows = [
            {"iso3": c, "research_ready": RESEARCH_READY, "target_status": "MONTHLY_SUFFICIENT",
             "longest_continuous_run": 200, "core_ratio": 1.0, "country": c,
             "core_coverage": "14/14", "optional_coverage": "0/5"}
            for c in ["EGY", "USA", "BRA", "VNM", "SAU", "DEU", "AUS", "JPN", "KEN", "MEX", "ARG", "IDN"]
        ]
        selected = select_diverse_countries(rows, max_per_region=2)
        regions = {r["region"] for r in selected}
        self.assertGreaterEqual(len(regions), 5)

    def test_select_excludes_annual_only(self):
        rows = [
            {"iso3": "BWA", "research_ready": RESEARCH_NOT_READY, "target_status": "ANNUAL_ONLY",
             "longest_continuous_run": 0, "core_ratio": 1.0, "country": "Botswana",
             "core_coverage": "14/14", "optional_coverage": "0/5"},
            {"iso3": "EGY", "research_ready": RESEARCH_READY, "target_status": "MONTHLY_SUFFICIENT",
             "longest_continuous_run": 300, "core_ratio": 1.0, "country": "Egypt",
             "core_coverage": "14/14", "optional_coverage": "0/5"},
        ]
        selected = select_diverse_countries(rows, max_per_region=5)
        self.assertEqual([r["iso3"] for r in selected], ["EGY"])


if __name__ == "__main__":
    unittest.main()
