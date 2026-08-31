"""Robust response validation for HGT-QF acquisition.

Never assume HTTP 200 == valid data. A 200 can still be an HTML portal page,
an authentication redirect, or an error page.

Validates, in order:
    1. HTTP status
    2. Content-Type
    3. response size
    4. response signature / magic bytes (XML/JSON/CSV detection)
    5. HTML/portal detection (PORTAL_HTML / NON_DATA_RESPONSE)
    6. XML validity
    7. JSON validity
    8. CSV structure
    9. expected schema / columns
    10. record count / presence of actual data values

Granular failure reasons:
    AUTH_FAILED, PORTAL_HTML, NON_DATA_RESPONSE, INVALID_XML, INVALID_JSON,
    INVALID_CSV, EMPTY_RESPONSE, SCHEMA_MISMATCH, NO_RECORDS, RATE_LIMITED,
    NETWORK_ERROR, TIMEOUT
"""
from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

# --- Granular failure / success codes --------------------------------------
OK = "OK"
AUTH_FAILED = "AUTH_FAILED"
PORTAL_HTML = "PORTAL_HTML"
NON_DATA_RESPONSE = "NON_DATA_RESPONSE"
INVALID_XML = "INVALID_XML"
INVALID_JSON = "INVALID_JSON"
INVALID_CSV = "INVALID_CSV"
EMPTY_RESPONSE = "EMPTY_RESPONSE"
SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
NO_RECORDS = "NO_RECORDS"
RATE_LIMITED = "RATE_LIMITED"
NETWORK_ERROR = "NETWORK_ERROR"
TIMEOUT = "TIMEOUT"

# HTML signatures used to detect portal / auth / error pages.
_HTML_SIGNATURES = (
    b"<!doctype html", b"<html", b"<head", b"<body", b"<title", b"<meta",
    b"<script", b"<form", b"login", b"sign in",
)

_PORTAL_TITLE_MARKERS = (
    "transparency platform", "login", "sign in", "authentication",
    "portal", "dashboard", "oops", "error", "not found", "captcha",
)


@dataclass
class ValidationResult:
    status: str            # OK or one of the granular codes above
    message: str = ""
    http_status: int | None = None
    content_type: str = ""
    size_bytes: int = 0
    record_count: int = 0
    columns: list[str] = field(default_factory=list)
    preview: str = ""
    data: Any = None       # parsed payload (json object / list / csv rows)

    @property
    def ok(self) -> bool:
        return self.status == OK


def is_html(content_type: str, body: bytes) -> bool:
    ct = (content_type or "").lower()
    if "html" in ct:
        return True
    head = body[:512].lower()
    return any(sig in head for sig in _HTML_SIGNATURES)


def looks_like_portal(content_type: str, body: bytes) -> bool:
    """Distinguish a portal/auth page from other HTML."""
    head = body[:4000].lower()
    if b"<html" in head or b"<!doctype html" in head:
        for marker in _PORTAL_TITLE_MARKERS:
            if marker.encode() in head:
                return True
        # Generic HTML without a data document -> portal
        return True
    return False


def _detect_format(content_type: str, body: bytes) -> str | None:
    ct = (content_type or "").lower()
    stripped = body.lstrip()[:200]
    if "xml" in ct or stripped.startswith((b"<?xml", b"<")) and b"<" in stripped:
        return "xml"
    if "json" in ct or stripped.startswith((b"{", b"[")):
        return "json"
    if "csv" in ct or "text/plain" in ct or b"," in body[:200]:
        return "csv"
    return None


def _parse_xml(body: bytes) -> tuple[Any, str]:
    try:
        root = ET.fromstring(body)
        return root, ""
    except ET.ParseError as exc:
        return None, str(exc)


def _parse_json(body: bytes) -> tuple[Any, str]:
    try:
        return json.loads(body.decode("utf-8", errors="replace")), ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _parse_csv(body: bytes) -> tuple[list[dict[str, Any]], str]:
    try:
        text = body.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = [row for row in reader]
        return rows, ""
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def _xml_record_count(root: Any) -> int:
    if root is None:
        return 0
    # ENTSO-E-style documents: count TimeSeries/Point payloads.
    ns = {"ts": "*"}
    points = root.findall(".//{*}Point")
    if points:
        return len(points)
    series = root.findall(".//{*}TimeSeries")
    return len(series)


def validate_response(
    response: Any,
    expected_format: str = "json",
    required_columns: list[str] | None = None,
    min_records: int = 1,
) -> ValidationResult:
    """Validate a `requests.Response` object. Returns a ValidationResult."""
    http_status = response.status_code
    content_type = response.headers.get("Content-Type", "")
    body = response.content or b""

    result = ValidationResult(
        status=OK, http_status=http_status, content_type=content_type,
        size_bytes=len(body), preview=body[:300].decode("utf-8", errors="replace"),
    )

    # 1. HTTP status
    if http_status in (401, 403):
        result.status = AUTH_FAILED
        result.message = f"HTTP {http_status} authentication failed"
        return result
    if http_status == 429:
        result.status = RATE_LIMITED
        result.message = "HTTP 429 rate limited"
        return result
    if http_status >= 500:
        result.status = NETWORK_ERROR
        result.message = f"HTTP {http_status} server error"
        return result
    if http_status != 200:
        result.status = NON_DATA_RESPONSE
        result.message = f"Unexpected HTTP {http_status}"
        return result

    # 2. Empty response
    if len(body) == 0 or body.isspace():
        result.status = EMPTY_RESPONSE
        result.message = "HTTP 200 but empty response body"
        return result

    # 3. HTML / portal detection BEFORE format parsing
    if is_html(content_type, body):
        if looks_like_portal(content_type, body):
            result.status = PORTAL_HTML
            result.message = "HTTP 200 but response is an HTML portal/auth page, not data"
        else:
            result.status = NON_DATA_RESPONSE
            result.message = "HTTP 200 but response is HTML, not the expected data format"
        return result

    # 4. Format detection vs expectation
    detected = _detect_format(content_type, body)
    if detected is None:
        result.status = NON_DATA_RESPONSE
        result.message = f"Cannot identify data format (Content-Type: {content_type!r})"
        return result
    if expected_format and detected != expected_format:
        result.status = NON_DATA_RESPONSE
        result.message = f"Expected {expected_format} but detected {detected} (Content-Type: {content_type!r})"
        return result

    # 5. Parse + schema + record count
    if detected == "xml":
        root, err = _parse_xml(body)
        if err:
            result.status = INVALID_XML
            result.message = f"XML parse error: {err}"
            return result
        result.record_count = _xml_record_count(root)
        result.data = root
        if result.record_count < min_records:
            result.status = NO_RECORDS
            result.message = f"XML parsed but found {result.record_count} record(s)"
            return result
        return result

    if detected == "json":
        data, err = _parse_json(body)
        if err:
            result.status = INVALID_JSON
            result.message = f"JSON parse error: {err}"
            return result
        result.data = data
        if isinstance(data, list):
            result.record_count = len(data)
        elif isinstance(data, dict):
            # common payload shapes
            for key in ("data", "rows", "records", "results"):
                if isinstance(data.get(key), list):
                    result.record_count = len(data[key])
                    result.data = data[key]
                    break
            else:
                result.record_count = 1 if data else 0
        if result.record_count < min_records:
            result.status = NO_RECORDS
            result.message = f"JSON parsed but found {result.record_count} record(s)"
            return result
        if required_columns and result.data and isinstance(result.data, list):
            missing = [c for c in required_columns if c not in result.data[0]]
            if missing:
                result.status = SCHEMA_MISMATCH
                result.message = f"JSON records missing expected columns: {missing}"
                return result
        return result

    if detected == "csv":
        rows, err = _parse_csv(body)
        if err:
            result.status = INVALID_CSV
            result.message = f"CSV parse error: {err}"
            return result
        result.data = rows
        result.record_count = len(rows)
        result.columns = list(rows[0].keys()) if rows else []
        if result.record_count < min_records:
            result.status = NO_RECORDS
            result.message = f"CSV parsed but found {result.record_count} record(s)"
            return result
        if required_columns:
            missing = [c for c in required_columns if c not in result.columns]
            if missing:
                result.status = SCHEMA_MISMATCH
                result.message = f"CSV missing expected columns: {missing}"
                return result
        return result

    result.status = NON_DATA_RESPONSE
    result.message = "Unhandled validation path"
    return result


__all__ = [
    "ValidationResult", "validate_response", "is_html", "looks_like_portal",
    "OK", "AUTH_FAILED", "PORTAL_HTML", "NON_DATA_RESPONSE", "INVALID_XML",
    "INVALID_JSON", "INVALID_CSV", "EMPTY_RESPONSE", "SCHEMA_MISMATCH",
    "NO_RECORDS", "RATE_LIMITED", "NETWORK_ERROR", "TIMEOUT",
]
