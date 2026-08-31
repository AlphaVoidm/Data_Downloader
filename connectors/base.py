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
NO_DATA = "NO_DATA"
NO_DATA_FOR_COUNTRY_INDICATOR = "NO_DATA_FOR_COUNTRY_INDICATOR"
AUTH_FAILED = "AUTH_FAILED"
RATE_LIMITED = "RATE_LIMITED"
NETWORK_ERROR = "NETWORK_ERROR"
TIMEOUT = "TIMEOUT"
PARSE_ERROR = "PARSE_ERROR"
NON_DATA_RESPONSE = "NON_DATA_RESPONSE"
SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
EMPTY_RESPONSE = "EMPTY_RESPONSE"
BULK_MANUAL = "BULK_MANUAL"
INVALID_REQUEST = "INVALID_REQUEST"
ENDPOINT_OR_INDICATOR_NOT_FOUND = "ENDPOINT_OR_INDICATOR_NOT_FOUND"
SOURCE_TEMPORARY_FAILURE = "SOURCE_TEMPORARY_FAILURE"
RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
CONFIGURATION_ERROR = "CONFIGURATION_ERROR"

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
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class AcquisitionOutcome:
    source_id: str
    country: str
    feature: str
    status: str  # SUCCESS | PARTIAL_SUCCESS | NO_DATA | ... | RETRY_EXHAUSTED
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
    attempts: list[dict[str, Any]] = field(default_factory=list)
    http_status: int | None = None
    response_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class ConnectorError(Exception):
    """Raised for unrecoverable connector problems.

    Carries the granular status code and (when available) the retry history
    so the dashboard can report "Attempts: N" without parsing strings.
    """

    def __init__(self, status: str, message: str | None = None,
                 attempts: list[dict[str, Any]] | None = None):
        self.status = status
        self.message = message or status
        self.attempts = attempts or []
        super().__init__(self.message)


# HTTP statuses that merit a retry with backoff vs. those that must not.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_NO_RETRY_STATUS = {400, 401, 403, 404, 422}


def _retry_after_seconds(resp: requests.Response, default: float) -> float:
    """Honor a Retry-After header (seconds or HTTP-date) when present."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            from datetime import datetime, timezone as _tz
            target = parsedate_to_datetime(raw.strip())
            delta = (target - datetime.now(_tz.utc)).total_seconds()
            return max(default, delta)
        except Exception:  # noqa: BLE001
            return default


class _HttpClient:
    """Throttled requests.Session with production-style retry/backoff.

    Retry policy:
        retry + backoff   429, 500, 502, 503, 504, connection reset, timeout
        never retry       400, 401, 403, 404, 422

    Every attempt (including successful ones) is appended to the optional
    ``history`` list so callers can surface "Attempts: N" honestly.
    """

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
        backoff: float = 2.0,
        history: list[dict[str, Any]] | None = None,
    ) -> requests.Response:
        attempts: list[dict[str, Any]] = []
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=timeout)
                self._last_request = time.monotonic()
                attempts.append({"attempt": attempt + 1, "http_status": resp.status_code})
                if resp.status_code in _NO_RETRY_STATUS:
                    if history is not None:
                        history.extend(attempts)
                    return resp
                if resp.status_code in _RETRYABLE_STATUS:
                    if attempt < retries - 1:
                        wait = _retry_after_seconds(resp, backoff ** (attempt + 1))
                        attempts[-1]["retry_wait_s"] = round(wait, 2)
                        time.sleep(wait)
                        continue
                    if history is not None:
                        history.extend(attempts)
                    raise ConnectorError(
                        RATE_LIMITED if resp.status_code == 429 else SOURCE_TEMPORARY_FAILURE,
                        message=f"HTTP {resp.status_code} persisted after {retries} attempts",
                        attempts=attempts,
                    )
                if history is not None:
                    history.extend(attempts)
                return resp
            except requests.exceptions.Timeout as exc:
                attempts.append({"attempt": attempt + 1, "error": "timeout"})
                if attempt < retries - 1:
                    time.sleep(backoff ** attempt)
                    continue
                if history is not None:
                    history.extend(attempts)
                raise ConnectorError(TIMEOUT, attempts=attempts) from exc
            except requests.exceptions.RequestException as exc:
                # Includes connection reset (ConnectionError), SSL EOF, DNS, etc.
                attempts.append({"attempt": attempt + 1, "error": type(exc).__name__})
                if attempt < retries - 1:
                    time.sleep(backoff ** attempt)
                    continue
                if history is not None:
                    history.extend(attempts)
                raise ConnectorError(NETWORK_ERROR, attempts=attempts) from exc
        if history is not None:
            history.extend(attempts)
        raise ConnectorError(RETRY_EXHAUSTED, attempts=attempts)


_HTTP = _HttpClient()


def get_credential(credentials: dict[str, str] | None, env_var: str) -> str | None:
    """Resolve a credential from the explicit mapping or environment.

    Delegates to the central credential manager so naming drift
    (e.g. ENTSOE_API_KEY vs ENTSOE_API_TOKEN) never silently drops a key.
    """
    from credential_manager import get_credential as _resolve, CREDENTIAL_ENVS, ENV_TO_SOURCE
    source_id = ENV_TO_SOURCE.get(env_var)
    if source_id:
        val = _resolve(source_id, credentials)
        if val:
            return val
    if credentials and credentials.get(env_var):
        return credentials[env_var]
    return os.getenv(env_var)


def acquisition_status_for_verification(status: str) -> str:
    """Map an endpoint-verification status onto the acquisition status vocabulary.

    Preserves granularity so e.g. an HTML portal or invalid JSON is never
    silently collapsed to ``NOT_VERIFIED`` (an HTTP 200 is never success).
    """
    s = (status or "").strip()
    if s in ("VERIFIED", "SUCCESS"):
        return SUCCESS
    return {
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
        NO_DATA: NO_DATA,
        NO_DATA_FOR_COUNTRY_INDICATOR: NO_DATA_FOR_COUNTRY_INDICATOR,
        INVALID_REQUEST: INVALID_REQUEST,
        ENDPOINT_OR_INDICATOR_NOT_FOUND: ENDPOINT_OR_INDICATOR_NOT_FOUND,
        SOURCE_TEMPORARY_FAILURE: SOURCE_TEMPORARY_FAILURE,
        RETRY_EXHAUSTED: RETRY_EXHAUSTED,
        CONFIGURATION_ERROR: CONFIGURATION_ERROR,
        "DEPENDENCY_MISSING": "DEPENDENCY_MISSING",
        "MAPPING_REQUIRED": "MAPPING_REQUIRED",
        "NOT_SUPPORTED": "NOT_SUPPORTED",
        "SKIPPED": "SKIPPED",
        "BULK_MANUAL": "BULK_MANUAL",
    }.get(s, "NOT_VERIFIED")


def verification_from_result(
    result: ValidationResult, source_id: str, country: str, feature: str,
    attempts: list[dict[str, Any]] | None = None,
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
        attempts=list(attempts or []),
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
    attempts: list[dict[str, Any]] | None = None,
) -> AcquisitionOutcome:
    """Map a ValidationResult onto an AcquisitionOutcome."""
    if result.ok:
        return AcquisitionOutcome(
            source_id=source_id, country=country, feature=feature, status=SUCCESS,
            message=result.message, records=records, path=path, frequency=frequency,
            unit=unit, received_start=received_start, received_end=received_end,
            schema_columns=schema_columns or [], verification_notes=verification_notes or [],
            provenance=provenance or {}, attempts=list(attempts or []),
            http_status=result.http_status, response_type=result.content_type,
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
        NO_DATA: NO_DATA,
        NO_DATA_FOR_COUNTRY_INDICATOR: NO_DATA_FOR_COUNTRY_INDICATOR,
        INVALID_REQUEST: INVALID_REQUEST,
        ENDPOINT_OR_INDICATOR_NOT_FOUND: ENDPOINT_OR_INDICATOR_NOT_FOUND,
        SOURCE_TEMPORARY_FAILURE: SOURCE_TEMPORARY_FAILURE,
        RETRY_EXHAUSTED: RETRY_EXHAUSTED,
        CONFIGURATION_ERROR: CONFIGURATION_ERROR,
    }.get(failure, PARSE_ERROR)
    return AcquisitionOutcome(
        source_id=source_id, country=country, feature=feature, status=status,
        message=result.message, failure_reason=failure,
        attempts=list(attempts or []), http_status=result.http_status,
        response_type=result.content_type,
    )


__all__ = [
    "SUCCESS", "PARTIAL_SUCCESS", "NO_RECORDS", "NO_DATA",
    "NO_DATA_FOR_COUNTRY_INDICATOR", "AUTH_FAILED", "RATE_LIMITED",
    "NETWORK_ERROR", "TIMEOUT", "PARSE_ERROR", "NON_DATA_RESPONSE",
    "SCHEMA_MISMATCH", "EMPTY_RESPONSE", "BULK_MANUAL", "INVALID_REQUEST",
    "ENDPOINT_OR_INDICATOR_NOT_FOUND", "SOURCE_TEMPORARY_FAILURE",
    "RETRY_EXHAUSTED", "CONFIGURATION_ERROR",
    "VERIFIED", "NOT_VERIFIED", "SKIPPED",
    "EndpointVerification", "AcquisitionOutcome", "ConnectorError",
    "get_credential", "acquisition_status_for_verification",
    "verification_from_result", "outcome_from_result", "_HTTP",
]
