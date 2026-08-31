"""Shared connector infrastructure: HTTP client, retry/backoff, and status models."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from response_validator import (
    AUTH_FAILED,
    EMPTY_RESPONSE,
    INVALID_CSV,
    INVALID_JSON,
    INVALID_XML,
    NETWORK_ERROR,
    NO_RECORDS,
    NON_DATA_RESPONSE,
    PORTAL_HTML,
    RATE_LIMITED,
    SCHEMA_MISMATCH,
    TIMEOUT,
    ValidationResult,
)

# --- Acquisition statuses (per spec §14) ------------------------------------
SUCCESS = "SUCCESS"
PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
NO_RECORDS = "NO_RECORDS"
AUTH_FAILED = "AUTH_FAILED"
RATE_LIMITED = "RATE_LIMITED"
NETWORK_ERROR = "NETWORK_ERROR"
TIMEOUT = "TIMEOUT"
PARSE_ERROR = "PARSE_ERROR"
NON_DATA_RESPONSE = "NON_DATA_RESPONSE"
SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
EMPTY_RESPONSE = "EMPTY_RESPONSE"
BULK_MANUAL = "BULK_MANUAL"

# --- Endpoint verification statuses -----------------------------------------
VERIFIED = "VERIFIED"
NOT_VERIFIED = "NOT_VERIFIED"
SKIPPED = "SKIPPED"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EndpointVerification:
    source_id: str
    country: str
    feature: str
    status: str  # VERIFIED | AUTH_FAILED | PORTAL_HTML | ... | SKIPPED
    message: str
    http_status: int | None = None
    content_type: str = ""
    checked_at: str = field(default_factory=now_utc)
    preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class AcquisitionOutcome:
    source_id: str
    country: str
    feature: str
    status: str  # SUCCESS | PARTIAL_SUCCESS | NO_RECORDS | ...
    message: str
    records: int = 0
    path: str = ""
    frequency: str = ""
    unit: str = ""
    requested_start: str = ""
    requested_end: str = ""
    received_start: str = ""
    received_end: str = ""
    schema_columns: list[str] = field(default_factory=list)
    verification_notes: list[str] = field(default_factory=list)
    failure_reason: str = ""   # granular code from the response validator
    retrieved_at: str = field(default_factory=now_utc)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class ConnectorError(Exception):
    """Raised for unrecoverable connector problems."""


class _HttpClient:
    """Throttled requests.Session with retry/backoff."""

    def __init__(self, user_agent: str = "HGT-QF-DataDesk/3.0 (academic-research)"):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.min_interval = 0.2
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 60,
        retries: int = 3,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=timeout)
                self._last_request = time.monotonic()
                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = 2 ** (attempt + 1)
                    time.sleep(wait)
                    continue
                return resp
            except requests.exceptions.Timeout as exc:
                last_error = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise ConnectorError(TIMEOUT) from exc
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        raise ConnectorError(NETWORK_ERROR) from last_error


_HTTP = _HttpClient()


def get_credential(credentials: dict[str, str] | None, env_var: str) -> str | None:
    if credentials and credentials.get(env_var):
        return credentials[env_var]
    return os.getenv(env_var)


def verification_from_result(
    result: ValidationResult, source_id: str, country: str, feature: str
) -> EndpointVerification:
    """Map a ValidationResult onto an EndpointVerification."""
    status = VERIFIED if result.ok else result.status
    return EndpointVerification(
        source_id=source_id,
        country=country,
        feature=feature,
        status=status,
        message=result.message,
        http_status=result.http_status,
        content_type=result.content_type,
        preview=result.preview[:200],
    )


def outcome_from_result(
    result: ValidationResult,
    source_id: str,
    country: str,
    feature: str,
    records: int = 0,
    path: str = "",
    frequency: str = "",
    unit: str = "",
    received_start: str = "",
    received_end: str = "",
    schema_columns: list[str] | None = None,
    verification_notes: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> AcquisitionOutcome:
    """Map a ValidationResult onto an AcquisitionOutcome."""
    if result.ok:
        return AcquisitionOutcome(
            source_id=source_id, country=country, feature=feature, status=SUCCESS,
            message=result.message, records=records, path=path, frequency=frequency,
            unit=unit, received_start=received_start, received_end=received_end,
            schema_columns=schema_columns or [], verification_notes=verification_notes or [],
            provenance=provenance or {},
        )
    failure = result.status
    status = {
        AUTH_FAILED: AUTH_FAILED,
        RATE_LIMITED: RATE_LIMITED,
        NETWORK_ERROR: NETWORK_ERROR,
        TIMEOUT: TIMEOUT,
        PORTAL_HTML: NON_DATA_RESPONSE,
        NON_DATA_RESPONSE: NON_DATA_RESPONSE,
        INVALID_XML: PARSE_ERROR,
        INVALID_JSON: PARSE_ERROR,
        INVALID_CSV: PARSE_ERROR,
        EMPTY_RESPONSE: EMPTY_RESPONSE,
        SCHEMA_MISMATCH: SCHEMA_MISMATCH,
        NO_RECORDS: NO_RECORDS,
    }.get(failure, PARSE_ERROR)
    return AcquisitionOutcome(
        source_id=source_id, country=country, feature=feature, status=status,
        message=result.message, failure_reason=failure,
    )


__all__ = [
    "SUCCESS", "PARTIAL_SUCCESS", "NO_RECORDS", "AUTH_FAILED", "RATE_LIMITED",
    "NETWORK_ERROR", "TIMEOUT", "PARSE_ERROR", "NON_DATA_RESPONSE",
    "SCHEMA_MISMATCH", "EMPTY_RESPONSE", "BULK_MANUAL",
    "VERIFIED", "NOT_VERIFIED", "SKIPPED",
    "EndpointVerification", "AcquisitionOutcome", "ConnectorError",
    "get_credential", "verification_from_result", "outcome_from_result", "_HTTP",
]
