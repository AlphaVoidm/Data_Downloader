"""Post-download data quality validation for HGT-QF.

Every downloaded dataset MUST pass these checks before being saved as a
successful result.  The goal is to prevent:

    - Silent NULL data (HTTP 200 → all NaN dataframe → "SUCCESS")
    - Wrong country data accepted
    - Wrong date range accepted
    - Schema mismatches (missing expected columns)
    - Unit mismatches (e.g. Kelvin instead of °C)
    - Duplicate records
    - Completely empty datasets stored as success

Validation result statuses:

    VALID               all checks pass
    PARTIAL_VALID       data usable but some checks flagged warnings
    SCHEMA_ERROR        expected columns/fields missing
    COUNTRY_MISMATCH    returned data is for a different country
    DATE_RANGE_ERROR    returned dates don't overlap requested period
    EMPTY_DATA          no valid records after parsing
    UNIT_MISMATCH       unexpected units in the data
    DUPLICATE_RECORDS   excessive duplicates detected
    ALL_NULL            every value column is NaN
    MALFORMED_RESPONSE  data cannot be parsed at all

The validator NEVER silently fills missing target values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from validation.units import unit_matches, KNOWN_UNITS

# ---------------------------------------------------------------------------
# Validation statuses
# ---------------------------------------------------------------------------

VALID = "VALID"
PARTIAL_VALID = "PARTIAL_VALID"
SCHEMA_ERROR = "SCHEMA_ERROR"
COUNTRY_MISMATCH = "COUNTRY_MISMATCH"
DATE_RANGE_ERROR = "DATE_RANGE_ERROR"
EMPTY_DATA = "EMPTY_DATA"
UNIT_MISMATCH = "UNIT_MISMATCH"
DUPLICATE_RECORDS = "DUPLICATE_RECORDS"
ALL_NULL = "ALL_NULL"
MALFORMED_RESPONSE = "MALFORMED_RESPONSE"

# Severity levels: anything >= REJECT blocks saving as "SUCCESS"
SEVERITY_OK = "OK"
SEVERITY_WARN = "WARN"
SEVERITY_REJECT = "REJECT"


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ValidationFinding:
    check: str
    status: str
    severity: str          # OK, WARN, REJECT
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataValidationReport:
    """Full validation report for one downloaded dataset."""
    status: str                           # final status
    severity: str                         # OK / WARN / REJECT
    country: str
    iso3: str
    concept: str
    source: str
    findings: list[ValidationFinding] = field(default_factory=list)

    # Numeric summary
    records_received: int = 0
    records_valid: int = 0
    records_expected: int = 0
    null_count: int = 0
    null_percentage: float = 0.0
    infinite_count: int = 0
    duplicate_count: int = 0
    coverage_pct: float = 0.0

    # Period info
    date_first: str = ""
    date_last: str = ""
    periods_observed: int = 0
    periods_expected: int = 0
    periods_missing: int = 0

    # Unit / schema info
    columns_found: list[str] = field(default_factory=list)
    columns_expected: list[str] = field(default_factory=list)
    units_found: dict[str, str] = field(default_factory=dict)
    units_expected: dict[str, str] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.status in (VALID, PARTIAL_VALID)

    @property
    def should_reject(self) -> bool:
        return self.severity == SEVERITY_REJECT

    def summary_text(self) -> str:
        """Human-readable summary."""
        lines = [
            f"STATUS: {self.status}",
            f"  {self.source} / {self.country} ({self.iso3}) / {self.concept}",
            f"  HTTP/Response: received {self.records_received:,} records",
        ]
        if self.records_expected > 0:
            lines.append(f"  Expected: {self.records_expected:,}")
        lines.append(f"  Valid records: {self.records_valid:,}")
        if self.null_count > 0:
            lines.append(f"  Null records: {self.null_count:,} ({self.null_percentage:.1f}%)")
        if self.duplicate_count > 0:
            lines.append(f"  Duplicates: {self.duplicate_count:,}")
        if self.periods_expected > 0:
            lines.append(f"  Periods: {self.periods_observed}/{self.periods_expected} "
                         f"(missing: {self.periods_missing})")
        lines.append(f"  Coverage: {self.coverage_pct:.1f}%")

        warnings = [f for f in self.findings if f.severity == SEVERITY_WARN]
        rejects = [f for f in self.findings if f.severity == SEVERITY_REJECT]
        for f in rejects:
            lines.append(f"  ✗ REJECT: [{f.check}] {f.message}")
        for f in warnings:
            lines.append(f"  ⚠ WARN: [{f.check}] {f.message}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "severity": self.severity,
            "country": self.country,
            "iso3": self.iso3,
            "concept": self.concept,
            "source": self.source,
            "records_received": self.records_received,
            "records_valid": self.records_valid,
            "records_expected": self.records_expected,
            "null_count": self.null_count,
            "null_percentage": self.null_percentage,
            "infinite_count": self.infinite_count,
            "duplicate_count": self.duplicate_count,
            "coverage_pct": self.coverage_pct,
            "date_first": self.date_first,
            "date_last": self.date_last,
            "periods_observed": self.periods_observed,
            "periods_expected": self.periods_expected,
            "periods_missing": self.periods_missing,
            "columns_found": self.columns_found,
            "columns_expected": self.columns_expected,
            "findings": [
                {"check": f.check, "status": f.status, "severity": f.severity,
                 "message": f.message, "details": f.details}
                for f in self.findings
            ],
        }


# ---------------------------------------------------------------------------
# Individual validation checks
# ---------------------------------------------------------------------------

def check_empty(df: pd.DataFrame) -> ValidationFinding:
    """Check 1: Is the dataframe completely empty?"""
    if df is None or df.empty:
        return ValidationFinding(
            check="EMPTY_DATA", status=EMPTY_DATA, severity=SEVERITY_REJECT,
            message="Response is empty — no records to validate",
        )
    return ValidationFinding(
        check="EMPTY_DATA", status=VALID, severity=SEVERITY_OK,
        message=f"Data has {len(df)} rows",
    )


def check_schema(
    df: pd.DataFrame,
    required_columns: list[str] | None = None,
) -> ValidationFinding:
    """Check 2: Do the expected columns exist?"""
    if not required_columns:
        return ValidationFinding(
            check="SCHEMA", status=VALID, severity=SEVERITY_OK,
            message="No schema requirements specified",
            details={"columns": list(df.columns)},
        )
    cols = set(df.columns)
    missing = [c for c in required_columns if c not in cols]
    if missing:
        return ValidationFinding(
            check="SCHEMA", status=SCHEMA_ERROR, severity=SEVERITY_REJECT,
            message=f"Missing required columns: {missing}",
            details={"expected": required_columns, "found": list(df.columns), "missing": missing},
        )
    return ValidationFinding(
        check="SCHEMA", status=VALID, severity=SEVERITY_OK,
        message="All required columns present",
        details={"columns": list(df.columns)},
    )


def check_country(
    df: pd.DataFrame,
    expected_iso3: str,
    country_column: str = "iso3",
) -> ValidationFinding:
    """Check 3: Does the data contain the expected country?

    Only checks if the dataframe has a country/iso3 column.
    """
    expected = expected_iso3.strip().upper()
    # Try to find a country column
    col = None
    for candidate in [country_column, "country", "iso3", "ISO3", "country_code", "iso_code"]:
        if candidate in df.columns:
            col = candidate
            break

    if col is None:
        return ValidationFinding(
            check="COUNTRY", status=VALID, severity=SEVERITY_OK,
            message="No country column to validate",
        )

    actual_values = df[col].dropna().astype(str).str.strip().str.upper().unique()
    if len(actual_values) == 0:
        return ValidationFinding(
            check="COUNTRY", status=COUNTRY_MISMATCH, severity=SEVERITY_REJECT,
            message="Country column exists but is entirely empty/NaN",
            details={"expected": expected, "column": col},
        )

    if expected not in actual_values:
        return ValidationFinding(
            check="COUNTRY", status=COUNTRY_MISMATCH, severity=SEVERITY_REJECT,
            message=f"Expected {expected} but found: {list(actual_values[:10])}",
            details={"expected": expected, "found": list(actual_values[:20]), "column": col},
        )

    return ValidationFinding(
        check="COUNTRY", status=VALID, severity=SEVERITY_OK,
        message=f"Country {expected} confirmed",
        details={"column": col},
    )


def check_date_range(
    df: pd.DataFrame,
    start_year: int,
    end_year: int,
    date_column: str = "date",
) -> ValidationFinding:
    """Check 4: Do the dates cover the requested period?"""
    # Find the date column
    col = None
    for candidate in [date_column, "Date", "DATE", "timestamp", "year", "period"]:
        if candidate in df.columns:
            col = candidate
            break
    if col is None and df.index.name and "date" in df.index.name.lower():
        col = df.index.name

    if col is None:
        return ValidationFinding(
            check="DATE_RANGE", status=VALID, severity=SEVERITY_OK,
            message="No date column found to validate",
        )

    try:
        if col == df.index.name:
            dates = pd.to_datetime(df.index)
        else:
            dates = pd.to_datetime(df[col], errors="coerce")
        dates = dates.dropna()

        if len(dates) == 0:
            return ValidationFinding(
                check="DATE_RANGE", status=DATE_RANGE_ERROR, severity=SEVERITY_REJECT,
                message="Date column exists but contains no valid dates",
            )

        first = dates.min().year
        last = dates.max().year

        # Must overlap with requested period
        if last < start_year or first > end_year:
            return ValidationFinding(
                check="DATE_RANGE", status=DATE_RANGE_ERROR, severity=SEVERITY_REJECT,
                message=f"Data spans {first}–{last}, requested {start_year}–{end_year} (no overlap)",
                details={"data_first": first, "data_last": last,
                         "requested_start": start_year, "requested_end": end_year},
            )

        # Check if partial coverage
        if first > start_year or last < end_year:
            return ValidationFinding(
                check="DATE_RANGE", status=VALID, severity=SEVERITY_WARN,
                message=f"Partial coverage: data {first}–{last}, requested {start_year}–{end_year}",
                details={"data_first": first, "data_last": last,
                         "requested_start": start_year, "requested_end": end_year},
            )

        return ValidationFinding(
            check="DATE_RANGE", status=VALID, severity=SEVERITY_OK,
            message=f"Date range OK: {first}–{last}",
            details={"data_first": first, "data_last": last},
        )
    except Exception as exc:
        return ValidationFinding(
            check="DATE_RANGE", status=VALID, severity=SEVERITY_WARN,
            message=f"Could not parse dates: {exc}",
        )


def check_nulls(
    df: pd.DataFrame,
    value_columns: list[str] | None = None,
    max_null_pct: float = 95.0,
) -> ValidationFinding:
    """Check 5: Are the value columns mostly NaN?

    Never silently accept a dataset that is all-null.
    """
    if value_columns is None:
        # Infer numeric columns
        value_columns = df.select_dtypes(include=[np.number]).columns.tolist()

    if not value_columns:
        return ValidationFinding(
            check="NULLS", status=VALID, severity=SEVERITY_OK,
            message="No numeric value columns to check",
        )

    total_cells = 0
    null_cells = 0
    per_col: dict[str, dict] = {}

    for col in value_columns:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        n = len(series)
        nc = int(series.isna().sum())
        total_cells += n
        null_cells += nc
        per_col[col] = {"total": n, "null": nc, "null_pct": round(nc / n * 100, 2) if n > 0 else 0}

    null_pct = (null_cells / total_cells * 100) if total_cells > 0 else 0

    if null_pct >= 100:
        return ValidationFinding(
            check="NULLS", status=ALL_NULL, severity=SEVERITY_REJECT,
            message=f"ALL values are null ({null_cells}/{total_cells} cells)",
            details={"null_pct": null_pct, "per_column": per_col},
        )

    if null_pct > max_null_pct:
        return ValidationFinding(
            check="NULLS", status=VALID, severity=SEVERITY_WARN,
            message=f"High null rate: {null_pct:.1f}% ({null_cells}/{total_cells})",
            details={"null_pct": null_pct, "per_column": per_col},
        )

    return ValidationFinding(
        check="NULLS", status=VALID, severity=SEVERITY_OK,
        message=f"Null rate: {null_pct:.1f}%",
        details={"null_pct": null_pct, "null_count": null_cells, "per_column": per_col},
    )


def check_infinite(
    df: pd.DataFrame,
    value_columns: list[str] | None = None,
) -> ValidationFinding:
    """Check 5b: Are there infinite values?"""
    if value_columns is None:
        value_columns = df.select_dtypes(include=[np.number]).columns.tolist()

    inf_count = 0
    for col in value_columns:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        inf_count += int(np.isinf(series).sum())

    if inf_count > 0:
        return ValidationFinding(
            check="INFINITE", status=VALID, severity=SEVERITY_WARN,
            message=f"Found {inf_count} infinite values",
            details={"infinite_count": inf_count},
        )
    return ValidationFinding(
        check="INFINITE", status=VALID, severity=SEVERITY_OK,
        message="No infinite values",
    )


def check_duplicates(
    df: pd.DataFrame,
    subset: list[str] | None = None,
    max_dup_pct: float = 5.0,
) -> ValidationFinding:
    """Check 6: Are there duplicate records?"""
    if subset is None:
        # Guess key columns
        candidates = ["date", "iso3", "country", "indicator", "year"]
        subset = [c for c in candidates if c in df.columns]

    if not subset:
        dup_count = int(df.duplicated().sum())
    else:
        dup_count = int(df.duplicated(subset=subset).sum())

    dup_pct = (dup_count / len(df) * 100) if len(df) > 0 else 0

    if dup_pct > max_dup_pct:
        return ValidationFinding(
            check="DUPLICATES", status=DUPLICATE_RECORDS, severity=SEVERITY_WARN,
            message=f"High duplicate rate: {dup_count} ({dup_pct:.1f}%)",
            details={"duplicate_count": dup_count, "duplicate_pct": dup_pct},
        )

    if dup_count > 0:
        return ValidationFinding(
            check="DUPLICATES", status=VALID, severity=SEVERITY_OK,
            message=f"{dup_count} duplicates ({dup_pct:.1f}%)",
            details={"duplicate_count": dup_count, "duplicate_pct": dup_pct},
        )

    return ValidationFinding(
        check="DUPLICATES", status=VALID, severity=SEVERITY_OK,
        message="No duplicates",
    )


def check_units(
    concept: str,
    reported_unit: str,
) -> ValidationFinding:
    """Check 7: Are the units consistent with what the source should provide?"""
    ok, note = unit_matches(concept, reported_unit)
    if ok:
        return ValidationFinding(
            check="UNITS", status=VALID, severity=SEVERITY_OK,
            message=note,
            details={"concept": concept, "unit": reported_unit},
        )
    return ValidationFinding(
        check="UNITS", status=UNIT_MISMATCH, severity=SEVERITY_WARN,
        message=note,
        details={"concept": concept, "unit": reported_unit},
    )


def check_coverage(
    df: pd.DataFrame,
    start_year: int,
    end_year: int,
    frequency: str = "monthly",
    date_column: str = "date",
) -> ValidationFinding:
    """Check 8: Calculate actual coverage (observed vs expected periods)."""
    # Find date column
    col = None
    for candidate in [date_column, "Date", "DATE", "timestamp", "year", "period"]:
        if candidate in df.columns:
            col = candidate
            break
    if col is None and df.index.name and "date" in df.index.name.lower():
        col = df.index.name

    if col is None:
        return ValidationFinding(
            check="COVERAGE", status=VALID, severity=SEVERITY_OK,
            message="No date column for coverage calculation",
        )

    try:
        if col == df.index.name:
            dates = pd.to_datetime(df.index)
        else:
            dates = pd.to_datetime(df[col], errors="coerce")
        dates = dates.dropna()

        if len(dates) == 0:
            return ValidationFinding(
                check="COVERAGE", status=EMPTY_DATA, severity=SEVERITY_REJECT,
                message="No valid dates for coverage analysis",
            )

        # Calculate expected periods
        if frequency == "monthly":
            expected = pd.period_range(f"{start_year}-01", f"{end_year}-12", freq="M")
            observed = dates.dt.to_period("M")
        elif frequency == "daily":
            expected_idx = pd.date_range(f"{start_year}-01-01", f"{end_year}-12-31", freq="D")
            expected = len(expected_idx)
            observed_set = set(dates.dt.date)
            observed_count = len(observed_set)
            coverage = observed_count / expected * 100 if expected > 0 else 0
            return ValidationFinding(
                check="COVERAGE", status=VALID,
                severity=SEVERITY_OK if coverage >= 80 else SEVERITY_WARN,
                message=f"Daily coverage: {observed_count}/{expected} ({coverage:.1f}%)",
                details={
                    "periods_expected": expected,
                    "periods_observed": observed_count,
                    "coverage_pct": round(coverage, 2),
                },
            )
        elif frequency == "annual" or frequency == "yearly":
            expected_periods = list(range(start_year, end_year + 1))
            observed_years = sorted(dates.dt.year.unique())
            observed_set = set(observed_years)
            missing = [y for y in expected_periods if y not in observed_set]
            coverage = len(observed_set) / len(expected_periods) * 100 if expected_periods else 100
            return ValidationFinding(
                check="COVERAGE", status=VALID,
                severity=SEVERITY_OK if coverage >= 80 else SEVERITY_WARN,
                message=f"Annual coverage: {len(observed_set)}/{len(expected_periods)} ({coverage:.1f}%)",
                details={
                    "periods_expected": len(expected_periods),
                    "periods_observed": len(observed_set),
                    "periods_missing": len(missing),
                    "missing_years": missing[:20],
                    "coverage_pct": round(coverage, 2),
                },
            )
        else:
            # Default: treat as monthly
            expected = pd.period_range(f"{start_year}-01", f"{end_year}-12", freq="M")
            observed = dates.dt.to_period("M")

        expected_set = set(expected)
        observed_set = set(observed.unique())
        missing = sorted(expected_set - observed_set)
        coverage = len(observed_set & expected_set) / len(expected_set) * 100 if expected_set else 100

        return ValidationFinding(
            check="COVERAGE", status=VALID,
            severity=SEVERITY_OK if coverage >= 80 else SEVERITY_WARN,
            message=f"Coverage: {len(observed_set & expected_set)}/{len(expected_set)} "
                    f"periods ({coverage:.1f}%)",
            details={
                "periods_expected": len(expected_set),
                "periods_observed": len(observed_set & expected_set),
                "periods_missing": len(missing),
                "missing_periods": [str(p) for p in missing[:20]],
                "coverage_pct": round(coverage, 2),
                "date_first": str(dates.min()),
                "date_last": str(dates.max()),
            },
        )
    except Exception as exc:
        return ValidationFinding(
            check="COVERAGE", status=VALID, severity=SEVERITY_WARN,
            message=f"Coverage check failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Full validation pipeline
# ---------------------------------------------------------------------------

def validate_downloaded_data(
    df: pd.DataFrame,
    country_name: str,
    iso3: str,
    concept: str,
    source: str,
    start_year: int,
    end_year: int,
    frequency: str = "monthly",
    required_columns: list[str] | None = None,
    reported_unit: str = "",
    value_columns: list[str] | None = None,
) -> DataValidationReport:
    """Run all validation checks on a downloaded dataset.

    Returns a DataValidationReport with the final status.
    """
    report = DataValidationReport(
        status=VALID,
        severity=SEVERITY_OK,
        country=country_name,
        iso3=iso3,
        concept=concept,
        source=source,
    )

    # Run all checks
    findings: list[ValidationFinding] = []

    # 1. Empty check
    findings.append(check_empty(df))
    if findings[-1].severity == SEVERITY_REJECT:
        report.findings = findings
        report.status = EMPTY_DATA
        report.severity = SEVERITY_REJECT
        return report

    report.records_received = len(df)
    report.columns_found = list(df.columns)
    report.columns_expected = required_columns or list(df.columns)

    # 2. Schema check
    findings.append(check_schema(df, required_columns))

    # 3. Country check
    findings.append(check_country(df, iso3))

    # 4. Date range check
    findings.append(check_date_range(df, start_year, end_year))

    # 5. Null check
    null_finding = check_nulls(df, value_columns)
    findings.append(null_finding)

    # Extract null stats
    if null_finding.details.get("null_count") is not None:
        report.null_count = null_finding.details["null_count"]
    if null_finding.details.get("null_pct") is not None:
        report.null_percentage = null_finding.details["null_pct"]

    # 5b. Infinite check
    inf_finding = check_infinite(df, value_columns)
    findings.append(inf_finding)
    report.infinite_count = inf_finding.details.get("infinite_count", 0)

    # 6. Duplicate check
    dup_finding = check_duplicates(df)
    findings.append(dup_finding)
    report.duplicate_count = dup_finding.details.get("duplicate_count", 0)

    # 7. Unit check
    if reported_unit:
        findings.append(check_units(concept, reported_unit))
        report.units_found = {concept: reported_unit}
        report.units_expected = {concept: reported_unit}

    # 8. Coverage check
    cov_finding = check_coverage(df, start_year, end_year, frequency)
    findings.append(cov_finding)
    if "periods_expected" in cov_finding.details:
        report.periods_expected = cov_finding.details["periods_expected"]
    if "periods_observed" in cov_finding.details:
        report.periods_observed = cov_finding.details["periods_observed"]
    if "periods_missing" in cov_finding.details:
        report.periods_missing = cov_finding.details["periods_missing"]
    if "coverage_pct" in cov_finding.details:
        report.coverage_pct = cov_finding.details["coverage_pct"]
    if "date_first" in cov_finding.details:
        report.date_first = cov_finding.details["date_first"]
    if "date_last" in cov_finding.details:
        report.date_last = cov_finding.details["date_last"]

    report.findings = findings

    # Calculate valid records
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) > 0:
        report.records_valid = int(df[numeric_cols].notna().any(axis=1).sum())
    else:
        report.records_valid = len(df)

    # Calculate expected records
    if frequency == "monthly":
        report.records_expected = (end_year - start_year + 1) * 12
    elif frequency == "annual":
        report.records_expected = end_year - start_year + 1
    elif frequency == "daily":
        report.records_expected = (pd.to_datetime(f"{end_year}-12-31") -
                                    pd.to_datetime(f"{start_year}-01-01")).days + 1

    # Determine final status
    rejects = [f for f in findings if f.severity == SEVERITY_REJECT]
    warns = [f for f in findings if f.severity == SEVERITY_WARN]

    if rejects:
        # Find the most severe rejection
        for f in rejects:
            if f.status == EMPTY_DATA:
                report.status = EMPTY_DATA
                break
            elif f.status == SCHEMA_ERROR:
                report.status = SCHEMA_ERROR
                break
            elif f.status == COUNTRY_MISMATCH:
                report.status = COUNTRY_MISMATCH
                break
            elif f.status == DATE_RANGE_ERROR:
                report.status = DATE_RANGE_ERROR
                break
            elif f.status == ALL_NULL:
                report.status = ALL_NULL
                break
        else:
            report.status = rejects[0].status
        report.severity = SEVERITY_REJECT
    elif warns:
        report.status = PARTIAL_VALID
        report.severity = SEVERITY_WARN
    else:
        report.status = VALID
        report.severity = SEVERITY_OK

    return report


# ---------------------------------------------------------------------------
# Status mapping to acquisition statuses
# ---------------------------------------------------------------------------

def validation_to_acquisition_status(report: DataValidationReport) -> tuple[str, str]:
    """Map a validation report to the acquisition status taxonomy.

    Returns (status, message).
    """
    if report.status == VALID:
        return "SUCCESS", f"Validated: {report.records_valid:,} records, {report.coverage_pct:.1f}% coverage"
    if report.status == PARTIAL_VALID:
        warns = [f.message for f in report.findings if f.severity == SEVERITY_WARN]
        return "PARTIAL_SUCCESS", f"Partial: {'; '.join(warns[:3])}"
    if report.status == SCHEMA_ERROR:
        return "INVALID_RESPONSE", f"Schema error: {next((f.message for f in report.findings if f.check == 'SCHEMA'), 'missing columns')}"
    if report.status == COUNTRY_MISMATCH:
        return "INVALID_RESPONSE", f"Country mismatch: {next((f.message for f in report.findings if f.check == 'COUNTRY'), 'wrong country')}"
    if report.status == DATE_RANGE_ERROR:
        return "INVALID_RESPONSE", f"Date range error: {next((f.message for f in report.findings if f.check == 'DATE_RANGE'), 'no overlap')}"
    if report.status == EMPTY_DATA:
        return "NO_DATA_AVAILABLE", "Empty response — no records"
    if report.status == ALL_NULL:
        return "NO_DATA_AVAILABLE", "All values are null"
    if report.status == UNIT_MISMATCH:
        return "PARTIAL_SUCCESS", f"Unit mismatch: {next((f.message for f in report.findings if f.check == 'UNITS'), '')}"
    if report.status == DUPLICATE_RECORDS:
        return "PARTIAL_SUCCESS", f"Duplicates detected: {report.duplicate_count}"
    return "INVALID_RESPONSE", f"Validation failed: {report.status}"


__all__ = [
    "DataValidationReport", "ValidationFinding",
    "VALID", "PARTIAL_VALID", "SCHEMA_ERROR", "COUNTRY_MISMATCH",
    "DATE_RANGE_ERROR", "EMPTY_DATA", "UNIT_MISMATCH", "DUPLICATE_RECORDS",
    "ALL_NULL", "MALFORMED_RESPONSE",
    "SEVERITY_OK", "SEVERITY_WARN", "SEVERITY_REJECT",
    "validate_downloaded_data", "validation_to_acquisition_status",
    "check_empty", "check_schema", "check_country", "check_date_range",
    "check_nulls", "check_infinite", "check_duplicates", "check_units",
    "check_coverage",
]
