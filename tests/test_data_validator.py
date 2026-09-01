"""Tests for the Data Validator module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data_validator import (
    validate_downloaded_data, validation_to_acquisition_status,
    check_empty, check_schema, check_country, check_date_range,
    check_nulls, check_infinite, check_duplicates, check_units, check_coverage,
    VALID, PARTIAL_VALID, SCHEMA_ERROR, COUNTRY_MISMATCH,
    DATE_RANGE_ERROR, EMPTY_DATA, ALL_NULL,
    SEVERITY_OK, SEVERITY_WARN, SEVERITY_REJECT,
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _make_monthly_df(iso3: str, start_year: int, end_year: int,
                     value_col: str = "value", with_nulls: bool = False) -> pd.DataFrame:
    """Create a monthly dataframe for testing."""
    dates = pd.period_range(f"{start_year}-01", f"{end_year}-12", freq="M").to_timestamp()
    data = {
        "date": dates,
        "iso3": iso3,
        value_col: np.random.randn(len(dates)),
    }
    df = pd.DataFrame(data)
    if with_nulls:
        # Set 10% to NaN
        n = max(1, len(df) // 10)
        df.loc[df.index[:n], value_col] = np.nan
    return df


def _make_empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "iso3", "value"])


def _make_all_null_df(iso3: str, n: int = 12) -> pd.DataFrame:
    dates = pd.period_range("2020-01", periods=n, freq="M").to_timestamp()
    return pd.DataFrame({
        "date": dates,
        "iso3": iso3,
        "value": [np.nan] * n,
    })


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

class TestCheckEmpty:
    def test_empty_dataframe(self):
        result = check_empty(_make_empty_df())
        assert result.status == EMPTY_DATA
        assert result.severity == SEVERITY_REJECT

    def test_non_empty_dataframe(self):
        df = _make_monthly_df("DEU", 2020, 2020)
        result = check_empty(df)
        assert result.status == VALID
        assert result.severity == SEVERITY_OK


class TestCheckSchema:
    def test_schema_present(self):
        df = _make_monthly_df("DEU", 2020, 2020)
        result = check_schema(df, required_columns=["date", "iso3", "value"])
        assert result.status == VALID

    def test_schema_missing_columns(self):
        df = _make_monthly_df("DEU", 2020, 2020)
        result = check_schema(df, required_columns=["date", "iso3", "nonexistent_col"])
        assert result.status == SCHEMA_ERROR
        assert result.severity == SEVERITY_REJECT

    def test_schema_no_requirements(self):
        df = _make_monthly_df("DEU", 2020, 2020)
        result = check_schema(df, required_columns=None)
        assert result.status == VALID


class TestCheckCountry:
    def test_country_match(self):
        df = _make_monthly_df("DEU", 2020, 2020)
        result = check_country(df, "DEU")
        assert result.status == VALID

    def test_country_mismatch(self):
        df = _make_monthly_df("DEU", 2020, 2020)
        result = check_country(df, "USA")
        assert result.status == COUNTRY_MISMATCH
        assert result.severity == SEVERITY_REJECT

    def test_no_country_column(self):
        df = pd.DataFrame({"date": pd.date_range("2020-01", periods=12), "value": range(12)})
        result = check_country(df, "DEU")
        assert result.status == VALID  # no country column = pass


class TestCheckDateRange:
    def test_date_range_overlap(self):
        df = _make_monthly_df("DEU", 2020, 2023)
        result = check_date_range(df, 2020, 2023)
        assert result.severity == SEVERITY_OK

    def test_date_range_no_overlap(self):
        df = _make_monthly_df("DEU", 2020, 2021)
        result = check_date_range(df, 2022, 2023)
        assert result.status == DATE_RANGE_ERROR
        assert result.severity == SEVERITY_REJECT

    def test_date_range_partial(self):
        df = _make_monthly_df("DEU", 2021, 2022)
        result = check_date_range(df, 2020, 2023)
        assert result.severity == SEVERITY_WARN  # partial overlap


class TestCheckNulls:
    def test_no_nulls(self):
        df = _make_monthly_df("DEU", 2020, 2020)
        result = check_nulls(df)
        assert result.severity == SEVERITY_OK

    def test_some_nulls(self):
        df = _make_monthly_df("DEU", 2020, 2020, with_nulls=True)
        result = check_nulls(df)
        # Should be warn (10% null) or ok depending on threshold
        assert result.severity in (SEVERITY_OK, SEVERITY_WARN)

    def test_all_nulls(self):
        df = _make_all_null_df("DEU")
        result = check_nulls(df)
        assert result.status == ALL_NULL
        assert result.severity == SEVERITY_REJECT


class TestCheckDuplicates:
    def test_no_duplicates(self):
        df = _make_monthly_df("DEU", 2020, 2020)
        result = check_duplicates(df)
        assert result.severity == SEVERITY_OK

    def test_with_duplicates(self):
        df = _make_monthly_df("DEU", 2020, 2020)
        # Add duplicate rows
        df = pd.concat([df, df.iloc[:3]])
        result = check_duplicates(df)
        # Should detect duplicates
        assert result.details.get("duplicate_count", 0) >= 3


class TestCheckUnits:
    def test_known_unit(self):
        result = check_units("temperature_2m", "°C")
        assert result.status == VALID

    def test_unknown_unit(self):
        result = check_units("temperature_2m", "furlongs")
        assert result.status != VALID  # should warn


class TestCheckCoverage:
    def test_full_coverage(self):
        df = _make_monthly_df("DEU", 2020, 2023)
        result = check_coverage(df, 2020, 2023, frequency="monthly")
        assert result.details["coverage_pct"] >= 95.0

    def test_partial_coverage(self):
        df = _make_monthly_df("DEU", 2021, 2022)
        result = check_coverage(df, 2020, 2023, frequency="monthly")
        assert result.details["coverage_pct"] < 100.0


# ---------------------------------------------------------------------------
# Full validation pipeline
# ---------------------------------------------------------------------------

class TestValidateDownloadedData:
    def test_valid_data(self):
        df = _make_monthly_df("DEU", 2020, 2023)
        report = validate_downloaded_data(
            df=df,
            country_name="Germany",
            iso3="DEU",
            concept="temperature_2m",
            source="NASA POWER",
            start_year=2020,
            end_year=2023,
            frequency="monthly",
            reported_unit="°C",
        )
        assert report.status == VALID
        assert report.is_valid
        assert not report.should_reject

    def test_empty_data(self):
        df = _make_empty_df()
        report = validate_downloaded_data(
            df=df,
            country_name="Germany",
            iso3="DEU",
            concept="temperature_2m",
            source="NASA POWER",
            start_year=2020,
            end_year=2023,
        )
        assert report.status == EMPTY_DATA
        assert report.should_reject

    def test_all_null_data(self):
        df = _make_all_null_df("DEU", n=12)
        report = validate_downloaded_data(
            df=df,
            country_name="Germany",
            iso3="DEU",
            concept="temperature_2m",
            source="NASA POWER",
            start_year=2020,
            end_year=2020,
        )
        assert report.status == ALL_NULL
        assert report.should_reject

    def test_country_mismatch(self):
        df = _make_monthly_df("DEU", 2020, 2023)
        report = validate_downloaded_data(
            df=df,
            country_name="United States",
            iso3="USA",  # mismatch!
            concept="temperature_2m",
            source="NASA POWER",
            start_year=2020,
            end_year=2023,
        )
        assert report.status == COUNTRY_MISMATCH
        assert report.should_reject

    def test_partial_success_with_warnings(self):
        df = _make_monthly_df("DEU", 2020, 2020, with_nulls=True)
        report = validate_downloaded_data(
            df=df,
            country_name="Germany",
            iso3="DEU",
            concept="temperature_2m",
            source="NASA POWER",
            start_year=2018,  # data only covers 2020
            end_year=2023,
            frequency="monthly",
        )
        # Should be partial due to coverage gap
        assert report.status in (PARTIAL_VALID, VALID)


class TestValidationToAcquisitionStatus:
    def test_valid_maps_to_success(self):
        df = _make_monthly_df("DEU", 2020, 2023)
        report = validate_downloaded_data(
            df=df, country_name="Germany", iso3="DEU",
            concept="temperature_2m", source="NASA POWER",
            start_year=2020, end_year=2023,
        )
        status, msg = validation_to_acquisition_status(report)
        assert status == "SUCCESS"

    def test_empty_maps_to_no_data(self):
        df = _make_empty_df()
        report = validate_downloaded_data(
            df=df, country_name="Germany", iso3="DEU",
            concept="temperature_2m", source="NASA POWER",
            start_year=2020, end_year=2023,
        )
        status, msg = validation_to_acquisition_status(report)
        assert status == "NO_DATA_AVAILABLE"

    def test_report_summary_text(self):
        df = _make_monthly_df("DEU", 2020, 2023)
        report = validate_downloaded_data(
            df=df, country_name="Germany", iso3="DEU",
            concept="temperature_2m", source="NASA POWER",
            start_year=2020, end_year=2023,
        )
        text = report.summary_text()
        assert "STATUS:" in text
        assert "NASA POWER" in text

    def test_report_to_dict(self):
        df = _make_monthly_df("DEU", 2020, 2023)
        report = validate_downloaded_data(
            df=df, country_name="Germany", iso3="DEU",
            concept="temperature_2m", source="NASA POWER",
            start_year=2020, end_year=2023,
        )
        d = report.to_dict()
        assert "status" in d
        assert "iso3" in d
        assert "records_received" in d
