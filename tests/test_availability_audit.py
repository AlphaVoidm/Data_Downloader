"""Tests for the availability audit (global availability + readiness reports)."""
import json
import tempfile
import unittest
from pathlib import Path

from availability_audit import (
    build_feature_summary,
    build_report_a,
    build_report_c,
    render_audit_report,
    run_availability_audit,
)


class AvailabilityAuditTest(unittest.TestCase):
    def test_report_c_columns(self):
        df = build_report_c(["EGY", "DEU", "USA", "KEN", "BWA"], 2000, 2024, {"EMBER_API_KEY": "x"})
        for col in ("country", "iso3", "region", "target_status", "target_source",
                    "first_month", "last_month", "expected_months", "observed_months",
                    "missing_months", "longest_continuous_run", "gap_count",
                    "core_coverage", "extended_coverage", "optional_coverage",
                    "research_ready", "reason"):
            self.assertIn(col, df.columns)

    def test_report_a_columns(self):
        df = build_report_a(["EGY"], 2000, 2024, {"EMBER_API_KEY": "x"})
        for col in ("country", "feature", "tier", "candidate_sources",
                    "candidate_source_statuses", "best_source", "availability_status",
                    "frequency", "license", "authentication_required",
                    "retrieval_method", "verification_url"):
            self.assertIn(col, df.columns)
        # per-source SOURCE_* statuses are recorded for the fallback chain
        self.assertTrue(df["candidate_source_statuses"].str.contains("SOURCE_").all())

    def test_feature_summary(self):
        rows = build_feature_summary(["EGY", "DEU", "BWA"], 2000, 2024, {"EMBER_API_KEY": "x"})
        concepts = {r["feature"] for r in rows}
        self.assertIn("electricity_demand", concepts)
        self.assertIn("ac_heat_pump_penetration", concepts)

    def test_audit_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = run_availability_audit(
                countries=["EGY", "DEU", "BWA"], start_year=2000, end_year=2024,
                credentials={"EMBER_API_KEY": "x"}, output_dir=tmp,
            )
            meta = Path(tmp) / "metadata"
            self.assertTrue((meta / "report_A_source_coverage.csv").exists())
            self.assertTrue((meta / "report_C_readiness.csv").exists())
            self.assertTrue((meta / "report_feature_summary.csv").exists())
            self.assertTrue((meta / "availability_audit.json").exists())
            payload = json.loads((meta / "availability_audit.json").read_text())
            self.assertEqual(payload["countries_evaluated"], 3)
            self.assertIn("MONTHLY_SUFFICIENT", payload["target_summary"])
            self.assertIn("RESEARCH_READY", payload["research_summary"])
            self.assertIn("research_config", payload)

    def test_10_country_audit_uses_10_denominator(self):
        countries = ["EGY", "DEU", "FRA", "GBR", "USA", "JPN", "IND", "BRA", "ZAF", "AUS"]
        rows = build_feature_summary(countries, 2000, 2024, {"EMBER_API_KEY": "x"})
        for r in rows:
            self.assertEqual(r["total"], 10)
            # available + auth + unavailable always sums to the audit scope
            self.assertEqual(r["available"] + r["auth_required"] + r["unavailable"], 10)

    def test_194_country_audit_uses_194_denominator(self):
        from country_registry import get_all_countries
        countries = [rec.iso3 for rec in get_all_countries()]
        self.assertEqual(len(countries), 194)
        rows = build_feature_summary(countries, 2000, 2024, {"EMBER_API_KEY": "x"})
        for r in rows:
            self.assertEqual(r["total"], 194)
            self.assertEqual(r["available"] + r["auth_required"] + r["unavailable"], 194)

    def test_render_contains_headers(self):
        audit = run_availability_audit(countries=["EGY"], start_year=2000, end_year=2024,
                                       credentials={"EMBER_API_KEY": "x"})
        text = render_audit_report(audit)
        self.assertIn("TARGET_READY", text)
        self.assertIn("FEATURE_COVERAGE", text)
        self.assertIn("RESEARCH_READY", text)
        self.assertIn("MONTHLY_SUFFICIENT", text)


if __name__ == "__main__":
    unittest.main()
