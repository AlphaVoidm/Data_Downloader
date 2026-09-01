"""Download Policy — configurable rules for when to accept or reject data.

The policy layer sits between the data validator and the acquisition pipeline.
Before downloading, it can validate the selection plan.
After downloading, it decides whether to save a dataset based on validation results.

The policy is configurable through a simple dict or YAML/JSON config.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from data_validator import (
    DataValidationReport,
    VALID, PARTIAL_VALID, SCHEMA_ERROR, COUNTRY_MISMATCH,
    DATE_RANGE_ERROR, EMPTY_DATA, UNIT_MISMATCH, DUPLICATE_RECORDS,
    ALL_NULL, MALFORMED_RESPONSE,
    SEVERITY_OK, SEVERITY_WARN, SEVERITY_REJECT,
)


# ---------------------------------------------------------------------------
# Default policy settings
# ---------------------------------------------------------------------------

DEFAULT_POLICY = {
    # Pre-download selection checks
    "reject_invalid_schema_selection": True,
    "reject_wrong_country_selection": True,
    "reject_wrong_date_range_selection": True,
    "reject_unexpected_units_selection": True,
    "reject_completely_empty_response": True,
    "reject_malformed_response": True,
    "reject_duplicate_records": False,  # warn but don't reject
    "warn_on_missing_periods": True,
    "warn_on_partial_coverage": True,
    "never_silently_fill_missing_target_values": True,
    "never_silently_switch_source": True,

    # Thresholds
    "max_null_percentage": 95.0,        # reject if more than this % is null
    "max_duplicate_percentage": 50.0,    # reject if more than this % is duplicate
    "min_coverage_percentage": 5.0,      # reject if less than this % coverage
    "max_auth_issues_before_warning": 3, # warn if more auth issues than this
}


@dataclass
class DownloadPolicy:
    """Configurable download policy."""
    # Rejection rules
    reject_invalid_schema: bool = True
    reject_wrong_country: bool = True
    reject_wrong_date_range: bool = True
    reject_unexpected_units: bool = False   # units are a warning, not rejection
    reject_empty_response: bool = True
    reject_malformed: bool = True
    reject_duplicate_records: bool = False

    # Warning rules
    warn_missing_periods: bool = True
    warn_partial_coverage: bool = True

    # Strict rules
    never_silently_fill_targets: bool = True
    never_silently_switch_source: bool = True

    # Thresholds
    max_null_pct: float = 95.0
    max_duplicate_pct: float = 50.0
    min_coverage_pct: float = 5.0
    max_auth_warnings: int = 3

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DownloadPolicy":
        return cls(
            reject_invalid_schema=d.get("reject_invalid_schema_selection", True),
            reject_wrong_country=d.get("reject_wrong_country_selection", True),
            reject_wrong_date_range=d.get("reject_wrong_date_range_selection", True),
            reject_unexpected_units=d.get("reject_unexpected_units_selection", False),
            reject_empty_response=d.get("reject_completely_empty_response", True),
            reject_malformed=d.get("reject_malformed_response", True),
            reject_duplicate_records=d.get("reject_duplicate_records", False),
            warn_missing_periods=d.get("warn_on_missing_periods", True),
            warn_partial_coverage=d.get("warn_on_partial_coverage", True),
            never_silently_fill_targets=d.get("never_silently_fill_missing_target_values", True),
            never_silently_switch_source=d.get("never_silently_switch_source", True),
            max_null_pct=d.get("max_null_percentage", 95.0),
            max_duplicate_pct=d.get("max_duplicate_percentage", 50.0),
            min_coverage_pct=d.get("min_coverage_percentage", 5.0),
            max_auth_warnings=d.get("max_auth_issues_before_warning", 3),
        )

    @classmethod
    def default(cls) -> "DownloadPolicy":
        return cls.from_dict(DEFAULT_POLICY)

    @classmethod
    def strict(cls) -> "DownloadPolicy":
        """Maximum strictness — reject on any validation issue."""
        return cls(
            reject_invalid_schema=True,
            reject_wrong_country=True,
            reject_wrong_date_range=True,
            reject_unexpected_units=True,
            reject_empty_response=True,
            reject_malformed=True,
            reject_duplicate_records=True,
            warn_missing_periods=True,
            warn_partial_coverage=True,
            never_silently_fill_targets=True,
            never_silently_switch_source=True,
            max_null_pct=50.0,
            max_duplicate_pct=10.0,
            min_coverage_pct=20.0,
        )

    @classmethod
    def permissive(cls) -> "DownloadPolicy":
        """Minimum strictness — save whatever we get."""
        return cls(
            reject_invalid_schema=True,
            reject_wrong_country=True,
            reject_wrong_date_range=False,
            reject_unexpected_units=False,
            reject_empty_response=True,
            reject_malformed=True,
            reject_duplicate_records=False,
            warn_missing_periods=True,
            warn_partial_coverage=True,
            never_silently_fill_targets=True,
            never_silently_switch_source=False,
            max_null_pct=99.0,
            max_duplicate_pct=90.0,
            min_coverage_pct=1.0,
        )


# ---------------------------------------------------------------------------
# Pre-download selection validation
# ---------------------------------------------------------------------------

@dataclass
class SelectionPolicyResult:
    """Result of pre-download policy checks."""
    proceed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proceed": self.proceed,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_selection_policy(
    plan_summary: dict[str, Any],
    policy: DownloadPolicy | None = None,
) -> SelectionPolicyResult:
    """Validate the download plan against the policy before starting.

    Args:
        plan_summary: dict with keys from validate_selection()['summary']
        policy: download policy (default if None)
    """
    if policy is None:
        policy = DownloadPolicy.default()

    errors: list[str] = []
    warnings: list[str] = []

    countries = plan_summary.get("countries", 0)
    features = plan_summary.get("features", 0)
    requests = plan_summary.get("requests", 0)
    auth_issues = plan_summary.get("auth_issues", 0)

    if countries == 0:
        errors.append("No countries selected")
    if features == 0:
        errors.append("No features selected")
    if requests == 0:
        errors.append("No valid source/country/feature combinations (0 requests)")

    if auth_issues > policy.max_auth_warnings:
        warnings.append(
            f"{auth_issues} source(s) require credentials — "
            "data will not be downloadable until credentials are provided"
        )

    if requests > 100:
        warnings.append(f"Large download: {requests} requests — this may take a while")

    return SelectionPolicyResult(
        proceed=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Post-download validation policy
# ---------------------------------------------------------------------------

@dataclass
class DownloadPolicyDecision:
    """What the policy decides about a downloaded dataset."""
    decision: str           # "SAVE", "SAVE_WITH_WARNING", "REJECT"
    status: str             # acquisition status to use
    message: str
    report: DataValidationReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "status": self.status,
            "message": self.message,
            "report_summary": self.report.to_dict() if self.report else None,
        }


def apply_download_policy(
    report: DataValidationReport,
    policy: DownloadPolicy | None = None,
    is_target: bool = False,
) -> DownloadPolicyDecision:
    """Apply the download policy to a validation report.

    Args:
        report: the data validation report
        policy: download policy (default if None)
        is_target: whether this is the target variable (stricter rules)

    Returns:
        DownloadPolicyDecision with SAVE / SAVE_WITH_WARNING / REJECT
    """
    if policy is None:
        policy = DownloadPolicy.default()

    status = report.status

    # Hard rejections — always block saving
    if status == EMPTY_DATA:
        return DownloadPolicyDecision(
            decision="REJECT", status="NO_DATA_AVAILABLE",
            message="Empty response — no data to save",
            report=report,
        )

    if status == MALFORMED_RESPONSE:
        return DownloadPolicyDecision(
            decision="REJECT", status="INVALID_RESPONSE",
            message="Malformed response — cannot parse data",
            report=report,
        )

    if policy.reject_invalid_schema and status == SCHEMA_ERROR:
        return DownloadPolicyDecision(
            decision="REJECT", status="INVALID_RESPONSE",
            message=f"Schema error: {next((f.message for f in report.findings if f.check == 'SCHEMA'), 'missing columns')}",
            report=report,
        )

    if policy.reject_wrong_country and status == COUNTRY_MISMATCH:
        return DownloadPolicyDecision(
            decision="REJECT", status="INVALID_RESPONSE",
            message=f"Country mismatch: {next((f.message for f in report.findings if f.check == 'COUNTRY'), 'wrong country')}",
            report=report,
        )

    if policy.reject_wrong_date_range and status == DATE_RANGE_ERROR:
        return DownloadPolicyDecision(
            decision="REJECT", status="INVALID_RESPONSE",
            message=f"Date range error: {next((f.message for f in report.findings if f.check == 'DATE_RANGE'), 'no date overlap')}",
            report=report,
        )

    if status == ALL_NULL:
        return DownloadPolicyDecision(
            decision="REJECT", status="NO_DATA_AVAILABLE",
            message="All values are null — no useful data",
            report=report,
        )

    # Null percentage threshold
    if report.null_percentage >= policy.max_null_pct:
        if is_target and policy.never_silently_fill_targets:
            return DownloadPolicyDecision(
                decision="REJECT", status="NO_DATA_AVAILABLE",
                message=f"Target variable has {report.null_percentage:.1f}% null values "
                        f"(threshold: {policy.max_null_pct}%) — refusing to save empty target",
                report=report,
            )
        return DownloadPolicyDecision(
            decision="REJECT", status="NO_DATA_AVAILABLE",
            message=f"Dataset has {report.null_percentage:.1f}% null values "
                    f"(threshold: {policy.max_null_pct}%)",
            report=report,
        )

    # Duplicate threshold
    if policy.reject_duplicate_records:
        dup_pct = (report.duplicate_count / report.records_received * 100
                   if report.records_received > 0 else 0)
        if dup_pct > policy.max_duplicate_pct:
            return DownloadPolicyDecision(
                decision="REJECT", status="INVALID_RESPONSE",
                message=f"Too many duplicates: {dup_pct:.1f}% "
                        f"(threshold: {policy.max_duplicate_pct}%)",
                report=report,
            )

    # Coverage threshold
    if report.coverage_pct > 0 and report.coverage_pct < policy.min_coverage_pct:
        return DownloadPolicyDecision(
            decision="REJECT", status="NO_DATA_AVAILABLE",
            message=f"Coverage too low: {report.coverage_pct:.1f}% "
                    f"(threshold: {policy.min_coverage_pct}%)",
            report=report,
        )

    # Partial success with warnings
    if status == PARTIAL_VALID:
        warn_msgs = [f.message for f in report.findings if f.severity == SEVERITY_WARN]
        return DownloadPolicyDecision(
            decision="SAVE_WITH_WARNING", status="PARTIAL_SUCCESS",
            message=f"Partial success: {'; '.join(warn_msgs[:3])}",
            report=report,
        )

    # Fully valid
    if status == VALID:
        return DownloadPolicyDecision(
            decision="SAVE", status="SUCCESS",
            message=f"Validated: {report.records_valid:,} records, "
                    f"{report.coverage_pct:.1f}% coverage",
            report=report,
        )

    # Fallback
    return DownloadPolicyDecision(
        decision="SAVE_WITH_WARNING", status="PARTIAL_SUCCESS",
        message=f"Validation completed with status: {status}",
        report=report,
    )


# ---------------------------------------------------------------------------
# Policy report rendering
# ---------------------------------------------------------------------------

def render_policy_report(results: list[DownloadPolicyDecision]) -> str:
    """Render a summary of policy decisions for multiple downloads."""
    lines = ["", "DOWNLOAD POLICY REPORT", "=" * 80]

    save_count = sum(1 for r in results if r.decision == "SAVE")
    warn_count = sum(1 for r in results if r.decision == "SAVE_WITH_WARNING")
    reject_count = sum(1 for r in results if r.decision == "REJECT")

    lines.append(f"  SAVE:     {save_count}")
    lines.append(f"  WARNING:  {warn_count}")
    lines.append(f"  REJECT:   {reject_count}")
    lines.append(f"  Total:    {len(results)}")
    lines.append("")

    for r in results:
        icon = {"SAVE": "✓", "SAVE_WITH_WARNING": "⚠", "REJECT": "✗"}.get(r.decision, "?")
        if r.report:
            lines.append(
                f"  {icon} {r.report.source:<20} {r.report.iso3:<5} "
                f"{r.report.concept:<30} → {r.status:<20} {r.message[:60]}"
            )

    return "\n".join(lines)


__all__ = [
    "DownloadPolicy", "SelectionPolicyResult", "DownloadPolicyDecision",
    "DEFAULT_POLICY",
    "validate_selection_policy", "apply_download_policy", "render_policy_report",
]
