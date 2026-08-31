"""Tests for the acquisition engine: coverage-gating + source fallback."""
import tempfile
import unittest
from unittest import mock

from connectors.base import AcquisitionOutcome, EndpointVerification, SUCCESS, VERIFIED
from acquisition_engine import acquire_feature


def _ok_connector(country, feature, start, end, credentials, out_dir, **kw):
    return (
        EndpointVerification(source_id="x", country=country, feature=feature, status=VERIFIED, message="ok"),
        AcquisitionOutcome(source_id="x", country=country, feature=feature, status=SUCCESS,
                           message="ok", records=12, frequency="monthly"),
    )


def _bulk_connector(country, feature, start, end, credentials, out_dir, **kw):
    return (
        EndpointVerification(source_id="x", country=country, feature=feature, status="BULK_MANUAL", message="deferred"),
        AcquisitionOutcome(source_id="x", country=country, feature=feature, status="BULK_MANUAL",
                           message="deferred"),
    )


def _fail_connector(country, feature, start, end, credentials, out_dir, **kw):
    return (
        EndpointVerification(source_id="x", country=country, feature=feature, status="PORTAL_HTML",
                             message="HTML portal, not data"),
        AcquisitionOutcome(source_id="x", country=country, feature=feature, status="NON_DATA_RESPONSE",
                           message="HTML portal, not data"),
    )


class AcquireFeatureTest(unittest.TestCase):
    def test_skips_unsupported_feature_without_http(self):
        # electricity_prices for USA: eurostat NOT_SUPPORTED, iea restricted -> AUTH_REQUIRED, no HTTP.
        result = acquire_feature("electricity_prices", "USA", 2000, 2024, tempfile.mkdtemp())
        self.assertEqual(result.status, "AUTH_REQUIRED")
        self.assertEqual(result.records, 0)

    def test_mock_success(self):
        with mock.patch("acquisition_engine.get_connector", return_value=_ok_connector):
            result = acquire_feature("electricity_demand", "EGY", 2000, 2024, tempfile.mkdtemp(),
                                     credentials={"EMBER_API_KEY": "x"})
            self.assertEqual(result.status, "SUCCESS")
            self.assertEqual(result.records, 12)

    def test_fallback_from_bulk_to_next_source(self):
        # First candidate returns BULK_MANUAL, second succeeds.
        conns = {"aemo": _bulk_connector, "ember": _ok_connector}
        with mock.patch("acquisition_engine.get_connector", side_effect=lambda sid: conns.get(sid)):
            result = acquire_feature("electricity_demand", "AUS", 2000, 2024, tempfile.mkdtemp(),
                                     credentials={"EMBER_API_KEY": "x"})
            self.assertEqual(result.status, "SUCCESS")
            self.assertEqual(result.source_name, "Ember")  # fell back from AEMO

    def test_fallback_past_failed_verification(self):
        conns = {"entsoe": _fail_connector, "ember": _ok_connector}
        with mock.patch("acquisition_engine.get_connector", side_effect=lambda sid: conns.get(sid)):
            result = acquire_feature("electricity_demand", "DEU", 2000, 2024, tempfile.mkdtemp(),
                                     credentials={"ENTSOE_API_TOKEN": "x", "EMBER_API_KEY": "x"})
            self.assertEqual(result.status, "SUCCESS")
            self.assertEqual(result.source_name, "Ember")

    def test_all_failed_reports_highest_priority_failure(self):
        conns = {"entsoe": _fail_connector, "eia": _fail_connector}
        with mock.patch("acquisition_engine.get_connector", side_effect=lambda sid: conns.get(sid)):
            result = acquire_feature("electricity_demand", "DEU", 2000, 2024, tempfile.mkdtemp(),
                                     credentials={"ENTSOE_API_TOKEN": "x"})
            self.assertEqual(result.status, "NON_DATA_RESPONSE")


if __name__ == "__main__":
    unittest.main()
