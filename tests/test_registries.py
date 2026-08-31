"""Tests for the three registries (Country / Feature / Source)."""
import unittest

from country_registry import (
    COUNTRY_REGISTRY,
    get_country_bbox,
    get_country_record,
    get_all_countries,
)
from feature_registry import (
    FEATURE_REGISTRY,
    get_all_features,
    get_feature,
    get_mandatory_features,
)
from source_registry import (
    SOURCE_REGISTRY,
    get_source_metadata,
    get_sources_for_variable,
)


class CountryRegistryTest(unittest.TestCase):
    def test_registry_is_populated(self):
        self.assertGreater(len(COUNTRY_REGISTRY), 100)

    def test_curated_bbox_present(self):
        rec = get_country_record("EGY")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.bbox_source, "curated")
        n, w, s, e = get_country_bbox("EGY")
        self.assertGreater(n, s)  # north > south
        self.assertLess(w, e)     # west < east

    def test_centroid_fallback_bbox(self):
        rec = get_country_record("AND")  # Andorra: not curated
        self.assertIsNotNone(rec)
        self.assertEqual(rec.bbox_source, "centroid_derived")

    def test_all_countries_sorted(self):
        codes = [r.iso3 for r in get_all_countries()]
        self.assertEqual(codes, sorted(codes))


class FeatureRegistryTest(unittest.TestCase):
    def test_25_features(self):
        self.assertEqual(len(FEATURE_REGISTRY), 25)

    def test_feature_ids(self):
        self.assertIn("VAR_01", FEATURE_REGISTRY)
        self.assertIn("VAR_25", FEATURE_REGISTRY)

    def test_mandatory_present(self):
        mandatory = {f.concept for f in get_mandatory_features()}
        self.assertIn("electricity_demand", mandatory)
        self.assertIn("temperature", mandatory)

    def test_demand_candidates_ordered(self):
        demand = get_feature("VAR_01")
        self.assertEqual(demand.concept, "electricity_demand")
        self.assertTrue(demand.is_target)
        self.assertEqual(demand.source_candidates[0], "Ember")

    def test_derived_features(self):
        cdd = get_feature("VAR_24")
        self.assertTrue(cdd.is_derived)
        self.assertEqual(cdd.derived_from, "VAR_02")


class SourceRegistryTest(unittest.TestCase):
    def test_registry_is_populated(self):
        self.assertGreater(len(SOURCE_REGISTRY), 10)

    def test_era5_geospatial_and_credentialed(self):
        meta = get_source_metadata("ERA5 / CDS")
        self.assertEqual(meta.dataset_type, "geospatial")
        self.assertEqual(meta.auth_required, "cds_credentials")
        self.assertEqual(meta.credential_env, "CDS_API_KEY")

    def test_iea_restricted(self):
        meta = get_source_metadata("IEA")
        self.assertEqual(meta.auth_required, "restricted")

    def test_variable_lookup(self):
        sources = {s.source for s in get_sources_for_variable("temperature")}
        self.assertIn("ERA5 / CDS", sources)
        self.assertIn("NASA POWER", sources)


if __name__ == "__main__":
    unittest.main()
