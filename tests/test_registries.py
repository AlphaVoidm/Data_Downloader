"""Tests for the redesigned registries (country / feature / source)."""
import unittest

from country_registry import COUNTRY_REGISTRY, get_country_bbox, get_country_record
from feature_registry import (
    FEATURE_REGISTRY,
    get_core_features,
    get_extended_features,
    get_optional_features,
    get_target_feature,
)
from source_registry import (
    SOURCE_REGISTRY,
    get_all_registered_sources,
    get_source,
    get_source_metadata,
)


class CountryRegistryTest(unittest.TestCase):
    def test_registry_is_populated(self):
        self.assertGreater(len(COUNTRY_REGISTRY), 150)

    def test_gbr_is_europe(self):
        rec = get_country_record("GBR")
        self.assertEqual(rec.region, "Europe")

    def test_bbox_present(self):
        n, w, s, e = get_country_bbox("EGY")
        self.assertGreater(n, s)
        self.assertLess(w, e)


class FeatureRegistryTest(unittest.TestCase):
    def test_target_feature_is_demand(self):
        target = get_target_feature()
        self.assertEqual(target.concept, "electricity_demand")
        self.assertTrue(target.is_target)

    def test_core_count(self):
        # 1 target + 13 core explanatory
        self.assertEqual(len(get_core_features()), 14)

    def test_extended_count(self):
        self.assertEqual(len(get_extended_features()), 6)

    def test_optional_count(self):
        self.assertEqual(len(get_optional_features()), 5)

    def test_ac_and_holidays_are_optional(self):
        concepts = {f.concept for f in FEATURE_REGISTRY.values()}
        self.assertIn("ac_heat_pump_penetration", concepts)
        self.assertIn("public_holidays", concepts)
        self.assertEqual(get_target_feature().tier, "target")
        ac = FEATURE_REGISTRY["ac_heat_pump_penetration"]
        self.assertEqual(ac.tier, "optional")
        self.assertEqual(FEATURE_REGISTRY["public_holidays"].tier, "optional")

    def test_demand_source_priority(self):
        demand = get_target_feature()
        self.assertEqual(list(demand.sources)[:2], ["entsoe", "eia"])

    def test_gbr_override(self):
        demand = get_target_feature()
        self.assertEqual(demand.ordered_sources("GBR")[0], "neso")

    def test_feature_aliases_resolve(self):
        from feature_registry import resolve_feature_concept
        self.assertEqual(resolve_feature_concept("wind"), "wind_speed_10m")
        self.assertEqual(resolve_feature_concept("wind_speed"), "wind_speed_10m")
        self.assertEqual(resolve_feature_concept("temperature"), "temperature_2m")
        self.assertEqual(resolve_feature_concept("temp"), "temperature_2m")
        self.assertEqual(resolve_feature_concept("solar"), "solar_radiation")
        self.assertEqual(resolve_feature_concept("demand"), "electricity_demand")

    def test_unknown_feature_helpful_error(self):
        from feature_registry import (
            FeatureNotFoundError,
            format_feature_not_found,
            resolve_feature_concept,
        )
        with self.assertRaises(FeatureNotFoundError):
            resolve_feature_concept("temprature")
        msg = format_feature_not_found("temprature")
        self.assertIn("Did you mean", msg)
        self.assertIn("temperature_2m", msg)
        self.assertIn("Available features", msg)


class SourceRegistryTest(unittest.TestCase):
    def test_registry_populated(self):
        self.assertGreater(len(SOURCE_REGISTRY), 10)

    def test_era5_geospatial_and_credentialed(self):
        src = get_source("era5")
        self.assertEqual(src.auth_env, "CDS_API_KEY")
        self.assertEqual(src.coverage_scope, "global")

    def test_iea_restricted(self):
        src = get_source("iea")
        self.assertTrue(src.auth_required)
        self.assertEqual(src.auth_type, "restricted")

    def test_legacy_lookup(self):
        self.assertIsNotNone(get_source_metadata("Ember"))
        self.assertIsNotNone(get_source_metadata("EIA Open Data"))
        self.assertEqual(get_source_metadata("EIA Open Data").source_id, "eia")

    def test_feature_lookup(self):
        sources = {s.source_id for s in get_all_registered_sources()
                   if "temperature_2m" in s.features}
        self.assertIn("era5", sources)
        self.assertIn("nasa_power", sources)


if __name__ == "__main__":
    unittest.main()
