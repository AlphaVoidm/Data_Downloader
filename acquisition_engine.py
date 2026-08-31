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
    get_all_features,
    get_target_feature,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        return "NOT_VERIFIED"  # transient; will fall back / retry
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
    feature = FEATURE_REGISTRY[concept.strip().lower()]
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
        })

        if verification.status == VERIFIED:
            if outcome.status in ("SUCCESS", "PARTIAL_SUCCESS"):
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
                base.verification_notes = outcome.verification_notes
                base.failure_reason = outcome.failure_reason
                return base
            # Verified endpoint but non-successful outcome (e.g. BULK_MANUAL,
            # NO_RECORDS, SCHEMA_MISMATCH) -> fall through to the next source.
            base.attempts[-1]["failure_reason"] = outcome.status
            base.attempts[-1]["verification"] = verification.status
            base.attempts[-1]["note"] = outcome.message
            continue

        # Verification failed for this source -> record and try the next.
        mapped = _connector_failure_status(verification)
        base.attempts[-1]["failure_reason"] = mapped
        base.attempts[-1]["verification"] = verification.status
        base.attempts[-1]["note"] = verification.message

    # All supported sources failed verification/download. Report the
    # highest-priority (first) source's outcome as the final status.
    if base.attempts:
        base.status = base.attempts[0].get("failure_reason", "NOT_VERIFIED")
        base.failure_reason = base.status
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
            results.append(acquire_feature(concept, iso3, start, end, out_dir, credentials))
    return results


__all__ = ["AcquiredFeature", "acquire_feature", "run_acquisition"]
