"""Tests for connector internals (URL/auth contract + error classification)."""
import json
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

import pandas as pd

from connectors import ember as ember_mod
from connectors import era5 as era5_mod
from connectors import nasa_power as nasa_mod
from connectors.base import ConnectorError


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self._payload = payload

    @property
    def content(self):
        return json.dumps(self._payload).encode("utf-8")

    def json(self):
        return self._payload


def _gen_rows(series_value_map, date="2024-01-01"):
    rows = []
    for series, twh in series_value_map:
        rows.append({
            "entity": "Egypt", "entity_code": "EGY", "is_aggregate_entity": False,
            "date": date, "series": series, "is_aggregate_series": False,
            "generation_twh": twh, "share_of_generation_pct": 100.0,
        })
    return rows


class EmberUrlTest(unittest.TestCase):
    def test_demand_url_uses_entity_code_and_ym_dates(self):
        url = ember_mod.build_ember_url("electricity-demand", "monthly", "EGY", 2000, 2024)
        self.assertIn("/electricity-demand/monthly", url)
        self.assertIn("entity_code=EGY", url)          # ISO code, NOT the name param
        self.assertNotIn("entity=EGY", url)
        self.assertIn("start_date=2000-01", url)       # monthly endpoints take YYYY-MM
        self.assertIn("end_date=2024-12", url)

    def test_yearly_url_uses_year_dates(self):
        url = ember_mod.build_ember_url("electricity-generation", "yearly", "BRA", 2000, 2024)
        self.assertIn("entity_code=BRA", url)
        self.assertIn("start_date=2000", url)
        self.assertIn("end_date=2024", url)

    def test_resolve_entity_from_options(self):
        options = {"data": [
            {"entity_code": "AFG", "entity": "Afghanistan"},
            {"entity_code": "EGY", "entity": "Egypt"},
        ]}
        with mock.patch("connectors.ember._HTTP.get",
                        return_value=_FakeResponse(options)) as get:
            entity = ember_mod.resolve_entity("EGY", "k", "electricity-demand", "monthly")
        self.assertEqual(entity["entity_code"], "EGY")
        self.assertEqual(entity["entity_name"], "Egypt")
        self.assertEqual(entity["resolution_method"], "options")
        # options endpoint queried with the entity_code filter first
        self.assertIn("options/electricity-demand/monthly/entity_code", get.call_args.args[0])

    def test_resolve_entity_falls_back_to_iso3(self):
        with mock.patch("connectors.ember._HTTP.get",
                        side_effect=ConnectorError("NETWORK_ERROR")) as get:
            entity = ember_mod.resolve_entity("EGY", "k", "electricity-demand", "monthly")
        self.assertEqual(entity["entity_code"], "EGY")
        self.assertEqual(entity["resolution_method"], "iso3_fallback")

    def test_uses_api_key_query_param_not_bearer(self):
        with mock.patch("connectors.ember._HTTP.get", return_value=_FakeResponse(_gen_rows([("Demand", 12.0)]))) as get:
            ember_mod.acquire_ember("EGY", "electricity_demand", 2024, 2024, "test-key", Path(tempfile.mkdtemp()))
            kwargs = get.call_args.kwargs
            self.assertEqual(kwargs["params"]["api_key"], "test-key")
            self.assertIn("electricity-demand", get.call_args.args[0])
            # no Authorization header sent
            self.assertNotIn("Authorization", kwargs.get("headers") or {})


class EmberExtractTest(unittest.TestCase):
    def test_demand_from_generation_series(self):
        df = ember_mod._extract(
            _gen_df(_gen_rows([("Coal", 3.0), ("Demand", 12.5), ("Total generation", 11.0)])),
            "electricity_demand",
        )
        self.assertIsNotNone(df[0])
        self.assertEqual(df[0].iloc[0]["value"], 12.5)

    def test_demand_from_dedicated_dataset(self):
        df = ember_mod._extract(
            _gen_df([{"entity": "Egypt", "entity_code": "EGY", "date": "2024-01-01", "demand_twh": 9.9}]),
            "electricity_demand",
        )
        self.assertIsNotNone(df[0])
        self.assertEqual(df[0].iloc[0]["value"], 9.9)

    def test_generation_uses_total_generation_series(self):
        df = ember_mod._extract(
            _gen_df(_gen_rows([("Coal", 3.0), ("Total generation", 11.0)])),
            "total_electricity_generation",
        )
        self.assertEqual(df[0].iloc[0]["value"], 11.0)

    def test_renewable_share_uses_clean_series(self):
        rows = [
            {"entity": "Egypt", "entity_code": "EGY", "date": "2024-01-01", "series": "Clean",
             "generation_twh": 6.0, "share_of_generation_pct": 42.0},
            {"entity": "Egypt", "entity_code": "EGY", "date": "2024-01-01", "series": "Fossil",
             "generation_twh": 8.0, "share_of_generation_pct": 58.0},
        ]
        df = ember_mod._extract(_gen_df(rows), "renewable_generation_share")
        self.assertEqual(df[0].iloc[0]["value"], 42.0)


def _gen_df(rows):
    import pandas as pd
    return pd.DataFrame(rows)


class Era5ErrorTest(unittest.TestCase):
    def test_auth_error_detected(self):
        status, reason = era5_mod._classify_cds_error(Exception("The request you have submitted is not valid (401)"))
        self.assertEqual(status, "AUTH_FAILED")

    def test_network_error_detected(self):
        status, _ = era5_mod._classify_cds_error(Exception("SSLZeroReturnError TLS connection closed"))
        self.assertEqual(status, "NETWORK_ERROR")

    def test_timeout_detected(self):
        status, _ = era5_mod._classify_cds_error(Exception("Read timed out."))
        self.assertEqual(status, "TIMEOUT")


class NasaErrorTest(unittest.TestCase):
    def test_network(self):
        self.assertEqual(nasa_mod._classify_nasa_error(Exception("Connection aborted")), "NETWORK_ERROR")

    def test_timeout(self):
        self.assertEqual(nasa_mod._classify_nasa_error(Exception("ReadTimeout")), "TIMEOUT")

    def test_monthly_aggregation_rounds_numeric_only(self):
        # Rounding the whole frame used to emit a "round has no effect with
        # datetime" warning; numeric-only rounding must not warn and must
        # leave the datetime column intact.
        daily = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-01", "2024-01-02",
                                    "2024-02-01", "2024-02-02"]),
            "t2m": [10.12345, 20.0, 5.0, 6.0],
            "solar": [1.1, 2.2, 3.3, 4.4],
            "wind": [2.1, 3.2, 4.3, 5.4],
            "precip": [0.1, 0.2, 0.3, 0.4],
        })
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning becomes an error
            monthly = nasa_mod._aggregate_monthly(daily)
        self.assertIn("date", monthly.columns)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(monthly["date"]))
        # numeric columns are rounded to 3 dp
        expected = round((10.12345 + 20.0) / 2, 3)
        self.assertAlmostEqual(monthly["temperature_2m"].iloc[0], expected, places=3)


if __name__ == "__main__":
    unittest.main()
