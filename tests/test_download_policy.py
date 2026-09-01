"""Tests for the Download Policy module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from download_policy import (
    DownloadPolicy, validate_selection_policy, apply_download_policy,
    render_policy_report, SelectionPolicyResult, DownloadPolicyDecision,
    DEFAULT_POLICY,
)
from data_validator import (
    validate_downloaded_data, DataValidationReport,
    VALID, PARTIAL_VALID, EMPTY_DATA, ALL_NULL, SCHEMA_ERROR,
    COUNTRY_MISMATCH,
)


# ---------------------------------------------------------------------------
# Helper to create test data
# ---------------------------------------------------------------------------

def _make_valid_report() -> DataValidationReport:
    """Create a validation report that would result from valid data."""
    dates = pd.period_range("2020-01", "2023-12", freq="M").to_timestamp()
    df = pd.DataFrame({
        "date": dates,
        "iso3": "DEU",
        "value": np.random.randn(len(dates)),
    })
    return validate_downloaded_data(
        df=df, country_name="Germany", iso3="DEU",
        concept="temperature_2m", source="NASA POWER",
        start_year=2020, end_year=2023,
    )


def _make_empty_report() -> DataValidationReport:
    """Create a validation report for empty data."""
    df = pd.DataFrame(columns=["date", "iso3", "value"])
    return validate_downloaded_data(
        df=df, country_name="Germany", iso3="DEU",
        concept="temperature_2m", source="NASA POWER",
        start_year=2020, end_year=2023,
    )


# ---------------------------------------------------------------------------
# DownloadPolicy class
# ---------------------------------------------------------------------------

class TestDownloadPolicy:
    def test_default_policy(self):
        policy = DownloadPolicy.default()
        assert policy.reject_invalid_schema is True
        assert policy.reject_wrong_country is True
        assert policy.reject_empty_response is True
        assert policy.never_silently_fill_targets is True
        assert policy.never_silently_switch_source is True

    def test_strict_policy(self):
        policy = DownloadPolicy.strict()
        assert policy.reject_unexpected_units is True
        assert policy.reject_duplicate_records is True
        assert policy.max_null_pct < 95.0

    def test_permissive_policy(self):
        policy = DownloadPolicy.permissive()
        assert policy.reject_wrong_date_range is False
        assert policy.reject_duplicate_records is False
        assert policy.max_null_pct > 95.0

    def test_from_dict(self):
        d = {
            "reject_invalid_schema_selection": False,
            "max_null_percentage": 80.0,
        }
        policy = DownloadPolicy.from_dict(d)
        assert policy.reject_invalid_schema is False
        assert policy.max_null_pct == 80.0


# ---------------------------------------------------------------------------
# Selection Policy (pre-download)
# ---------------------------------------------------------------------------

class TestValidateSelectionPolicy:
    def test_valid_selection_passes(self):
        summary = {"countries": 2, "features": 3, "sources": 2, "requests": 6, "auth_issues": 0}
        result = validate_selection_policy(summary)
        assert result.proceed is True
        assert len(result.errors) == 0

    def test_no_countries_fails(self):
        summary = {"countries": 0, "features": 3, "sources": 2, "requests": 0, "auth_issues": 0}
        result = validate_selection_policy(summary)
        assert result.proceed is False
        assert any("No countries" in e for e in result.errors)

    def test_no_features_fails(self):
        summary = {"countries": 2, "features": 0, "sources": 2, "requests": 0, "auth_issues": 0}
        result = validate_selection_policy(summary)
        assert result.proceed is False

    def test_no_requests_fails(self):
        summary = {"countries": 2, "features": 3, "sources": 2, "requests": 0, "auth_issues": 0}
        result = validate_selection_policy(summary)
        assert result.proceed is False

    def test_auth_issues_warning(self):
        summary = {"countries": 2, "features": 3, "sources": 2, "requests": 6, "auth_issues": 10}
        result = validate_selection_policy(summary)
        assert result.proceed is True  # warns but doesn't block
        assert len(result.warnings) > 0

    def test_large_download_warning(self):
        summary = {"countries": 20, "features": 15, "sources": 5, "requests": 300, "auth_issues": 0}
        result = validate_selection_policy(summary)
        assert result.proceed is True
        assert any("Large download" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Download Policy (post-download)
# ---------------------------------------------------------------------------

class TestApplyDownloadPolicy:
    def test_valid_data_saved(self):
        report = _make_valid_report()
        policy = DownloadPolicy.default()
        decision = apply_download_policy(report, policy)
        assert decision.decision in ("SAVE", "SAVE_WITH_WARNING")
        assert decision.status in ("SUCCESS", "PARTIAL_SUCCESS")

    def test_empty_data_rejected(self):
        report = _make_empty_report()
        policy = DownloadPolicy.default()
        decision = apply_download_policy(report, policy)
        assert decision.decision == "REJECT"
        assert decision.status == "NO_DATA_AVAILABLE"

    def test_target_with_high_nulls_rejected(self):
        """Target variable with 100% nulls should always be rejected."""
        df = pd.DataFrame({
            "date": pd.period_range("2020-01", periods=12, freq="M").to_timestamp(),
            "iso3": "DEU",
            "value": [np.nan] * 12,
        })
        report = validate_downloaded_data(
            df=df, country_name="Germany", iso3="DEU",
            concept="electricity_demand", source="Ember",
            start_year=2020, end_year=2020,
        )
        policy = DownloadPolicy.default()
        decision = apply_download_policy(report, policy, is_target=True)
        assert decision.decision == "REJECT"

    def test_decision_to_dict(self):
        report = _make_valid_report()
        policy = DownloadPolicy.default()
        decision = apply_download_policy(report, policy)
        d = decision.to_dict()
        assert "decision" in d
        assert "status" in d
        assert "message" in d


# ---------------------------------------------------------------------------
# Policy Report Rendering
# ---------------------------------------------------------------------------

class TestRenderPolicyReport:
    def test_render_empty(self):
        text = render_policy_report([])
        assert "DOWNLOAD POLICY REPORT" in text

    def test_render_with_decisions(self):
        report = _make_valid_report()
        policy = DownloadPolicy.default()
        decisions = [
            apply_download_policy(report, policy),
            _make_empty_report(),
        ]
        decisions = [apply_download_policy(d, policy) for d in decisions if isinstance(d, DataValidationReport)]
        # Just test the empty case
        text = render_policy_report([apply_download_policy(_make_empty_report(), policy)])
        assert "REJECT" in text or "SAVE" in text


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_pipeline_valid(self):
        """Test the full flow: validate data → apply policy → get decision."""
        # Create good data
        dates = pd.period_range("2020-01", "2023-12", freq="M").to_timestamp()
        df = pd.DataFrame({
            "date": dates,
            "iso3": "DEU",
            "temperature_2m": np.random.randn(len(dates)) * 10 + 10,
        })

        # Validate
        report = validate_downloaded_data(
            df=df, country_name="Germany", iso3="DEU",
            concept="temperature_2m", source="NASA POWER",
            start_year=2020, end_year=2023,
            frequency="monthly", reported_unit="°C",
        )

        # Apply policy
        policy = DownloadPolicy.default()
        decision = apply_download_policy(report, policy)

        # Should be saved
        assert decision.decision in ("SAVE", "SAVE_WITH_WARNING")
        assert report.is_valid

    def test_full_pipeline_reject_wrong_country(self):
        """Data for DEU but requesting USA should be rejected."""
        dates = pd.period_range("2020-01", "2023-12", freq="M").to_timestamp()
        df = pd.DataFrame({
            "date": dates,
            "iso3": "DEU",
            "value": np.random.randn(len(dates)),
        })

        report = validate_downloaded_data(
            df=df, country_name="United States", iso3="USA",
            concept="temperature_2m", source="NASA POWER",
            start_year=2020, end_year=2023,
        )

        policy = DownloadPolicy.default()
        decision = apply_download_policy(report, policy)
        assert decision.decision == "REJECT"
        assert decision.status == "INVALID_RESPONSE"
