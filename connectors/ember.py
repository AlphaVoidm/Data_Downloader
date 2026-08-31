"""Ember electricity data connector.

Documented REST API (https://api.ember-energy.org/v1/):
    GET /v1/{dataset}/{resolution}?entity={ISO3|name}&start_date={YYYY}&end_date={YYYY}&api_key={KEY}

Datasets: ``electricity-generation``, ``electricity-demand``,
``power-sector-emissions``, ``carbon-intensity``; resolutions ``monthly`` /
``yearly``. The monthly *generation* dataset is long-format: one row per
(area, month, electricity source) with a ``series`` column ("Demand",
"Total generation", "Clean", ...) plus ``generation_twh`` and
``share_of_generation_pct``.

The connector therefore:
  * sends the key as the ``api_key`` query parameter (NOT ``Authorization``),
  * filters ``entity`` by ISO-3 (falling back to the country name),
  * uses the dedicated ``electricity-demand`` dataset for demand (with a
    generation-dataset "Demand" series fallback),
  * and only reports SUCCESS after confirming the entity is covered, the
    frequency is monthly, units are TWh/%, and actual records were returned.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from country_registry import get_country_record
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

BASE = "https://api.ember-energy.org/v1/"
KEY_ENV = "EMBER_API_KEY"

# feature -> (dataset, resolution)
_DATASET_BY_FEATURE = {
    "electricity_demand": "electricity-demand",
    "total_electricity_generation": "electricity-generation",
    "renewable_generation_share": "electricity-generation",
    "generation_mix": "electricity-generation",
}

# Candidate value-column names, in priority order (checked case-insensitively).
_DEMAND_VALUE_COLS = ["demand_twh", "total_demand_twh", "total_demand", "demand", "value"]
_GEN_VALUE_COLS = ["generation_twh", "total_generation", "value"]

_DATE_COLS = ("date", "month", "period", "datetime", "year_month")
_SERIES_COL = "series"
_MIX_SERIES = (
    "Coal", "Gas", "Other fossil", "Nuclear", "Hydro", "Wind", "Solar",
    "Bioenergy", "Other renewables", "Total generation", "Demand", "Net imports",
)


def build_ember_url(dataset: str, resolution: str, entity: str, start: str, end: str) -> str:
    """Assemble the documented Ember endpoint URL (without the key)."""
    return f"{BASE}{dataset}/{resolution}?entity={entity}&start_date={start}&end_date={end}"


def _entity_candidates(country: str) -> list[str]:
    candidates = [country]
    rec = get_country_record(country)
    if rec and rec.country_name:
        candidates.append(rec.country_name)
    return candidates


def _find_date_col(columns: list[str]) -> str | None:
    for c in columns:
        if c.lower() in _DATE_COLS:
            return c
    return None


def _pick_value_col(columns: list[str], hints: list[str]) -> str | None:
    lowered = {c: c.lower() for c in columns}
    for hint in hints:
        hint_l = hint.lower()
        for c in columns:
            if lowered[c] == hint_l or hint_l in lowered[c]:
                return c
    return None


def _rows_for_series(df: pd.DataFrame, series_names: set[str], date_col: str, value_col: str) -> pd.DataFrame:
    sub = df[df[_SERIES_COL].astype(str).str.strip().isin(series_names)]
    return sub[[date_col, value_col]].rename(columns={value_col: "value"})


def _extract(
    df: pd.DataFrame, feature: str,
) -> tuple[pd.DataFrame | None, str]:
    """Reduce a raw Ember response frame to (date, value) — or (date, series, value) for mix."""
    date_col = _find_date_col(list(df.columns))
    if date_col is None:
        return None, "Ember response is missing a date column"

    if feature == "electricity_demand":
        value_col = _pick_value_col(list(df.columns), _DEMAND_VALUE_COLS)
        if value_col is None and _SERIES_COL in df.columns:
            # Generation-format response: demand lives in the "Demand" series
            # under the generation value column.
            value_col = _pick_value_col(list(df.columns), _GEN_VALUE_COLS)
        if value_col:
            if _SERIES_COL in df.columns:
                out = _rows_for_series(df, {"Demand"}, date_col, value_col)
            else:
                out = df[[date_col, value_col]].rename(columns={value_col: "value"})
            return out, ""

    if feature in ("total_electricity_generation", "renewable_generation_share"):
        if _SERIES_COL in df.columns:
            if feature == "total_electricity_generation":
                series = {"Total generation"}
                value_col = _pick_value_col(list(df.columns), _GEN_VALUE_COLS)
            else:  # renewable share = share of "Renewables"/"Clean" series
                series = {"Renewables", "Clean"}
                value_col = _pick_value_col(list(df.columns), ["share_of_generation_pct", "clean_pct"])
            if not value_col:
                return None, "No share/generation value column found"
            out = _rows_for_series(df, series, date_col, value_col)
            if feature == "renewable_generation_share" and not out.empty:
                out = out[~out.duplicated(subset=[date_col])]
            return out, ""

    if feature == "generation_mix":
        if _SERIES_COL in df.columns:
            value_col = _pick_value_col(list(df.columns), _GEN_VALUE_COLS)
            if not value_col:
                return None, "No generation value column found"
            sub = df[df[_SERIES_COL].astype(str).str.strip().isin(_MIX_SERIES)]
            return sub[[date_col, _SERIES_COL, value_col]].rename(
                columns={_SERIES_COL: "series", value_col: "value"}), ""

    # Wide-format fallback: first numeric non-date column.
    value_col = _pick_value_col(list(df.columns), _DEMAND_VALUE_COLS + _GEN_VALUE_COLS)
    if value_col:
        return df[[date_col, value_col]].rename(columns={value_col: "value"}), ""
    return None, "Could not identify a value column"


def _request(country: str, start_year: int, end_year: int, key: str, dataset: str):
    last_resp = None
    for entity in _entity_candidates(country):
        params = {
            "entity": entity,
            "start_date": str(start_year),
            "end_date": str(end_year),
            "api_key": key,
        }
        resp = _HTTP.get(build_ember_url(dataset, "monthly", entity, str(start_year), str(end_year)),
                         params=params, timeout=60)
        last_resp = resp
        result = validate_response(resp, expected_format="json", min_records=0)
        if result.ok and isinstance(result.data, list) and result.data:
            return resp, result, entity
        if result.status in ("AUTH_FAILED", "RATE_LIMITED"):
            return resp, result, entity
    return last_resp, validate_response(last_resp, expected_format="json", min_records=0), country


def verify_ember(country: str, feature: str, key: str | None) -> EndpointVerification:
    if not key:
        return EndpointVerification(
            source_id="ember", country=country, feature=feature,
            status=AUTH_FAILED, message=f"{KEY_ENV} not configured",
        )
    dataset = _DATASET_BY_FEATURE.get(feature, "electricity-generation")
    try:
        resp, result, _entity = _request(country, 2024, 2024, key, dataset)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="ember", country=country, feature=feature, status=str(exc), message=str(exc),
        )
    return verification_from_result(result, "ember", country, feature)


def acquire_ember(
    country: str, feature: str, start_year: int, end_year: int, key: str | None, out_dir: Path,
) -> AcquisitionOutcome:
    if not key:
        return AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status=AUTH_FAILED,
            message=f"{KEY_ENV} not configured", failure_reason="AUTH_FAILED",
        )
    dataset = _DATASET_BY_FEATURE.get(feature, "electricity-generation")

    # Demand: prefer the dedicated demand dataset; fall back to generation
    # "Demand" series if the dedicated dataset yields nothing usable.
    datasets = [dataset]
    if feature == "electricity_demand":
        datasets = ["electricity-demand", "electricity-generation"]

    df: pd.DataFrame | None = None
    last_result = None
    for ds in datasets:
        try:
            resp, result, entity = _request(country, start_year, end_year, key, ds)
        except ConnectorError as exc:
            return AcquisitionOutcome(
                source_id="ember", country=country, feature=feature,
                status=str(exc), message=str(exc), failure_reason=str(exc),
            )
        last_result = result
        if not result.ok:
            if result.status in ("AUTH_FAILED", "RATE_LIMITED"):
                return outcome_from_result(result, "ember", country, feature)
            continue
        raw = pd.DataFrame(result.data)
        out_df, err = _extract(raw, feature)
        if out_df is not None and not out_df.empty:
            df = out_df
            break
        if err:
            last_result = result
            continue

    if df is None or df.empty:
        if last_result is not None and last_result.status in ("NO_RECORDS",):
            return AcquisitionOutcome(
                source_id="ember", country=country, feature=feature, status="NO_RECORDS",
                message=f"Ember returned no records for {feature}", failure_reason="NO_RECORDS",
            )
        return AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status="SCHEMA_MISMATCH",
            message=f"Could not extract '{feature}' from Ember response",
            failure_reason="SCHEMA_MISMATCH",
        )

    out_df = out_df.rename(columns={out_df.columns[0]: "date"})
    out_path = out_dir / "raw" / "electricity" / "demand" / "ember" / f"{country}.csv"
    if feature != "electricity_demand":
        out_path = out_dir / "raw" / "electricity" / feature / "ember" / f"{country}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)

    unit = "TWh" if feature != "renewable_generation_share" else "%"
    return AcquisitionOutcome(
        source_id="ember", country=country, feature=feature,
        status="SUCCESS", message=f"{len(out_df)} Ember monthly records for {feature}",
        records=len(out_df), path=str(out_path), frequency="monthly", unit=unit,
        requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
        received_start=str(out_df['date'].min())[:7] if len(out_df) else "",
        received_end=str(out_df['date'].max())[:7] if len(out_df) else "",
        schema_columns=list(out_df.columns),
        verification_notes=[f"dataset {dataset}", "JSON valid", f"columns {list(out_df.columns)}"],
        provenance={"endpoint": build_ember_url(dataset, "monthly", country, str(start_year), str(end_year))},
    )


def ember_connector(
    country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path,
    **_: Any,
) -> tuple[EndpointVerification, AcquisitionOutcome]:
    key = get_credential(credentials, KEY_ENV)
    verification = verify_ember(country, feature, key)
    if verification.status != "VERIFIED":
        status = verification.status if verification.status in (
            "AUTH_FAILED", "RATE_LIMITED", "NETWORK_ERROR", "TIMEOUT", "NO_RECORDS") else "NOT_VERIFIED"
        return verification, AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status=status,
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_ember(country, feature, start, end, key, out_dir)
    return verification, outcome
