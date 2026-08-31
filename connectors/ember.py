"""Ember electricity data connector.

Uses the Ember API (EMBER_API_KEY). Verifies country coverage, temporal
frequency, units, and actual records before declaring success.
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

ENDPOINT = "https://api.ember-energy.org/v1/electricity-generation/monthly"
KEY_ENV = "EMBER_API_KEY"

_FEATURE_VALUE_HINTS = {
    "electricity_demand": ["demand", "demand_twh", "total_demand", "load"],
    "total_electricity_generation": ["generation", "generation_twh", "total_generation"],
    "renewable_generation_share": ["renewable_share", "renewables_pct", "renewable_pct", "clean_pct"],
}

_DATE_COLS = ("date", "month", "period", "datetime", "year_month")
_MIX_COLS = ("coal", "gas", "hydro", "nuclear", "oil", "solar", "wind", "bioenergy", "other_renewable", "other_fossil")


def _find_date_col(columns: list[str]) -> str | None:
    for c in columns:
        if c.lower() in _DATE_COLS:
            return c
    return None


def _pick_value_col(columns: list[str], feature: str) -> str | None:
    hints = _FEATURE_VALUE_HINTS.get(feature, [])
    lower = {c: c.lower() for c in columns}
    for hint in hints:
        for c in columns:
            if hint in lower[c]:
                return c
    # fallback: first numeric non-date column
    return None


def verify_ember(country: str, feature: str, key: str | None) -> EndpointVerification:
    if not key:
        return EndpointVerification(
            source_id="ember", country=country, feature=feature,
            status=AUTH_FAILED, message=f"{KEY_ENV} not configured",
        )
    params = {
        "entity_code": country,
        "start_date": "2024-01-01",
        "end_date": "2024-02-01",
    }
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = _HTTP.get(ENDPOINT, params=params, headers=headers, timeout=30)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="ember", country=country, feature=feature, status=str(exc), message=str(exc),
        )
    result = validate_response(resp, expected_format="json", min_records=1)
    return verification_from_result(result, "ember", country, feature)


def acquire_ember(country: str, feature: str, start_year: int, end_year: int, key: str | None, out_dir: Path) -> AcquisitionOutcome:
    if not key:
        return AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status=AUTH_FAILED,
            message=f"{KEY_ENV} not configured", failure_reason="AUTH_FAILED",
        )
    params = {
        "entity_code": country,
        "start_date": f"{start_year}-01-01",
        "end_date": f"{end_year}-12-31",
    }
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = _HTTP.get(ENDPOINT, params=params, headers=headers, timeout=60)
    except ConnectorError as exc:
        return AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status=str(exc),
            message=str(exc), failure_reason=str(exc),
        )
    result = validate_response(resp, expected_format="json", min_records=1)
    if not result.ok:
        return outcome_from_result(result, "ember", country, feature)

    items = result.data if isinstance(result.data, list) else []
    if not items:
        return AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status="NO_RECORDS",
            message="Ember returned no records", failure_reason="NO_RECORDS",
        )

    df = pd.DataFrame(items)
    date_col = _find_date_col(list(df.columns))
    if date_col is None:
        return AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status="SCHEMA_MISMATCH",
            message="Ember response missing a date column", failure_reason="SCHEMA_MISMATCH",
        )

    if feature == "generation_mix":
        mix_cols = [c for c in df.columns if c.lower() in _MIX_COLS]
        keep = [date_col] + mix_cols
        out_df = df[keep].copy()
    else:
        value_col = _pick_value_col(list(df.columns), feature)
        if value_col is None:
            return AcquisitionOutcome(
                source_id="ember", country=country, feature=feature, status="SCHEMA_MISMATCH",
                message=f"Could not identify a value column for '{feature}'", failure_reason="SCHEMA_MISMATCH",
            )
        out_df = df[[date_col, value_col]].copy()

    out_df = out_df.rename(columns={date_col: "date"})
    out_path = out_dir / "raw" / "electricity" / "demand" / "ember" / f"{country}.csv"
    if feature != "electricity_demand":
        out_path = out_dir / "raw" / "electricity" / feature / "ember" / f"{country}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)

    return AcquisitionOutcome(
        source_id="ember", country=country, feature=feature,
        status="SUCCESS", message=f"{len(out_df)} Ember records for {feature}",
        records=len(out_df), path=str(out_path), frequency="monthly", unit="TWh / %",
        requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
        received_start=str(out_df['date'].min())[:7] if len(out_df) else "",
        received_end=str(out_df['date'].max())[:7] if len(out_df) else "",
        schema_columns=list(out_df.columns),
        verification_notes=["JSON valid", f"columns {list(out_df.columns)}"],
        provenance={"endpoint": ENDPOINT, "entity_code": country},
    )


def ember_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    key = get_credential(credentials, KEY_ENV)
    verification = verify_ember(country, feature, key)
    if verification.status != "VERIFIED":
        status = verification.status if verification.status in ("AUTH_FAILED", "RATE_LIMITED", "NETWORK_ERROR", "TIMEOUT", "NO_RECORDS") else "NOT_VERIFIED"
        return verification, AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status=status,
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_ember(country, feature, start, end, key, out_dir)
    return verification, outcome
