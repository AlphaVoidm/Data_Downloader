"""Tests for the acquisition engine: coverage-gating + source fallback."""
import tempfile
import unittest
from unittest import mock

import acquisition_engine
from connectors.base import AcquisitionOutcome, EndpointVerification, SUCCESS, VERIFIED
from acquisition_engine import AcquiredFeature, acquire_feature, run_acquisition


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

    def test_run_does_not_abort_on_item_failure(self):
        # One country x feature blowing up must never terminate the run; the
        # other items still process and the failure is captured with a reason.
        real = acquisition_engine.acquire_feature

        def flaky(concept, country, start, end, out_dir, credentials=None):
            if concept == "temperature_2m" and country == "EGY":
                raise RuntimeError("kaboom")
            return real(concept, country, start, end, out_dir, credentials)

        with mock.patch("acquisition_engine.acquire_feature", side_effect=flaky):
            results = run_acquisition(
                ["EGY", "DEU"], 2000, 2024, tempfile.mkdtemp(),
                concepts=["electricity_demand", "temperature_2m"],
            )

        self.assertEqual(len(results), 4)
        failed = [r for r in results if r.status == "FAILED"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].concept, "temperature_2m")
        self.assertEqual(failed[0].failure_reason, "UNEXPECTED_ERROR")
        # The other three items were still processed and recorded.
        self.assertEqual(len([r for r in results if r.concept == "electricity_demand"]), 2)
        self.assertEqual(len([r for r in results if r.concept == "temperature_2m"]), 2)

    def test_report_b_records_required_metadata(self):
        from acquisition_report import build_report_b

        r = AcquiredFeature(
            country="EGY", country_name="Egypt", concept="electricity_demand",
            name="Electricity demand", role="target", source_id="ember",
            source_name="Ember", status="AUTH_REQUIRED", message="missing key",
            requested_start="2000-01", requested_end="2024-12",
            failure_reason="AUTH_REQUIRED",
        )
        df = build_report_b([r])
        for col in ("country", "iso3", "feature", "source", "requested_period",
                    "received_period", "frequency", "units", "records", "status",
                    "source_status", "failure_reason", "attempts",
                    "retrieval_timestamp_utc"):
            self.assertIn(col, df.columns)
        row = df.iloc[0]
        self.assertEqual(row["iso3"], "EGY")
        self.assertEqual(row["feature"], "electricity_demand")
        self.assertEqual(row["status"], "AUTH_REQUIRED")
        self.assertEqual(row["failure_reason"], "AUTH_REQUIRED")
        self.assertEqual(row["requested_period"], "2000-01 / 2024-12")


if __name__ == "__main__":
    unittest.main()
