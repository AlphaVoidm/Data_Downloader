"""Tests for source-specific connector semantics:

* World Bank: HTTP 200 + zero records -> NO_DATA_FOR_COUNTRY_INDICATOR (not a failure),
  400/404/429/5xx classified granularly.
* Nager.Date: no holiday records -> NO_DATA, optional feature never blocks.
"""
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from connectors import world_bank as wb
from connectors import misc
from connectors.base import (
    NO_DATA,
    NO_DATA_FOR_COUNTRY_INDICATOR,
    INVALID_REQUEST,
    ENDPOINT_OR_INDICATOR_NOT_FOUND,
)


class _FakeResp:
    def __init__(self, status_code, payload=None, content_type="application/json"):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self._payload = payload
        import json as _json
        self.content = _json.dumps(payload).encode("utf-8") if payload is not None else b""

    def json(self):
        return self._payload


class WorldBankSemanticsTest(unittest.TestCase):
    def test_200_with_zero_records_is_no_data(self):
        payload = [{"page": 1, "pages": 1, "per_page": 1000, "total": 0}, []]
        with mock.patch("connectors.world_bank._HTTP.get",
                        return_value=_FakeResp(200, payload)):
            outcome = wb.acquire_world_bank("BWA", "gdp", 2000, 2024, Path(tempfile.mkdtemp()))
        self.assertEqual(outcome.status, NO_DATA_FOR_COUNTRY_INDICATOR)
        self.assertEqual(outcome.records, 0)

    def test_200_with_records_is_success(self):
        payload = [{"page": 1, "pages": 1, "per_page": 1000, "total": 1},
                   [{"date": "2020", "value": 123.0}]]
        with mock.patch("connectors.world_bank._HTTP.get",
                        return_value=_FakeResp(200, payload)):
            outcome = wb.acquire_world_bank("EGY", "gdp", 2020, 2020, Path(tempfile.mkdtemp()))
        self.assertEqual(outcome.status, "SUCCESS")
        self.assertEqual(outcome.records, 1)

    def test_message_wrapper_invalid_indicator(self):
        payload = [{"message": [{"id": "120", "key": "Invalid value",
                                 "value": "The provided parameter value is not valid"}]}]
        with mock.patch("connectors.world_bank._HTTP.get",
                        return_value=_FakeResp(200, payload)):
            outcome = wb.acquire_world_bank("EGY", "gdp", 2000, 2024, Path(tempfile.mkdtemp()))
        self.assertEqual(outcome.status, INVALID_REQUEST)

    def test_http_404_is_endpoint_not_found(self):
        with mock.patch("connectors.world_bank._HTTP.get",
                        return_value=_FakeResp(404, None)):
            verification = wb.verify_world_bank("EGY", "gdp")
        self.assertEqual(verification.status, ENDPOINT_OR_INDICATOR_NOT_FOUND)

    def test_indicator_code_lookup(self):
        self.assertEqual(wb._indicator_code("gdp"), "NY.GDP.MKTP.CD")


class NagerSemanticsTest(unittest.TestCase):
    def test_no_holiday_records_is_no_data_not_failure(self):
        with mock.patch("connectors.misc._nager_year", return_value=[]):
            verification, outcome = misc.nager_connector(
                "EGY", "public_holidays", 2000, 2024, None, Path(tempfile.mkdtemp()))
        self.assertEqual(outcome.status, NO_DATA)
        self.assertEqual(verification.status, "VERIFIED")

    def test_holiday_records_saved(self):
        sample = [{"date": "2024-01-01", "name": "New Year", "localName": "New Year", "types": ["Public"]}]
        with mock.patch("connectors.misc._nager_year", return_value=sample):
            verification, outcome = misc.nager_connector(
                "EGY", "public_holidays", 2024, 2024, None, Path(tempfile.mkdtemp()))
        self.assertEqual(outcome.status, "SUCCESS")
        self.assertEqual(outcome.records, 1)
        self.assertEqual(outcome.frequency, "annual")

    def test_iso2_mapping(self):
        self.assertEqual(misc._iso2("EGY"), "EG")
        self.assertIsNone(misc._iso2("NOPE"))


class EmberDiscoverySemanticsTest(unittest.TestCase):
    """Ember must discover what a country actually exposes and must never
    manufacture an electricity-demand series out of generation data."""

    def _generation_only_rows(self):
        return [
            {"date": "2024-01", "series": "Coal", "generation_twh": 1.0,
             "share_of_generation_pct": 5.0},
            {"date": "2024-01", "series": "Gas", "generation_twh": 2.0,
             "share_of_generation_pct": 10.0},
            {"date": "2024-01", "series": "Total generation", "generation_twh": 20.0,
             "share_of_generation_pct": 100.0},
            {"date": "2024-01", "series": "Net imports", "generation_twh": -1.0,
             "share_of_generation_pct": -5.0},
        ]

    def _demand_rows(self):
        return [
            {"date": "2024-01", "series": "Demand", "demand_twh": 15.0},
            {"date": "2024-02", "series": "Demand", "demand_twh": 14.5},
        ]

    def test_no_demand_series_is_no_data_not_fabricated(self):
        from connectors import ember

        def fake_get(url, **kwargs):
            if "electricity-demand" in url:
                return _FakeResp(200, [])
            return _FakeResp(200, self._generation_only_rows())

        with mock.patch("connectors.ember._HTTP.get", side_effect=fake_get):
            outcome = ember.acquire_ember(
                "EGY", "electricity_demand", 2024, 2024, key="k", out_dir=Path(tempfile.mkdtemp()))

        self.assertEqual(outcome.status, "NO_DATA")
        self.assertIn("Total generation", outcome.message)   # what Ember actually has
        self.assertIn("demand=False", outcome.message)       # explicit demand=absent
        self.assertTrue(outcome.provenance["has_generation"])
        self.assertFalse(outcome.provenance["has_demand"])
        self.assertNotIn("Demand", outcome.provenance["available_series"])
        self.assertEqual(outcome.path, "")  # no demand file fabricated

    def test_genuine_demand_series_acquires(self):
        from connectors import ember

        def fake_get(url, **kwargs):
            return _FakeResp(200, self._demand_rows())

        with mock.patch("connectors.ember._HTTP.get", side_effect=fake_get):
            outcome = ember.acquire_ember(
                "EGY", "electricity_demand", 2024, 2024, key="k", out_dir=Path(tempfile.mkdtemp()))

        self.assertEqual(outcome.status, "SUCCESS")
        self.assertEqual(outcome.records, 2)
        self.assertEqual(outcome.unit, "TWh")

    def test_discover_reports_generation_no_demand(self):
        from connectors import ember

        def fake_get(url, **kwargs):
            if "electricity-demand" in url:
                return _FakeResp(200, [])
            return _FakeResp(200, self._generation_only_rows())

        with mock.patch("connectors.ember._HTTP.get", side_effect=fake_get):
            discovery = ember.discover_ember_series("EGY", "k", 2024, 2024)

        self.assertFalse(discovery["has_demand"])
        self.assertTrue(discovery["has_generation"])
        self.assertIn("Total generation", discovery["available_series"])


if __name__ == "__main__":
    unittest.main()
