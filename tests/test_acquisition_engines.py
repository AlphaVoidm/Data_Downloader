"""Tests for the three acquisition engines + the bulk/scenario connectors
(IIASA SSP, GPWv4), the Ember bulk fallback, and the panel layer."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from connectors import iiasa as iiasa_mod
from connectors import gpwv4 as gpwv4_mod
from connectors import ember as ember_mod
from panel import build_country_panel, FEATURE_SPECS, DEFAULT_PANEL_FEATURES


# ---------------------------------------------------------------- IIASA SSP

def _iamc_csv() -> pd.DataFrame:
    years = {str(y): [0] * 6 for y in range(2010, 2110, 10)}
    rows = [
        ["IIASA GDP", "SSP2", "EGY", "GDP|PPP", "billion US$2017/yr"],
        ["IIASA GDP", "SSP2", "DEU", "GDP|PPP", "billion US$2017/yr"],
        ["IIASA-WiC POP", "SSP2", "EGY", "Population", "millions"],
        ["IIASA-WiC POP", "SSP1", "EGY", "Population", "millions"],
        ["IIASA-WiC POP", "SSP2", "World", "Population", "millions"],
    ]
    for i, row in enumerate(rows):
        for j, y in enumerate(sorted(years)):
            row.append(100.0 * (i + 1) + j)
    cols = ["Model", "Scenario", "Region", "Variable", "Unit"] + sorted(years)
    return pd.DataFrame(rows, columns=cols)


class IiasaExtractionTest(unittest.TestCase):
    def test_extract_country_rows(self):
        df = _iamc_csv()
        long = iiasa_mod.extract_ssp_country(df, "EGY", "Population", "SSP2", 2010, 2100)
        self.assertFalse(long.empty)
        self.assertEqual(set(long["variable"]), {"Population"})
        self.assertEqual(set(long["scenario"]), {"SSP2"})
        self.assertEqual(set(long["iso3"]), {"EGY"})
        # 'World' and other-country rows excluded
        self.assertNotIn("World", set(long["iso3"]))

    def test_scenario_normalization(self):
        self.assertEqual(iiasa_mod.normalize_scenario("SSP2-Baseline"), "SSP2")
        self.assertEqual(iiasa_mod.normalize_scenario("ssp2"), "SSP2")

    def test_read_ssp_bulk_parses_years(self):
        path = Path(tempfile.mkdtemp()) / "ssp.csv"
        _iamc_csv().to_csv(path, index=False)
        bulk = iiasa_mod.read_ssp_bulk(path)
        self.assertIn("Region", bulk.columns)
        self.assertIn("Variable", bulk.columns)

    def test_acquire_caches_bulk_and_writes_rows(self):
        root = Path(tempfile.mkdtemp())
        with mock.patch.object(iiasa_mod, "download_ssp_bulk") as dl, \
             mock.patch.object(iiasa_mod, "read_ssp_bulk", return_value=_iamc_csv()) as rd:
            dl.return_value = root / "_cache" / "ssp.csv"
            outcome = iiasa_mod.acquire_iiasa("EGY", "ssp_population", "SSP2", 2010, 2100, root)
        self.assertEqual(outcome.status, "SUCCESS")
        self.assertTrue(Path(outcome.path).exists())


# ------------------------------------------------------------------- GPWv4

class Gpwv4ZonalTest(unittest.TestCase):
    def test_cell_area_km2_shape(self):
        lats = np.array([0.0, 10.0, 30.0])
        area = gpwv4_mod.cell_area_km2(lats, 1.0, 1.0)
        self.assertEqual(area.shape, (3, 1))
        self.assertAlmostEqual(area[0, 0], 111.32 * 111.32, places=0)
        self.assertLess(area[2, 0], area[0, 0])  # cos(30) < 1

    def test_zonal_sum_excludes_nodata(self):
        density = np.array([[1.0, np.nan], [2.0, 3.0]])  # persons/km2
        lats = np.array([0.0, 0.0])
        stats = gpwv4_mod.zonal_sum(density, lats, 1.0, 1.0)
        cell = 111.32 ** 2
        self.assertAlmostEqual(stats["population"], (1 + 2 + 3) * cell, places=-3)
        self.assertEqual(stats["cell_count"], 3)

    def test_years_in_range(self):
        self.assertEqual(gpwv4_mod._years_in_range(2000, 2020), [2000, 2005, 2010, 2015, 2020])
        self.assertEqual(gpwv4_mod._years_in_range(2016, 2019), [])

    def test_no_credentials_and_dependency_gating(self):
        # With rasterio installed but no network, the connector must degrade
        # honestly (network error), never to "not covered".
        with mock.patch.object(gpwv4_mod, "_download_zip",
                               side_effect=RuntimeError("Could not download GPWv4 raster: HTTP 403")):
            outcome = gpwv4_mod.acquire_gpwv4("EGY", "total_population", 2010, 2020, Path(tempfile.mkdtemp()))
        self.assertEqual(outcome.status, "NETWORK_ERROR")
        self.assertNotEqual(outcome.status, "SOURCE_NOT_COVERED")


# ------------------------------------------------------------ Ember bulk

class _FakeResp:
    def __init__(self, status, content):
        self.status_code = status
        self.content = content
        self.headers = {"Content-Type": "text/csv"}


class EmberBulkFallbackTest(unittest.TestCase):
    def test_bulk_demand_extracts_genuine_demand_series(self):
        csv_body = (
            "entity,entity_code,is_aggregate_entity,date,series,is_aggregate_series,"
            "generation_twh,share_of_generation_pct\n"
            "Egypt,EGY,FALSE,2000-01-01,Demand,TRUE,10.5,100\n"
            "Egypt,EGY,FALSE,2000-01-01,Total generation,TRUE,10.0,95\n"
            "Egypt,EGY,FALSE,2000-02-01,Demand,TRUE,11.0,100\n"
            "Germany,DEU,FALSE,2000-01-01,Demand,TRUE,50.0,100\n"
        ).encode()
        with mock.patch("connectors.ember._HTTP.get", return_value=_FakeResp(200, csv_body)):
            df = ember_mod._bulk_demand("EGY", 2000, 2000)
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 2)                 # only EGY demand rows
        self.assertAlmostEqual(df.iloc[0]["value"], 10.5)
        # "Total generation" is never used as demand
        self.assertNotIn("Total generation", set(df["value"].astype(str)))


# ---------------------------------------------------------------- Panel

def _write_feature_files(root: Path):
    (root / "raw" / "electricity" / "demand" / "ember").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": ["2000-01-01", "2000-02-01", "2000-03-01"],
        "value": [10.0, 11.0, 12.0],
    }).to_csv(root / "raw" / "electricity" / "demand" / "ember" / "EGY.csv", index=False)

    (root / "climate").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": ["2000-01-01", "2000-02-01", "2000-03-01"],
        "temperature_2m": [15.0, 16.0, 17.0],
        "solar_radiation": [5.0, 5.5, 6.0],
        "wind_speed_10m": [3.0, 3.2, 3.1],
        "precipitation": [1.0, 2.0, 3.0],
    }).to_csv(root / "climate" / "EGY_nasa_power.csv", index=False)

    (root / "raw" / "socioeconomic" / "indicators" / "worldbank").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "iso3": ["EGY", "EGY"],
        "year": [2000, 2001],
        "indicator": ["gdp", "gdp"],
        "indicator_code": ["NY.GDP.MKTP.CD", "NY.GDP.MKTP.CD"],
        "value": [998.0, 976.0],
        "unit": ["current USD", "current USD"],
        "observed": [True, True],
    }).to_csv(root / "raw" / "socioeconomic" / "indicators" / "worldbank" / "EGY.csv", index=False)


class PanelBuildTest(unittest.TestCase):
    def test_panel_aligns_monthly_and_annual_without_filling(self):
        root = Path(tempfile.mkdtemp())
        _write_feature_files(root)
        paths = build_country_panel("EGY", 2000, 2000, root)
        panel = pd.read_parquet(paths["panel"])

        self.assertEqual(len(panel), 12)             # 12 months of 2000
        self.assertEqual(set(panel["iso3"]), {"EGY"})
        # monthly climate filled for all 12 months? only 3 provided -> others NaN (no fill)
        self.assertEqual(panel["temperature_2m"].notna().sum(), 3)
        # annual gdp -> January row only, never forward-filled
        gdp = panel[panel["gdp"].notna()]
        self.assertEqual(len(gdp), 1)
        self.assertEqual(gdp["date"].dt.month.iloc[0], 1)

        prov = pd.read_csv(paths["provenance"])
        self.assertIn("feature", prov.columns)
        self.assertIn("quality_flag", prov.columns)
        missing = prov[prov["quality_flag"] == "MISSING"]
        self.assertFalse(missing.empty)              # e.g. population not acquired

    def test_annual_feature_never_interpolated(self):
        root = Path(tempfile.mkdtemp())
        _write_feature_files(root)
        paths = build_country_panel("EGY", 2000, 2001, root)
        panel = pd.read_parquet(paths["panel"])
        # gdp present in exactly 2 rows (2000 and 2001 January), not 24
        self.assertEqual(panel["gdp"].notna().sum(), 2)


# ---------------------------------------------------------------- Engines

class EngineDispatchTest(unittest.TestCase):
    def test_mode_dispatch(self):
        from engines import get_engine
        self.assertEqual(get_engine("ember").__name__, "engines.country_api_engine")
        self.assertEqual(get_engine("era5").__name__, "engines.grid_engine")
        self.assertEqual(get_engine("cmip6").__name__, "engines.grid_engine")
        self.assertEqual(get_engine("gpwv4").__name__, "engines.grid_engine")
        self.assertEqual(get_engine("nasa_power").__name__, "engines.grid_engine")
        self.assertEqual(get_engine("iiasa").__name__, "engines.scenario_bulk_engine")
        self.assertEqual(get_engine("iea").__name__, "engines.restricted")

    def test_restricted_engine_reports_honestly(self):
        from engines.restricted import acquire
        verification, outcome = acquire("iea", "EGY", "electricity_prices", 2000, 2024, None, Path(tempfile.mkdtemp()))
        self.assertEqual(outcome.status, "NOT_SUPPORTED")
        self.assertIn("restricted", outcome.message)

    def test_country_api_engine_resolves_connector(self):
        from engines.country_api_engine import acquire
        with mock.patch("connectors.world_bank.world_bank_connector",
                        return_value=(mock.MagicMock(status="VERIFIED"),
                                      mock.MagicMock(status="SUCCESS"))) as conn:
            verification, outcome = acquire("world_bank", "EGY", "gdp", 2000, 2024, None, Path(tempfile.mkdtemp()))
        conn.assert_called_once()
        self.assertEqual(outcome.status, "SUCCESS")


if __name__ == "__main__":
    unittest.main()
