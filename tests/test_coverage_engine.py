"""Tests for the Coverage Engine (Component 4) — fully network-free."""
import unittest

from coverage_engine import (
    ACCESS_REQUIRES_AUTH,
    AVAILABLE,
    NOT_COVERED,
    PARTIAL_AVAILABLE,
    PERIOD_NOT_AVAILABLE,
    evaluate_source,
    frequency_satisfies,
    resolve_feature,
    summarize_demand_counts,
)
from feature_registry import get_feature


class FrequencyTest(unittest.TestCase):
    def test_finer_satisfies_monthly(self):
        self.assertTrue(frequency_satisfies("monthly", "hourly")[0])
        self.assertTrue(frequency_satisfies("monthly", "daily")[0])

    def test_annual_does_not_satisfy_monthly(self):
        ok, note = frequency_satisfies("monthly", "annual")
        self.assertFalse(ok)
        self.assertEqual(note, "coarser-than-required")


class EvaluateSourceTest(unittest.TestCase):
    def test_entsoe_not_covered_for_egypt(self):
        d = evaluate_source("ENTSO-E Transparency", get_feature("VAR_01"), "EGY", 2000, 2024)
        self.assertEqual(d.status, NOT_COVERED)

    def test_aemo_available_for_australia(self):
        d = evaluate_source("AEMO", get_feature("VAR_01"), "AUS", 2010, 2020)
        self.assertEqual(d.status, AVAILABLE)

    def test_eia_not_covered_for_france(self):
        d = evaluate_source("EIA Open Data", get_feature("VAR_01"), "FRA", 2000, 2024)
        self.assertEqual(d.status, NOT_COVERED)

    def test_period_not_available(self):
        d = evaluate_source("Ember", get_feature("VAR_01"), "EGY", 1980, 1995)
        self.assertEqual(d.status, PERIOD_NOT_AVAILABLE)

    def test_era5_requires_auth_without_credentials(self):
        d = evaluate_source("ERA5 / CDS", get_feature("VAR_02"), "EGY", 2000, 2024)
        self.assertEqual(d.status, ACCESS_REQUIRES_AUTH)

    def test_era5_available_with_credentials(self):
        d = evaluate_source("ERA5 / CDS", get_feature("VAR_02"), "EGY", 2000, 2024,
                            credentials={"CDS_API_KEY": "test"})
        self.assertEqual(d.status, AVAILABLE)

    def test_world_bank_global(self):
        d = evaluate_source("World Bank", get_feature("VAR_06"), "KEN", 2000, 2024)
        self.assertEqual(d.status, AVAILABLE)

    def test_eurostat_eu_only(self):
        d = evaluate_source("Eurostat", get_feature("VAR_18"), "DEU", 2000, 2024)
        self.assertEqual(d.status, AVAILABLE)
        d2 = evaluate_source("Eurostat", get_feature("VAR_18"), "USA", 2000, 2024)
        self.assertEqual(d2.status, NOT_COVERED)

    def test_iea_restricted_access(self):
        d = evaluate_source("IEA", get_feature("VAR_20"), "DEU", 2000, 2024)
        self.assertEqual(d.status, ACCESS_REQUIRES_AUTH)

    def test_owid_ev_scoped(self):
        d = evaluate_source("OWID", get_feature("VAR_19"), "USA", 2000, 2024)
        self.assertEqual(d.status, AVAILABLE)
        d2 = evaluate_source("OWID", get_feature("VAR_19"), "BWA", 2000, 2024)
        self.assertEqual(d2.status, NOT_COVERED)


class ResolveFeatureTest(unittest.TestCase):
    def test_demand_egypt_prefers_ember(self):
        plan = resolve_feature("VAR_01", "EGY", 2000, 2024)
        self.assertEqual(plan.best_status, AVAILABLE)
        self.assertEqual(plan.best_source, "Ember")

    def test_demand_botswana_annual_only(self):
        plan = resolve_feature("VAR_01", "BWA", 2000, 2024)
        self.assertEqual(plan.best_status, PARTIAL_AVAILABLE)
        self.assertEqual(plan.best_source, "Ember")

    def test_temperature_no_creds_uses_nasa(self):
        plan = resolve_feature("VAR_02", "EGY", 2000, 2024)
        self.assertEqual(plan.best_status, AVAILABLE)
        self.assertEqual(plan.best_source, "NASA POWER")

    def test_temperature_with_cds_uses_era5(self):
        plan = resolve_feature("VAR_02", "EGY", 2000, 2024, credentials={"CDS_API_KEY": "x"})
        self.assertEqual(plan.best_status, AVAILABLE)
        self.assertEqual(plan.best_source, "ERA5 / CDS")

    def test_prices_usa_restricted(self):
        plan = resolve_feature("VAR_18", "USA", 2000, 2024)
        self.assertEqual(plan.best_status, ACCESS_REQUIRES_AUTH)

    def test_prices_germany_open(self):
        plan = resolve_feature("VAR_18", "DEU", 2000, 2024)
        self.assertEqual(plan.best_status, AVAILABLE)
        self.assertEqual(plan.best_source, "Eurostat")

    def test_cdd_derived_from_temperature(self):
        plan = resolve_feature("VAR_24", "EGY", 2000, 2024)
        self.assertEqual(plan.best_status, AVAILABLE)
        self.assertEqual(plan.derived_from_feature, "VAR_02")


class DemandSummaryTest(unittest.TestCase):
    def test_split_monthly_vs_annual(self):
        summary = summarize_demand_counts(["EGY", "DEU", "USA", "BWA", "TCD"], 2000, 2024)
        self.assertEqual(summary["demand_monthly_capable"], 3)   # EGY, DEU, USA
        self.assertEqual(summary["demand_annual_only"], 2)       # BWA, TCD
        self.assertEqual(summary["demand_unavailable"], 0)


if __name__ == "__main__":
    unittest.main()
