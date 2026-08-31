"""Tests for connector internals (URL/auth contract + error classification)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from connectors import ember as ember_mod
from connectors import era5 as era5_mod
from connectors import nasa_power as nasa_mod


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
    def test_demand_url(self):
        url = ember_mod.build_ember_url("electricity-demand", "monthly", "EGY", "2000", "2024")
        self.assertIn("/electricity-demand/monthly", url)
        self.assertIn("entity=EGY", url)
        self.assertIn("start_date=2000", url)
        self.assertIn("end_date=2024", url)

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


if __name__ == "__main__":
    unittest.main()
