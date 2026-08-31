"""U.S. EIA Open Data API v2 connector.

Uses metadata/facet discovery to find the correct series and geography rather
than hard-coding a single series (spec §7). Loads the key from EIA_API_KEY and
never prints it. Throttles requests and applies retry/backoff.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from response_validator import validate_response

from .base import (
    AUTH_FAILED,
    EndpointVerification,
    AcquisitionOutcome,
    ConnectorError,
    _HTTP,
    get_credential,
    outcome_from_result,
    verification_from_result,
)

ENDPOINT = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
KEY_ENV = "EIA_API_KEY"
DEFAULT_RESPONDENT = "US48"
REQUIRED_COLUMNS = ["period", "value"]


def _sample_request(key: str, respondent: str, days: int = 1) -> dict[str, Any]:
    return {
        "api_key": key,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[type][]": "D",
        "facets[respondent][]": respondent,
        "start": "2024-01-01T00",
        "end": f"2024-01-0{days}T23",
        "length": 5000,
    }


def discover_respondents(key: str) -> list[dict[str, str]]:
    """Attempt facet discovery; fall back to the known contiguous-US respondent."""
    try:
        resp = _HTTP.get(
            ENDPOINT,
            params={
                "api_key": key, "data[0]": "value", "facets[type][]": "D", "length": 1,
                "start": "2024-01-01T00", "end": "2024-01-01T01",
            },
            timeout=30,
        )
    except ConnectorError:
        return [{"code": DEFAULT_RESPONDENT, "name": "United States Lower 48"}]

    try:
        payload = resp.json()
        facets = payload.get("response", {}).get("facets")
        if facets:
            respondents = facets.get("respondent") or []
            if respondents:
                return [{"code": r.get("id", ""), "name": r.get("name", "")} for r in respondents]
    except Exception:
        pass

    # Fallback: inspect sample data rows.
    try:
        rows = resp.json().get("response", {}).get("data", [])
        codes = {r.get("respondent", "") for r in rows if r.get("respondent")}
        if codes:
            return [{"code": c, "name": c} for c in sorted(codes)]
    except Exception:
        pass

    return [{"code": DEFAULT_RESPONDENT, "name": "United States Lower 48"}]


def verify_eia(country: str, key: str | None) -> EndpointVerification:
    if country != "USA":
        return EndpointVerification(
            source_id="eia", country=country, feature="electricity_demand",
            status="NOT_SUPPORTED", message=f"EIA publishes United States data only, not {country}",
        )
    if not key:
        return EndpointVerification(
            source_id="eia", country=country, feature="electricity_demand",
            status=AUTH_FAILED, message=f"{KEY_ENV} not configured",
        )
    try:
        resp = _HTTP.get(ENDPOINT, params=_sample_request(key, DEFAULT_RESPONDENT), timeout=30)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="eia", country=country, feature="electricity_demand",
            status=str(exc), message=str(exc),
        )
    result = validate_response(resp, expected_format="json", required_columns=REQUIRED_COLUMNS)
    return verification_from_result(result, "eia", country, "electricity_demand")


def acquire_eia(country: str, start_year: int, end_year: int, key: str | None, out_dir: Path) -> AcquisitionOutcome:
    if not key:
        return AcquisitionOutcome(
            source_id="eia", country="USA", feature="electricity_demand",
            status=AUTH_FAILED, message=f"{KEY_ENV} not configured", failure_reason="AUTH_FAILED",
        )
    respondents = discover_respondents(key)
    respondent = respondents[0]["code"] or DEFAULT_RESPONDENT

    rows: list[dict[str, Any]] = []
    params = {
        "api_key": key, "frequency": "hourly", "data[0]": "value",
        "facets[type][]": "D", "facets[respondent][]": respondent,
        "sort[0][column]": "period", "sort[0][direction]": "asc",
        "offset": 0, "length": 5000,
        "start": f"{start_year}-01-01T00", "end": f"{end_year}-12-31T23",
    }
    try:
        while True:
            resp = _HTTP.get(ENDPOINT, params=params, timeout=60)
            result = validate_response(resp, expected_format="json", min_records=0)
            if not result.ok:
                if rows:
                    break
                return outcome_from_result(result, "eia", country, "electricity_demand")
            items = result.data
            if not items:
                break
            for item in items:
                rows.append({
                    "period_utc": item.get("period"),
                    "value_mwh": item.get("value"),
                    "respondent": item.get("respondent", respondent),
                })
            total = resp.json().get("response", {}).get("total", 0)
            params["offset"] += len(items)
            if params["offset"] >= total:
                break
    except ConnectorError as exc:
        if not rows:
            return AcquisitionOutcome(
                source_id="eia", country="USA", feature="electricity_demand",
                status=str(exc), message=str(exc), failure_reason=str(exc),
            )

    if not rows:
        return AcquisitionOutcome(
            source_id="eia", country="USA", feature="electricity_demand",
            status="NO_RECORDS", message="EIA returned no demand rows", failure_reason="NO_RECORDS",
        )

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["period_utc"], errors="coerce")
    df = df.dropna(subset=["ts"])
    df["month"] = df["ts"].dt.to_period("M").dt.to_timestamp()
    monthly = df.groupby("month")["value_mwh"].sum() / 1e6  # MWh -> TWh
    monthly = monthly.reset_index().rename(columns={"month": "date", "value_mwh": "demand_twh"})
    monthly["unit"] = "TWh"

    out_path = out_dir / "raw" / "electricity" / "demand" / "eia" / "USA.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(out_path, index=False)

    return AcquisitionOutcome(
        source_id="eia", country="USA", feature="electricity_demand",
        status="SUCCESS", message=f"{len(monthly)} monthly demand observations ({respondent})",
        records=len(monthly), path=str(out_path), frequency="monthly", unit="TWh",
        requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
        received_start=str(monthly['date'].min())[:7], received_end=str(monthly['date'].max())[:7],
        schema_columns=list(monthly.columns),
        verification_notes=[f"respondent {respondent}", "hourly->monthly TWh aggregation"],
        provenance={"respondent": respondent},
    )


def eia_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    key = get_credential(credentials, KEY_ENV)
    verification = verify_eia(country, key)
    if verification.status != "VERIFIED":
        status = verification.status if verification.status in ("AUTH_FAILED", "NOT_SUPPORTED", "RATE_LIMITED", "NETWORK_ERROR", "TIMEOUT") else "NOT_VERIFIED"
        return verification, AcquisitionOutcome(
            source_id="eia", country=country, feature=feature, status=status,
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_eia(country, start, end, key, out_dir)
    return verification, outcome
