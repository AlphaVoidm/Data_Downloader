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
        # 1 target + 15 core explanatory (locked hierarchy)
        self.assertEqual(len(get_core_features()), 16)

    def test_extended_count(self):
        self.assertEqual(len(get_extended_features()), 4)

    def test_optional_count(self):
        self.assertEqual(len(get_optional_features()), 4)

    def test_excluded_count(self):
        from feature_registry import get_excluded_features
        self.assertEqual([f.concept for f in get_excluded_features()],
                         ["ac_heat_pump_penetration"])

    def test_ac_excluded_and_holidays_optional(self):
        concepts = {f.concept for f in FEATURE_REGISTRY.values()}
        self.assertIn("ac_heat_pump_penetration", concepts)
        self.assertIn("public_holidays", concepts)
        self.assertEqual(get_target_feature().tier, "target")
        ac = FEATURE_REGISTRY["ac_heat_pump_penetration"]
        self.assertEqual(ac.tier, "excluded")  # never gates the pipeline
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

    def test_capability_matrix_classifies_modes_and_roles(self):
        from source_registry import get_source_capability_matrix, get_source
        matrix = {r["source_id"]: r for r in get_source_capability_matrix()}
        self.assertEqual(matrix["ember"]["acquisition_mode"], "api_country_query")
        self.assertEqual(matrix["era5"]["acquisition_mode"], "bulk_job")
        self.assertEqual(matrix["iea"]["acquisition_mode"], "restricted")
        # CMIP6 is registered but explicitly future-scenario only.
        self.assertEqual(matrix["cmip6"]["role"], "future_scenario")
        self.assertEqual(matrix["cmip6"]["features"], "(none — scenario only)")
        # CMIP6 must not be wired into any feature's source list.
        cmip = get_source("cmip6")
        self.assertEqual(cmip.features, ())


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
