"""Tests for the availability audit (Reports A & C)."""
import json
import tempfile
import unittest
from pathlib import Path

from availability_audit import (
    build_report_a,
    build_report_c,
    render_audit_report,
    run_availability_audit,
)


class AvailabilityAuditTest(unittest.TestCase):
    def test_report_c_columns(self):
        df = build_report_c(["EGY", "DEU", "USA", "KEN", "BWA"], 2000, 2024, {"EMBER_API_KEY": "x"})
        for col in ("country", "iso3", "demand_status", "climate_status", "macro_status",
                    "energy_status", "optional_feature_coverage", "core_readiness", "reason"):
            self.assertIn(col, df.columns)

    def test_report_a_columns(self):
        df = build_report_a(["EGY"], 2000, 2024, {"EMBER_API_KEY": "x"})
        for col in ("country", "feature", "candidate_sources", "best_source", "frequency",
                    "historical_start", "status", "access_type"):
            self.assertIn(col, df.columns)

    def test_audit_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = run_availability_audit(
                countries=["EGY", "DEU", "BWA"], start_year=2000, end_year=2024,
                credentials={"EMBER_API_KEY": "x"}, output_dir=tmp,
            )
            meta = Path(tmp) / "metadata"
            self.assertTrue((meta / "report_A_source_coverage.csv").exists())
            self.assertTrue((meta / "report_C_readiness.csv").exists())
            self.assertTrue((meta / "availability_audit.json").exists())
            payload = json.loads((meta / "availability_audit.json").read_text())
            self.assertEqual(payload["countries_evaluated"], 3)
            self.assertIn("MONTHLY_SUFFICIENT", payload["demand_summary"])

    def test_render_contains_headers(self):
        audit = run_availability_audit(countries=["EGY"], start_year=2000, end_year=2024,
                                       credentials={"EMBER_API_KEY": "x"})
        text = render_audit_report(audit)
        self.assertIn("HGT-QF GLOBAL DATA AVAILABILITY AUDIT", text)
        self.assertIn("MONTHLY_SUFFICIENT", text)
        self.assertIn("CORE_READY", text)


if __name__ == "__main__":
    unittest.main()
