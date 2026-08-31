"""Tests for the redesigned coverage/discovery engine."""
import unittest

from coverage_engine import (
    ANNUAL_ONLY,
    AUTH_REQUIRED,
    MAPPING_REQUIRED,
    MONTHLY_SUFFICIENT,
    NOT_SUPPORTED,
    SUPPORTED,
    classify_demand,
    evaluate_source,
    resolve_feature,
)
from feature_registry import get_feature


class EvaluateSourceTest(unittest.TestCase):
    def test_entsoe_not_supported_for_egypt(self):
        d = evaluate_source("entsoe", get_feature("electricity_demand"), "EGY", 2000, 2024)
        self.assertEqual(d.status, NOT_SUPPORTED)

    def test_eia_not_supported_for_france(self):
        d = evaluate_source("eia", get_feature("electricity_demand"), "FRA", 2000, 2024)
        self.assertEqual(d.status, NOT_SUPPORTED)

    def test_neso_supported_for_gbr(self):
        d = evaluate_source("neso", get_feature("electricity_demand"), "GBR", 2000, 2024)
        self.assertEqual(d.status, SUPPORTED)

    def test_aemo_supported_for_australia(self):
        d = evaluate_source("aemo", get_feature("electricity_demand"), "AUS", 2000, 2024)
        self.assertEqual(d.status, SUPPORTED)

    def test_era5_requires_auth_without_credentials(self):
        d = evaluate_source("era5", get_feature("temperature_2m"), "EGY", 2000, 2024)
        self.assertEqual(d.status, AUTH_REQUIRED)

    def test_era5_supported_with_credentials(self):
        d = evaluate_source("era5", get_feature("temperature_2m"), "EGY", 2000, 2024,
                            credentials={"CDS_API_KEY": "test"})
        self.assertEqual(d.status, SUPPORTED)

    def test_eia_supported_with_credentials(self):
        d = evaluate_source("eia", get_feature("electricity_demand"), "USA", 2000, 2024,
                            credentials={"EIA_API_KEY": "test"})
        self.assertEqual(d.status, SUPPORTED)

    def test_eia_auth_required_without_credentials(self):
        d = evaluate_source("eia", get_feature("electricity_demand"), "USA", 2000, 2024)
        self.assertEqual(d.status, AUTH_REQUIRED)

    def test_entsoe_mapping_required_for_europe_without_eic(self):
        # Ukraine is in the ENTSO-E perimeter but has no EIC code mapping.
        d = evaluate_source("entsoe", get_feature("electricity_demand"), "UKR", 2000, 2024,
                            credentials={"ENTSOE_API_TOKEN": "x"})
        self.assertEqual(d.status, MAPPING_REQUIRED)

    def test_world_bank_global(self):
        d = evaluate_source("world_bank", get_feature("gdp"), "KEN", 2000, 2024)
        self.assertEqual(d.status, SUPPORTED)

    def test_eurostat_eu_only(self):
        d = evaluate_source("eurostat", get_feature("electricity_prices"), "DEU", 2000, 2024)
        self.assertEqual(d.status, SUPPORTED)
        d2 = evaluate_source("eurostat", get_feature("electricity_prices"), "USA", 2000, 2024)
        self.assertEqual(d2.status, NOT_SUPPORTED)

    def test_iea_restricted(self):
        d = evaluate_source("iea", get_feature("ev_stock_sales"), "DEU", 2000, 2024)
        self.assertEqual(d.status, AUTH_REQUIRED)

    def test_ember_annual_only_for_botswana(self):
        d = evaluate_source("ember", get_feature("electricity_demand"), "BWA", 2000, 2024,
                            credentials={"EMBER_API_KEY": "x"})
        self.assertEqual(d.status, SUPPORTED)
        self.assertEqual(d.frequency, "annual")

    def test_ember_monthly_for_egypt(self):
        d = evaluate_source("ember", get_feature("electricity_demand"), "EGY", 2000, 2024,
                            credentials={"EMBER_API_KEY": "x"})
        self.assertEqual(d.status, SUPPORTED)
        self.assertEqual(d.frequency, "monthly")


class ResolveFeatureTest(unittest.TestCase):
    def test_demand_usa_prefers_eia(self):
        plan = resolve_feature("electricity_demand", "USA", 2000, 2024, {"EIA_API_KEY": "x", "EMBER_API_KEY": "x"})
        self.assertEqual(plan.best_status, SUPPORTED)
        self.assertEqual(plan.best_source_id, "eia")

    def test_demand_deu_prefers_entsoe(self):
        plan = resolve_feature("electricity_demand", "DEU", 2000, 2024,
                               {"ENTSOE_API_TOKEN": "x", "EMBER_API_KEY": "x"})
        self.assertEqual(plan.best_source_id, "entsoe")

    def test_demand_gbr_prefers_neso(self):
        plan = resolve_feature("electricity_demand", "GBR", 2000, 2024,
                               {"ENTSOE_API_TOKEN": "x", "EMBER_API_KEY": "x"})
        self.assertEqual(plan.best_source_id, "neso")

    def test_demand_aus_prefers_aemo(self):
        plan = resolve_feature("electricity_demand", "AUS", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(plan.best_source_id, "aemo")

    def test_demand_egypt_uses_ember(self):
        plan = resolve_feature("electricity_demand", "EGY", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(plan.best_source_id, "ember")

    def test_demand_kenya_without_ember_key_is_auth_required(self):
        plan = resolve_feature("electricity_demand", "KEN", 2000, 2024)
        self.assertEqual(plan.best_status, AUTH_REQUIRED)

    def test_temperature_no_creds_uses_nasa(self):
        plan = resolve_feature("temperature_2m", "EGY", 2000, 2024)
        self.assertEqual(plan.best_status, SUPPORTED)
        self.assertEqual(plan.best_source_id, "nasa_power")

    def test_temperature_with_cds_uses_era5(self):
        plan = resolve_feature("temperature_2m", "EGY", 2000, 2024, {"CDS_API_KEY": "x"})
        self.assertEqual(plan.best_source_id, "era5")

    def test_cdd_derived_from_temperature(self):
        plan = resolve_feature("cooling_degree_days", "EGY", 2000, 2024)
        self.assertEqual(plan.best_status, SUPPORTED)
        self.assertEqual(plan.derived_from, "temperature_2m")


class DemandClassificationTest(unittest.TestCase):
    def test_usa_monthly_sufficient(self):
        d = classify_demand("USA", 2000, 2024, {"EIA_API_KEY": "x", "EMBER_API_KEY": "x"})
        self.assertEqual(d["status"], MONTHLY_SUFFICIENT)
        self.assertEqual(d["best_monthly_source"], "EIA Open Data")

    def test_botswana_annual_only(self):
        d = classify_demand("BWA", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(d["status"], ANNUAL_ONLY)

    def test_egypt_monthly_sufficient(self):
        d = classify_demand("EGY", 2000, 2024, {"EMBER_API_KEY": "x"})
        self.assertEqual(d["status"], MONTHLY_SUFFICIENT)
        self.assertEqual(d["best_monthly_source"], "Ember")


if __name__ == "__main__":
    unittest.main()
