"""Tests for the acquisition engine (Component 5) with mocked adapters."""
import tempfile
import unittest
from unittest import mock

from acquisition_engine import (
    acquire_feature,
    run_acquisition,
)
from coverage_engine import ACCESS_REQUIRES_AUTH


def _fake_adapter(country, output, start, end, credentials=None):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("iso3,value\n%s,1\n" % country, encoding="utf-8")
    return 1, "SUCCESS", "fake ok"


class AcquireFeatureTest(unittest.TestCase):
    def test_pending_source_returns_adapter_pending(self):
        # VAR_20 (AC) resolves to IEA (restricted) — but let's force a pending path
        # by acquiring a feature whose best source has no adapter and no auth.
        with mock.patch.dict("acquisition_engine.ADAPTERS", {}, clear=False):
            result = acquire_feature("VAR_20", "DEU", 2000, 2024, tempfile.mkdtemp())
        # IEA is restricted -> coverage engine flags ACCESS_REQUIRES_AUTH, no download.
        self.assertEqual(result.status, ACCESS_REQUIRES_AUTH)

    def test_skip_known_unavailable(self):
        # VAR_18 (prices) for KEN: Eurostat not covered, IEA restricted -> skip, no HTTP.
        with mock.patch.dict("acquisition_engine.ADAPTERS", {}, clear=False):
            result = acquire_feature("VAR_18", "KEN", 2000, 2024, tempfile.mkdtemp())
        self.assertEqual(result.status, ACCESS_REQUIRES_AUTH)
        self.assertIn("Skipped", result.message)

    def test_mocked_adapter_dispatch(self):
        with mock.patch.dict(
            "acquisition_engine.ADAPTERS",
            {"Ember": _fake_adapter, "NASA POWER": _fake_adapter},
            clear=False,
        ):
            result = acquire_feature("VAR_01", "EGY", 2000, 2024, tempfile.mkdtemp())
            self.assertEqual(result.status, "SUCCESS")
            self.assertEqual(result.source, "Ember")
            self.assertEqual(result.records, 1)


class RunAcquisitionTest(unittest.TestCase):
    def test_run_returns_results_for_all_features(self):
        with mock.patch.dict(
            "acquisition_engine.ADAPTERS",
            {"Ember": _fake_adapter, "NASA POWER": _fake_adapter},
            clear=False,
        ):
            results = run_acquisition(
                countries=["EGY"], start=2000, end=2024,
                output_dir=tempfile.mkdtemp(),
                feature_ids=["VAR_01", "VAR_20"],  # demand + AC (restricted)
            )
        self.assertEqual(len(results), 2)
        statuses = {r.feature_id: r.status for r in results}
        self.assertEqual(statuses["VAR_01"], "SUCCESS")
        self.assertEqual(statuses["VAR_20"], ACCESS_REQUIRES_AUTH)


if __name__ == "__main__":
    unittest.main()
