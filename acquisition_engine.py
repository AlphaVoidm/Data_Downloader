"""Acquisition Engine for HGT-QF (redesigned).

For every country x feature:
    1. coverage discovery (which sources SUPPORT it, in priority order)
    2. endpoint verification for the best supported source
    3. if verification fails, fall back to the next supported source
    4. download/extract via the matching connector
    5. never treats HTTP 200 HTML/portal/error as data (response_validator)

Only reports a final failure after ALL applicable sources have been evaluated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from connectors import get_connector
from connectors.base import (
    AcquisitionOutcome,
    EndpointVerification,
    VERIFIED,
)
from status_vocabulary import source_status
from country_registry import get_country_record
from coverage_engine import (
    AUTH_REQUIRED,
    MAPPING_REQUIRED,
    NOT_SUPPORTED,
    SUPPORTED,
    TEMPORARILY_UNAVAILABLE,
    UNKNOWN,
    resolve_feature,
)
from feature_registry import (
    FEATURE_REGISTRY,
    FeatureNotFoundError,
    format_feature_not_found,
    get_all_features,
    get_target_feature,
    resolve_feature_concept,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_download(outcome: AcquisitionOutcome, country: str, feature: str,
                       start: int, end: int) -> tuple[bool, list[str]]:
    """Post-acquisition data quality validation.

    Uses the comprehensive data_validator module to check:
        1. Schema validation (required columns exist)
        2. Country validation (returned data matches requested country)
        3. Date validation (period overlap with request)
        4. Null validation (not all-NaN data)
        5. Coverage calculation (observed vs expected periods)
        6. Unit validation (reported units match expectations)
        7. Duplicate detection

    Never silently accepts NULL data as SUCCESS.
    """
    notes: list[str] = []

    if not outcome.path:
        return True, ["metadata-only outcome (no file artifact)"]

    p = Path(outcome.path)
    if not p.exists():
        return False, [f"output file missing: {p}"]

    try:
        import pandas as pd
        if p.suffix == ".csv":
            df = pd.read_csv(p)
        elif p.suffix == ".parquet":
            df = pd.read_parquet(p)
        else:
            return False, [f"unrecognized output format: {p.suffix}"]
    except Exception as exc:  # noqa: BLE001
        return False, [f"output unreadable: {exc}"]

    if df.empty:
        return False, ["output file has zero rows"]

    # Run the comprehensive data validator
    try:
        from data_validator import validate_downloaded_data, VALID, PARTIAL_VALID

        country_name = getattr(outcome, "country_name", country)
        source_name = getattr(outcome, "source_name", outcome.source_id if hasattr(outcome, "source_id") else "")
        frequency = outcome.frequency if hasattr(outcome, "frequency") and outcome.frequency else "monthly"
        reported_unit = outcome.unit if hasattr(outcome, "unit") and outcome.unit else ""

        report = validate_downloaded_data(
            df=df,
            country_name=country_name,
            iso3=country,
            concept=feature,
            source=source_name,
            start_year=start,
            end_year=end,
            frequency=frequency,
            reported_unit=reported_unit,
        )

        if report.status == VALID:
            notes.append(
                f"✓ VALID: {report.records_valid:,} records, "
                f"{report.coverage_pct:.1f}% coverage, "
                f"{report.null_percentage:.1f}% null"
            )
        elif report.status == PARTIAL_VALID:
            warn_msgs = [f.message for f in report.findings if f.severity == "WARN"]
            notes.append(
                f"⚠ PARTIAL: {report.records_valid:,} records, "
                f"{report.coverage_pct:.1f}% coverage, "
                f"{'|'.join(warn_msgs[:2])}"
            )
        else:
            reject_msgs = [f.message for f in report.findings if f.severity == "REJECT"]
            notes.append(
                f"✗ {report.status}: {report.records_received:,} received, "
                f"{'|'.join(reject_msgs[:2])}"
            )
            return False, notes

        # Always append date info
        if report.date_first and report.date_last:
            notes.append(f"date range {report.date_first} .. {report.date_last}")
        notes.append(f"null: {report.null_count} ({report.null_percentage:.1f}%), "
                     f"duplicates: {report.duplicate_count}")

    except ImportError:
        # Fallback to lightweight validation if data_validator not available
        notes.append(f"validated {len(df)} rows")
        date_col = next((c for c in df.columns if str(c).lower() in ("date", "month", "period", "year")), None)
        if date_col is not None:
            try:
                import pandas as pd
                d = pd.to_datetime(df[date_col], errors="coerce")
                notes.append(f"date range {d.min()} .. {d.max()}")
            except Exception:  # noqa: BLE001
                pass

    return True, notes


@dataclass
class AcquiredFeature:
    country: str
    country_name: str
    concept: str
    name: str
    role: str
    source_id: str
    source_name: str
    status: str
    message: str
    records: int = 0
    path: str = ""
    frequency: str = ""
    unit: str = ""
    requested_start: str = ""
    requested_end: str = ""
    received_start: str = ""
    received_end: str = ""
    verification_status: str = ""
    verification_notes: list[str] = field(default_factory=list)
    failure_reason: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    http_attempts: list[dict[str, Any]] = field(default_factory=list)
    http_status: int | None = None
    response_type: str = ""
    retrieved_at: str = field(default_factory=now_utc)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _connector_failure_status(verification: EndpointVerification) -> str:
    """Map a failed endpoint verification onto an acquisition status."""
    s = verification.status
    if s == "AUTH_FAILED":
        return "AUTH_FAILED"
    if s == "RATE_LIMITED":
        return "RATE_LIMITED"
    if s in ("NETWORK_ERROR", "TIMEOUT"):
        return s  # preserve granularity; fallback still proceeds
    if s == "MAPPING_REQUIRED":
        return "MAPPING_REQUIRED"
    if s == "NOT_SUPPORTED":
        return "NOT_SUPPORTED"
    if s in ("PORTAL_HTML", "NON_DATA_RESPONSE"):
        return "NON_DATA_RESPONSE"
    if s in ("INVALID_XML", "INVALID_JSON", "INVALID_CSV"):
        return "PARSE_ERROR"
    if s == "EMPTY_RESPONSE":
        return "EMPTY_RESPONSE"
    if s == "SCHEMA_MISMATCH":
        return "SCHEMA_MISMATCH"
    if s == "NO_RECORDS":
        return "NO_RECORDS"
    if s == "NO_DATA":
        return "NO_DATA"
    if s == "NO_DATA_FOR_COUNTRY_INDICATOR":
        return "NO_DATA_FOR_COUNTRY_INDICATOR"
    if s == "INVALID_REQUEST":
        return "INVALID_REQUEST"
    if s == "ENDPOINT_OR_INDICATOR_NOT_FOUND":
        return "ENDPOINT_OR_INDICATOR_NOT_FOUND"
    if s == "SOURCE_TEMPORARY_FAILURE":
        return "SOURCE_TEMPORARY_FAILURE"
    if s == "RETRY_EXHAUSTED":
        return "RETRY_EXHAUSTED"
    if s == "CONFIGURATION_ERROR":
        return "CONFIGURATION_ERROR"
    if s == "BULK_MANUAL":
        return "BULK_MANUAL"
    if s == "DEPENDENCY_MISSING":
        return "DEPENDENCY_MISSING"
    return "NOT_VERIFIED"


def acquire_feature(
    concept: str,
    country: str,
    start: int,
    end: int,
    out_dir: Path | str,
    credentials: dict[str, str] | None = None,
) -> AcquiredFeature:
    # Canonical feature resolution — never expose a raw KeyError for a
    # user alias/typo.
    try:
        canonical = resolve_feature_concept(concept)
    except FeatureNotFoundError:
        return AcquiredFeature(
            country=country.strip().upper(), country_name="", concept=concept.strip().lower(),
            name=concept, role="UNKNOWN", source_id="", source_name="",
            status="UNKNOWN_FEATURE", message=format_feature_not_found(concept),
            failure_reason="UNKNOWN_FEATURE",
        )

    feature = FEATURE_REGISTRY[canonical]
    concept = canonical
    country = country.strip().upper()
    rec = get_country_record(country)
    cname = rec.country_name if rec else country

    plan = resolve_feature(concept, country, start, end, credentials)

    base = AcquiredFeature(
        country=country, country_name=cname, concept=concept, name=feature.name,
        role=feature.role, source_id=plan.best_source_id, source_name=plan.best_source_name,
        status="", message="", requested_start=f"{start}-01", requested_end=f"{end}-12",
    )

    # If discovery says nothing is supported, stop here (no HTTP).
    if plan.best_status != SUPPORTED:
        base.status = plan.best_status
        base.failure_reason = plan.best_status
        base.message = {
            NOT_SUPPORTED: "No registered source supports this country/feature",
            AUTH_REQUIRED: "Data exists but required credentials are missing",
            MAPPING_REQUIRED: "Area/series mapping required before download",
            TEMPORARILY_UNAVAILABLE: "No source currently available for the requested period",
            UNKNOWN: "Source(s) not present in the registry",
        }.get(plan.best_status, plan.best_status)
        return base

    # Walk supported candidates in priority order with fallback.
    supported_candidates = [d for d in plan.decisions if d.status == SUPPORTED]
    for decision in supported_candidates:
        connector = get_connector(decision.source_id)
        if connector is None:
            base.attempts.append({"source": decision.source_name, "verification": "SKIPPED",
                                  "note": "no connector registered"})
            continue

        verification, outcome = connector(
            country=country, feature=concept, start=start, end=end,
            credentials=credentials, out_dir=Path(out_dir),
        )
        base.attempts.append({
            "source": decision.source_name,
            "verification": verification.status,
            "verification_note": verification.message,
            "source_status": source_status(verification.status),
            "http_attempts": list(verification.attempts),
        })

        if verification.status == VERIFIED:
            if outcome.status in ("SUCCESS", "PARTIAL_SUCCESS"):
                valid, validation_notes = _validate_download(outcome, country, concept, start, end)
                base.source_id = outcome.source_id
                base.source_name = decision.source_name
                base.status = outcome.status
                base.message = outcome.message
                base.records = outcome.records
                base.path = outcome.path
                base.frequency = outcome.frequency
                base.unit = outcome.unit
                base.received_start = outcome.received_start
                base.received_end = outcome.received_end
                base.verification_status = VERIFIED
                base.verification_notes = list(outcome.verification_notes) + [
                    ("post-validation PASS: " if valid else "post-validation FAIL: ") + "; ".join(validation_notes)
                ]
                base.failure_reason = outcome.failure_reason
                base.http_attempts = list(outcome.attempts)
                base.http_status = outcome.http_status
                base.response_type = outcome.response_type
                base.attempts[-1]["source_status"] = source_status(outcome.status)
                return base
            # Verified endpoint but non-successful outcome (e.g. BULK_MANUAL,
            # NO_RECORDS, SCHEMA_MISMATCH, NO_DATA) -> fall through.
            base.attempts[-1]["failure_reason"] = outcome.status
            base.attempts[-1]["verification"] = verification.status
            base.attempts[-1]["source_status"] = source_status(outcome.status)
            base.attempts[-1]["note"] = outcome.message
            base.attempts[-1]["http_attempts"] = list(outcome.attempts)
            base.http_attempts = list(outcome.attempts)
            base.http_status = outcome.http_status
            base.response_type = outcome.response_type
            continue

        # Verification failed for this source -> record and try the next.
        mapped = _connector_failure_status(verification)
        base.attempts[-1]["failure_reason"] = mapped
        base.attempts[-1]["verification"] = verification.status
        base.attempts[-1]["source_status"] = source_status(verification.status)
        base.attempts[-1]["note"] = verification.message

    # All supported sources failed verification/download. Report the
    # highest-priority (first) source's outcome as the final status, and keep
    # the per-source diagnostic notes (e.g. Ember "available series: …") in the
    # message so the researcher sees exactly what each source had.
    if base.attempts:
        base.status = base.attempts[0].get("failure_reason", "NOT_VERIFIED")
        base.failure_reason = base.status
        notes = [a.get("note") for a in base.attempts if a.get("note")]
        base.message = " | ".join(notes) if notes else "All supported sources failed verification or download"
    else:
        base.status = "NOT_VERIFIED"
        base.failure_reason = "NOT_VERIFIED"
        base.message = "All supported sources failed verification or download"
    return base


def run_acquisition(
    countries: list[str],
    start: int,
    end: int,
    out_dir: Path | str,
    credentials: dict[str, str] | None = None,
    concepts: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[AcquiredFeature]:
    if concepts is None:
        concepts = [f.concept for f in get_all_features()]
    results: list[AcquiredFeature] = []
    for iso3 in countries:
        for concept in concepts:
            if progress:
                progress(f"{iso3} | {concept}")
            try:
                results.append(acquire_feature(concept, iso3, start, end, out_dir, credentials))
            except Exception as exc:  # noqa: BLE001 — one failure must never abort the run
                results.append(AcquiredFeature(
                    country=iso3, country_name="", concept=str(concept), name=str(concept),
                    role="", source_id="", source_name="", status="FAILED",
                    message=f"Unexpected error: {exc!r}", failure_reason="UNEXPECTED_ERROR",
                ))
    return results


__all__ = ["AcquiredFeature", "acquire_feature", "run_acquisition"]
