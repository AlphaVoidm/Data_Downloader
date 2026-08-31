"""Tests for the availability audit + report rendering."""
import json
import tempfile
import unittest
from pathlib import Path

from availability_audit import render_audit_report, run_availability_audit


class AvailabilityAuditTest(unittest.TestCase):
    def test_audit_summary_structure(self):
        audit = run_availability_audit(
            countries=["EGY", "DEU", "USA", "KEN"],
            start_year=2000, end_year=2024,
        )
        self.assertEqual(audit["countries_evaluated"], 4)
        self.assertIn("VAR_01", audit["feature_summary"])
        self.assertIn("demand_monthly_capable", audit["demand_summary"])
        self.assertTrue(audit["recommended_countries"])

    def test_audit_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_availability_audit(
                countries=["EGY", "DEU"], start_year=2000, end_year=2024, output_dir=tmp
            )
            meta = Path(tmp) / "metadata"
            self.assertTrue((meta / "coverage_matrix.csv").exists())
            self.assertTrue((meta / "feature_coverage_detail.csv").exists())
            self.assertTrue((meta / "source_selection_table.csv").exists())
            self.assertTrue((meta / "feature_coverage_summary.csv").exists())
            self.assertTrue((meta / "recommended_countries.csv").exists())
            self.assertTrue((meta / "availability_audit.json").exists())
            payload = json.loads((meta / "availability_audit.json").read_text())
            self.assertEqual(payload["countries_evaluated"], 2)

    def test_report_render_contains_headers(self):
        audit = run_availability_audit(countries=["EGY"], start_year=2000, end_year=2024)
        text = render_audit_report(audit)
        self.assertIn("HGT-QF GLOBAL DATA AVAILABILITY AUDIT", text)
        self.assertIn("RECOMMENDED COUNTRIES FOR HGT-QF", text)
        self.assertIn("Monthly-capable", text)


if __name__ == "__main__":
    unittest.main()
