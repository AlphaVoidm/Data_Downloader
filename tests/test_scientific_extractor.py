"""Tests for the scientific geospatial extractor (Component 6)."""
import unittest

import numpy as np
import pandas as pd

from scientific_extractor import (
    ERA5_VARIABLES,
    aggregate_grid_to_series,
    extract_era5_monthly_country,
    _area_weights,
)


class AreaWeightsTest(unittest.TestCase):
    def test_equator_weight_is_one(self):
        w = _area_weights(np.array([0.0, 60.0, -90.0]))
        self.assertAlmostEqual(w[0], 1.0)
        self.assertAlmostEqual(w[1], 0.5)
        self.assertAlmostEqual(w[2], 0.0)


class AggregationTest(unittest.TestCase):
    def _make_dataset(self):
        import xarray as xr
        time = pd.date_range("2000-01-01", periods=2, freq="MS")
        lat = np.array([0.0, 10.0])
        lon = np.array([0.0, 10.0])
        # temperature field in Kelvin; constant per time step
        t = np.ones((2, 2, 2)) * 300.0
        t[:, :, 1] = 280.0  # half the grid colder
        ds = xr.Dataset(
            {"2m_temperature": (("time", "latitude", "longitude"), t)},
            coords={"time": time, "latitude": lat, "longitude": lon},
        )
        return ds

    def test_aggregate_temperature_returns_celsius(self):
        ds = self._make_dataset()
        df = aggregate_grid_to_series(ds, {"temperature": ERA5_VARIABLES["temperature"]})
        self.assertEqual(len(df), 2)
        # area-weighted mean of 300 and 280 (equal weights) = 290 K -> 16.85 °C
        self.assertAlmostEqual(df["temperature_c"].iloc[0], 290.0 - 273.15, places=2)

    def test_missing_variable_is_skipped(self):
        ds = self._make_dataset()
        df = aggregate_grid_to_series(ds, {"precipitation": ERA5_VARIABLES["precipitation"]})
        self.assertTrue(df.empty or "precipitation_mm" not in df.columns)


class ExtractorStatusTest(unittest.TestCase):
    def test_no_credentials_returns_access_required(self):
        result = extract_era5_monthly_country("EGY", variables=["temperature"], start_year=2000, end_year=2001)
        self.assertEqual(result.status, "ACCESS_REQUIRES_AUTH")
        self.assertEqual(result.records, 0)

    def test_missing_bbox_returns_period_not_available(self):
        result = extract_era5_monthly_country("ZZZ", variables=["temperature"])
        self.assertEqual(result.status, "PERIOD_NOT_AVAILABLE")

    def test_unknown_variable_returns_parse_error(self):
        result = extract_era5_monthly_country("EGY", variables=["not_a_var"])
        self.assertEqual(result.status, "PARSE_ERROR")


if __name__ == "__main__":
    unittest.main()
