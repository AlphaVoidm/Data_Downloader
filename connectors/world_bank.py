"""World Bank World Development Indicators connector.

Semantics (per the data-engineering review):
    HTTP 200 + records      -> SUCCESS
    HTTP 200 + zero records -> NO_DATA_FOR_COUNTRY_INDICATOR (NOT a failure —
                               the indicator simply has no observations for
                               this country; never marks the country broken)
    HTTP 400                -> INVALID_REQUEST
    HTTP 404                -> ENDPOINT_OR_INDICATOR_NOT_FOUND
    HTTP 429                -> RATE_LIMITED (retried with backoff upstream)
    HTTP 5xx                -> SOURCE_TEMPORARY_FAILURE
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from response_validator import validate_response

from .base import (
    NO_DATA_FOR_COUNTRY_INDICATOR,
    EndpointVerification,
    AcquisitionOutcome,
    ConnectorError,
    _HTTP,
    acquisition_status_for_verification,
    outcome_from_result,
    verification_from_result,
)

BASE = "https://api.worldbank.org/v2/country/{iso3}/indicator/{code}"

FEATURE_INDICATORS: dict[str, tuple[str, str]] = {
    "gdp": ("NY.GDP.MKTP.CD", "current USD"),
    "gdp_growth": ("NY.GDP.MKTP.KD.ZG", "%"),
    "gdp_per_capita": ("NY.GDP.PCAP.CD", "current USD"),
    "inflation_cpi": ("FP.CPI.TOTL.ZG", "%"),
    "total_population": ("SP.POP.TOTL", "count"),
    "population_growth": ("SP.POP.GROW", "%"),
    "urban_population": ("SP.URB.TOTL", "count"),
    "urbanisation_rate": ("SP.URB.TOTL.IN.ZS", "%"),
    "electricity_access": ("EG.ELC.ACCS.ZS", "%"),
    "manufacturing_value_added": ("NV.IND.MANF.ZS", "%"),
    "total_electricity_generation": ("EG.ELC.PROD.KH", "kWh"),
}


def _indicator_code(feature: str) -> str:
    return FEATURE_INDICATORS[feature][0]


def _is_wb_message_wrapper(payload: Any) -> str:
    """Detect the World Bank API's `[{"message": [...]}]` error envelope.

    The WB API often returns HTTP 200 with a message body for an unknown
    indicator instead of a 4xx. Returns "" when the payload is normal data.
    """
    if not isinstance(payload, list) or len(payload) != 1:
        return ""
    first = payload[0]
    if isinstance(first, dict) and "message" in first:
        msgs = first["message"] or []
        text = " ".join(str(m.get("value", "")) for m in msgs if isinstance(m, dict))
        return text or "World Bank reported an error envelope"
    return ""


def _classify_payload_error(payload: Any) -> str:
    """Classify a World Bank error envelope into a granular status.

    Returns "" when the payload is normal data (no error envelope) so callers
    can distinguish "no data" from "invalid indicator".
    """
    text = _is_wb_message_wrapper(payload)
    if not text:
        return ""
    text = text.lower()
    if "invalid value" in text or "not valid" in text or "parameter" in text:
        return "INVALID_REQUEST"
    if "not found" in text or "no data" in text or "does not exist" in text:
        return "ENDPOINT_OR_INDICATOR_NOT_FOUND"
    return "NON_DATA_RESPONSE"


def verify_world_bank(country: str, feature: str) -> EndpointVerification:
    code = _indicator_code(feature)
    url = BASE.format(iso3=country, code=code)
    history: list[dict[str, Any]] = []
    try:
        resp = _HTTP.get(url, params={"format": "json", "per_page": 1, "date": "2020"},
                         timeout=30, history=history)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="world_bank", country=country, feature=feature,
            status=exc.status, message=str(exc), attempts=exc.attempts or history,
        )
    result = validate_response(resp, expected_format="json", min_records=0)
    if result.ok:
        err = _classify_payload_error(resp.json())
        if err:
            result.status = err
            result.message = f"World Bank error envelope: {_is_wb_message_wrapper(resp.json())}"
            result.data = None
    return verification_from_result(result, "world_bank", country, feature, attempts=history)


def acquire_world_bank(country: str, feature: str, start_year: int, end_year: int, out_dir: Path) -> AcquisitionOutcome:
    code, unit = FEATURE_INDICATORS[feature]
    url = BASE.format(iso3=country, code=code)
    rows: list[dict[str, Any]] = []
    page = 1
    total_pages = 1
    history: list[dict[str, Any]] = []
    last_http_status: int | None = None
    last_content_type = ""
    try:
        while page <= total_pages:
            resp = _HTTP.get(
                url, params={"format": "json", "per_page": 1000,
                             "date": f"{start_year}:{end_year}", "page": page},
                timeout=30, history=history,
            )
            last_http_status = resp.status_code
            last_content_type = resp.headers.get("Content-Type", "")
            result = validate_response(resp, expected_format="json", min_records=0)
            if not result.ok:
                if rows:
                    break
                return outcome_from_result(result, "world_bank", country, feature,
                                           attempts=history)
            payload = resp.json()
            err = _classify_payload_error(payload)
            if err:
                return AcquisitionOutcome(
                    source_id="world_bank", country=country, feature=feature,
                    status=err, message=_is_wb_message_wrapper(payload) or err,
                    failure_reason=err, attempts=history,
                    http_status=last_http_status, response_type=last_content_type,
                )
            if not isinstance(payload, list) or len(payload) < 2:
                break
            meta, data = payload[0], payload[1] or []
            total_pages = int(meta.get("pages", 1) or 1)
            for item in data:
                val = item.get("value")
                if val is None:
                    continue
                rows.append({
                    "iso3": country, "year": int(item["date"]),
                    "indicator": feature, "indicator_code": code,
                    "value": val, "unit": unit, "observed": True,
                })
            if page >= total_pages:
                break
            page += 1
    except ConnectorError as exc:
        if not rows:
            return AcquisitionOutcome(
                source_id="world_bank", country=country, feature=feature,
                status=exc.status, message=str(exc), failure_reason=exc.status,
                attempts=exc.attempts or history,
            )

    if not rows:
        # HTTP 200 (or fully-paginated 200s) with zero observations: the
        # indicator simply has no data for this country — NOT a failure.
        return AcquisitionOutcome(
            source_id="world_bank", country=country, feature=feature,
            status=NO_DATA_FOR_COUNTRY_INDICATOR,
            message=f"World Bank indicator {code} has no observations for {country} ({start_year}-{end_year})",
            failure_reason=NO_DATA_FOR_COUNTRY_INDICATOR,
            attempts=history, http_status=last_http_status, response_type=last_content_type,
        )

    df = pd.DataFrame(rows).sort_values("year")
    out_path = out_dir / "raw" / "socioeconomic" / "indicators" / "worldbank" / f"{country}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Append if multiple indicators already exist for this country.
    if out_path.exists():
        existing = pd.read_csv(out_path)
        existing = existing[existing["indicator"] != feature]
        df = pd.concat([existing, df], ignore_index=True)
    df.to_csv(out_path, index=False)

    return AcquisitionOutcome(
        source_id="world_bank", country=country, feature=feature,
        status="SUCCESS", message=f"{len(rows)} annual observations for {feature}",
        records=len(rows), path=str(out_path), frequency="annual", unit=unit,
        requested_start=str(start_year), requested_end=str(end_year),
        received_start=str(df['year'].min()), received_end=str(df['year'].max()),
        schema_columns=list(df.columns),
        verification_notes=["JSON valid", f"indicator {code}"],
        provenance={"indicator_code": code},
        attempts=history, http_status=last_http_status, response_type=last_content_type,
    )


def world_bank_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    verification = verify_world_bank(country, feature)
    if verification.status != "VERIFIED":
        return verification, AcquisitionOutcome(
            source_id="world_bank", country=country, feature=feature,
            status=acquisition_status_for_verification(verification.status),
            message=verification.message, failure_reason=verification.status,
            attempts=verification.attempts, http_status=verification.http_status,
            response_type=verification.content_type,
        )
    outcome = acquire_world_bank(country, feature, start, end, out_dir)
    return verification, outcome
