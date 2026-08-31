"""Tests for the gridded-source acquisition paths.

* ERA5/CMIP6 are globally gridded and must NEVER be classified as
  "SOURCE_NOT_COVERED" for a country — acquisition is via spatial subset.
* CMIP6 experiment/variable resolution and the chunked spatial-subset
  connector (mocked at the CDS/xarray boundary).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from source_mapping import validate_source_capability
from extraction.cmip6_extractor import (
    normalize_experiment,
    resolve_variable,
    _pick_nc_name,
)
from connectors import cmip6 as cmip6_mod
from connectors import era5 as era5_mod


class GridSourceCoverageTest(unittest.TestCase):
    def test_era5_is_globally_covered(self):
        status, msg = validate_source_capability("EGY", "ERA5 / CDS")
        self.assertEqual(status, "OK")
        self.assertIn("bbox", msg)

    def test_cmip6_is_globally_covered(self):
        status, msg = validate_source_capability("EGY", "CMIP6 / CDS")
        self.assertEqual(status, "OK")
        self.assertIn("bbox", msg)

    def test_national_sources_still_not_covered(self):
        # Genuinely territory-limited sources keep honest classification.
        self.assertEqual(validate_source_capability("EGY", "ESO / NESO")[0], "SOURCE_NOT_COVERED")
        self.assertEqual(validate_source_capability("EGY", "EIA Open Data")[0], "SOURCE_NOT_COVERED")


class CMIP6ResolutionTest(unittest.TestCase):
    def test_experiment_aliases(self):
        self.assertEqual(normalize_experiment("ssp245"), "ssp2_4_5")
        self.assertEqual(normalize_experiment("ssp2_4_5"), "ssp2_4_5")
        self.assertEqual(normalize_experiment("historical"), "historical")

    def test_variable_resolution(self):
        spec = resolve_variable("tas")
        self.assertEqual(spec["cds_name"], "near_surface_air_temperature")
        self.assertIn("tas", spec["nc_names"])
        self.assertIsNone(resolve_variable("not_a_variable"))

    def test_netcdf_variable_selection(self):
        class _Var:
            def __init__(self, name):
                self.name = name
                self.dims = ("time", "lat", "lon")
        class _DS:
            def __init__(self):
                self.data_vars = {"tas": _Var("tas"), "pr": _Var("pr")}
            def __contains__(self, item):
                return item in self.data_vars
        self.assertEqual(_pick_nc_name(_DS(), ["tas"]), "tas")
        self.assertEqual(_pick_nc_name(_DS(), ["near_surface_air_temperature", "tas"]), "tas")


class CMIP6ConnectorTest(unittest.TestCase):
    def test_no_credentials_is_auth_failed(self):
        with mock.patch.object(cmip6_mod, "cds_credentials_available", return_value=False):
            out = cmip6_mod.acquire_cmip6(
                "EGY", "tas", "ssp245", None, 2015, 2020, None, Path(tempfile.mkdtemp()))
        self.assertEqual(out.status, "AUTH_FAILED")

    def test_unsupported_variable_schema_mismatch(self):
        with mock.patch.object(cmip6_mod, "cds_credentials_available", return_value=True):
            out = cmip6_mod.acquire_cmip6(
                "EGY", "bogus", "historical", None, 2000, 2001, None, Path(tempfile.mkdtemp()))
        self.assertEqual(out.status, "SCHEMA_MISMATCH")

    def test_diagnose_reports_bbox_and_experiment(self):
        diag = cmip6_mod.diagnose_cmip6("EGY", "tas", "ssp245", None, 2015, 2100, None)
        self.assertEqual(diag["experiment"], "ssp2_4_5")
        self.assertEqual(diag["cds_variable"], "near_surface_air_temperature")
        self.assertEqual(diag["bbox"], [31.67, 24.7, 21.72, 36.89])


if __name__ == "__main__":
    unittest.main()
