"""Ember electricity data connector.

Official REST API (https://api.ember-energy.org/v1/, OpenAPI 1.1.0):

    GET /v1/{dataset}/{resolution}
        ?entity_code={ISO3}&start_date={YYYY or YYYY-MM}&end_date={...}&api_key={KEY}
    GET /v1/options/{dataset}/{resolution}/{filter_name}    (discovery)

Datasets: ``electricity-generation``, ``electricity-demand``,
``power-sector-emissions``, ``carbon-intensity``; resolutions ``monthly`` /
``yearly``.

Contract notes (from the official OpenAPI spec):

  * countries are selected with ``entity_code`` (the 3-letter ISO code),
    NOT ``entity`` (which is the country *name*);
  * monthly endpoints take ``start_date``/``end_date`` as ``YYYY-MM``
    (yearly endpoints take ``YYYY``);
  * a successful response is ``{"stats": {...}, "data": [rows]}`` — demand
    rows are wide (``demand_twh`` per month), generation rows are long
    (``series`` + ``generation_twh``).

The connector therefore:
  * resolves the country's Ember entity via the ``/options/...`` discovery
    endpoint (falling back to the ISO-3 ``entity_code``), then queries with
    ``entity_code`` + a name-based retry;
  * uses the dedicated ``electricity-demand`` dataset for demand (with a
    generation-dataset "Demand" series fallback — the demand *series* Ember
    itself publishes, never a fabrication from "Total generation");
  * and only reports SUCCESS after confirming the entity is covered, the
    frequency is monthly, units are TWh/%, and actual records were returned.
"""
from __future__ import annotations

import io
import os
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
    acquisition_status_for_verification,
    outcome_from_result,
    verification_from_result,
)

BASE = "https://api.ember-energy.org/v1/"
KEY_ENV = "EMBER_API_KEY"

# Ember's open bulk long-format CSVs (public downloads bucket). Used as the
# documented fallback when the API returns zero rows for a country: "API zero"
# must never be conflated with "Ember does not have this country". Configurable
# via the EMBER_BULK_URLS env var (comma-separated).
_BULK_URLS = (
    "https://storage.googleapis.com/emb-prod-bkt-publicdata/public-downloads/"
    "monthly_full_release_long_format.csv",
    "https://storage.googleapis.com/emb-prod-bkt-publicdata/public-downloads/"
    "yearly_full_release_long_format.csv",
)

# feature -> dataset (resolution is always "monthly" for HGT-QF)
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


def _date_arg(year: int | str, resolution: str, end: bool = False) -> str:
    """Monthly endpoints take YYYY-MM; yearly endpoints take YYYY."""
    y = int(year)
    if resolution == "monthly":
        return f"{y:04d}-12" if end else f"{y:04d}-01"
    return str(y)


def build_ember_url(dataset: str, resolution: str, entity_code: str, start: int | str, end: int | str) -> str:
    """Assemble the documented Ember endpoint URL (without the key).

    Uses ``entity_code`` (the 3-letter ISO code) and the correct per-resolution
    date format.
    """
    return (
        f"{BASE}{dataset}/{resolution}"
        f"?entity_code={entity_code}"
        f"&start_date={_date_arg(start, resolution)}"
        f"&end_date={_date_arg(end, resolution, end=True)}"
    )


def _entity_candidates(country: str) -> list[str]:
    """Legacy helper: ISO-3 code first, then the registry name (kept for auth-check)."""
    candidates = [country]
    rec = get_country_record(country)
    if rec and rec.country_name:
        candidates.append(rec.country_name)
    return candidates


def _looks_like_iso(code: str) -> bool:
    return bool(code) and len(code) == 3 and code.isalpha() and code.isupper()


def _options_values(data: Any) -> list[tuple[str, str]]:
    """Normalize an Ember ``/options/...`` response into (code, name) pairs.

    The options endpoint returns ``{"stats": ..., "data": [...]}``; entries may
    be plain strings or objects like ``{"entity_code": "EGY", "entity": "Egypt"}``.
    """
    rows = data
    if isinstance(data, dict):
        for key in ("data", "rows", "results", "values"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
        else:
            return []
    if not isinstance(rows, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in rows:
        if isinstance(item, str):
            pairs.append((item, item))
        elif isinstance(item, dict):
            code = item.get("entity_code") or item.get("code") or item.get("value") or ""
            name = item.get("entity") or item.get("name") or item.get("value") or code or ""
            pairs.append((str(code), str(name)))
    return pairs


def resolve_entity(
    country: str,
    key: str,
    dataset: str = "electricity-demand",
    resolution: str = "monthly",
    history: list[Any] | None = None,
) -> dict[str, str]:
    """Discover the canonical Ember entity for a country via /options/.

    Returns ``{"entity_code", "entity_name", "resolution_method"}``. The ISO-3
    ``entity_code`` is the canonical query key; discovery confirms it (and
    recovers the canonical entity name) rather than assuming a hard-coded
    mapping. Falls back to ``entity_code=<ISO3>`` when the options endpoint is
    unreachable.
    """
    rec = get_country_record(country)
    name = rec.country_name if rec else country
    iso3 = country
    out: dict[str, str] = {
        "entity_code": iso3,
        "entity_name": name,
        "resolution_method": "iso3_fallback",
    }

    for filter_name in ("entity_code", "entity"):
        url = f"{BASE}options/{dataset}/{resolution}/{filter_name}"
        try:
            resp = _HTTP.get(url, params={"api_key": key}, timeout=60, retries=2, history=history)
        except ConnectorError:
            # Options endpoint unreachable (network/auth) — fall back to the
            # documented ISO-3 entity_code rather than retrying the second
            # filter over the same broken endpoint.
            return out
        result = validate_response(resp, expected_format="json", min_records=0)
        if not result.ok:
            continue
        for code, entity_name in _options_values(result.data):
            if filter_name == "entity_code":
                if code.upper() == iso3.upper():
                    out["entity_code"] = code
                    if entity_name and entity_name != code:
                        out["entity_name"] = entity_name
                    out["resolution_method"] = "options"
                    return out
            else:  # entity names
                if entity_name.casefold() == name.casefold():
                    out["entity_name"] = entity_name
                    if _looks_like_iso(code):
                        out["entity_code"] = code
                    out["resolution_method"] = "options"
                    return out
    return out


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
            # under the generation value column. Only the genuine "Demand"
            # series is used — never "Total generation".
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


def _request(
    country: str,
    start_year: int,
    end_year: int,
    key: str,
    dataset: str,
    resolution: str = "monthly",
    history: list[Any] | None = None,
    entity: dict[str, str] | None = None,
):
    """Query a dataset endpoint with ``entity_code`` (canonical) then name.

    Returns ``(response, validation_result, resolved_entity)``. Returns early
    on hard errors (auth / rate-limit / not-found / invalid) or as soon as
    records come back.
    """
    if entity is None:
        entity = resolve_entity(country, key, dataset, resolution, history)

    url = f"{BASE}{dataset}/{resolution}"
    attempts: list[tuple[str, str]] = [
        ("entity_code", entity["entity_code"]),
        ("entity", entity["entity_name"]),
    ]
    last_resp, last_result = None, None
    for param_key, param_val in attempts:
        if not param_val:
            continue
        params = {
            param_key: param_val,
            "start_date": _date_arg(start_year, resolution),
            "end_date": _date_arg(end_year, resolution, end=True),
            "api_key": key,
        }
        resp = _HTTP.get(url, params=params, timeout=60, history=history)
        last_resp = resp
        last_result = validate_response(resp, expected_format="json", min_records=0)
        if last_result.ok and isinstance(last_result.data, list) and last_result.data:
            return resp, last_result, entity
        if last_result.status in ("AUTH_FAILED", "RATE_LIMITED",
                                  "INVALID_REQUEST", "ENDPOINT_OR_INDICATOR_NOT_FOUND"):
            return resp, last_result, entity
    return last_resp, last_result, entity


def _bulk_urls() -> list[str]:
    extra = [u.strip() for u in os.getenv("EMBER_BULK_URLS", "").split(",") if u.strip()]
    return extra + list(_BULK_URLS)


def _bulk_demand(
    country: str, start_year: int, end_year: int, history: list[Any] | None = None,
) -> pd.DataFrame | None:
    """Documented bulk fallback: extract the genuine "Demand" series for a
    country from Ember's open long-format CSV (entity_code = ISO-3).

    Returns a (date, value) DataFrame or None. Never manufactures demand from
    "Total generation" — only the "Demand" series row is used.
    """
    rec = get_country_record(country)
    name = rec.country_name if rec else country
    for url in _bulk_urls():
        try:
            resp = _HTTP.get(url, timeout=300, history=history)
        except ConnectorError:
            continue
        if resp.status_code != 200:
            continue
        try:
            raw = pd.read_csv(io.BytesIO(resp.content), low_memory=False)
        except Exception:  # noqa: BLE001
            continue
        entity_col = next((c for c in raw.columns if c.strip().lower() == "entity_code"), None)
        series_col = next((c for c in raw.columns if c.strip().lower() == "series"), None)
        date_col = _find_date_col(list(raw.columns))
        if entity_col is None or series_col is None or date_col is None:
            continue
        sub = raw[raw[entity_col].astype(str).str.strip().str.upper() == country.upper()]
        if sub.empty:
            # fall back to entity name matching (bulk file may use names)
            ent_col2 = next((c for c in raw.columns if c.strip().lower() == "entity"), None)
            if ent_col2 is not None:
                sub = raw[raw[ent_col2].astype(str).str.strip().str.casefold() == name.casefold()]
        if sub.empty:
            continue
        demand = sub[sub[series_col].astype(str).str.strip().str.casefold() == "demand"]
        if demand.empty:
            continue
        value_col = _pick_value_col(list(raw.columns), _DEMAND_VALUE_COLS)
        if value_col is None:
            value_col = _pick_value_col(list(raw.columns), _GEN_VALUE_COLS)
        if value_col is None:
            continue
        out = demand[[date_col, value_col]].rename(columns={value_col: "value"})
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out["value"] = pd.to_numeric(out["value"], errors="coerce")
        out = out.dropna(subset=["date", "value"])
        out = out[(out["date"].dt.year >= start_year) & (out["date"].dt.year <= end_year)]
        out = out.sort_values("date").drop_duplicates(subset=["date"], keep="first")
        if not out.empty:
            return out[["date", "value"]].reset_index(drop=True)
    return None


def discover_ember_series(
    country: str, key: str, start_year: int = 2024, end_year: int = 2024,
    resolution: str = "monthly",
) -> dict[str, Any]:
    """Discover which electricity series Ember actually publishes for a country.

    Queries both the generation and demand datasets (long-format `series`
    column for generation) and reports the distinct series present — e.g.
    Demand, Total generation, Coal, Gas, Net imports. Used to distinguish
    "Ember has generation but no demand" from "Ember has no data at all".

    This is the dataset-discovery step: the connector records exactly what
    exists instead of assuming "no demand records = country unavailable".
    """
    out: dict[str, Any] = {
        "country": country,
        "available_series": [],
        "has_demand": False,
        "has_generation": False,
        "series_by_dataset": {},
        "entity": None,
        "error": "",
    }
    entity: dict[str, str] | None = None
    for dataset in ("electricity-demand", "electricity-generation"):
        history: list[Any] = []
        try:
            resp, result, entity = _request(
                country, start_year, end_year, key, dataset, resolution, history, entity=entity)
        except ConnectorError as exc:
            out["series_by_dataset"][dataset] = f"error:{exc.status}"
            out["error"] = exc.status
            continue
        out["entity"] = entity
        if not result.ok or not isinstance(result.data, list):
            out["series_by_dataset"][dataset] = f"status:{result.status}"
            continue
        df = pd.DataFrame(result.data)
        if _SERIES_COL in df.columns:
            series = sorted({str(s).strip() for s in df[_SERIES_COL].dropna()})
        else:
            # wide dataset (demand): report the metric columns as "series"
            series = sorted(
                c for c in df.columns
                if c not in ("entity", "entity_code", "is_aggregate_entity", "date", "is_aggregate_series")
            )
        out["series_by_dataset"][dataset] = series
        out["available_series"].extend(series)

    out["available_series"] = sorted(set(out["available_series"]))
    lowered = {s.lower() for s in out["available_series"]}
    out["has_demand"] = "demand" in lowered or any("demand" in s for s in lowered)
    gen_markers = ("total generation", "coal", "gas", "hydro", "wind", "solar",
                   "nuclear", "bioenergy", "other renewables", "clean", "net imports",
                   "generation_twh")
    out["has_generation"] = any(m in lowered for m in gen_markers)
    return out


def verify_ember(country: str, feature: str, key: str | None) -> EndpointVerification:
    if not key:
        return EndpointVerification(
            source_id="ember", country=country, feature=feature,
            status=AUTH_FAILED, message=f"{KEY_ENV} not configured",
        )
    dataset = _DATASET_BY_FEATURE.get(feature, "electricity-generation")
    history: list[Any] = []
    try:
        resp, result, _entity = _request(country, 2024, 2024, key, dataset, "monthly", history)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="ember", country=country, feature=feature,
            status=exc.status, message=str(exc), attempts=exc.attempts or history,
        )
    return verification_from_result(result, "ember", country, feature, attempts=history)


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
    history: list[Any] = []
    seen_series: set[str] = set()
    entity: dict[str, str] | None = None
    for ds in datasets:
        try:
            resp, result, entity = _request(
                country, start_year, end_year, key, ds, "monthly", history, entity=entity)
        except ConnectorError as exc:
            return AcquisitionOutcome(
                source_id="ember", country=country, feature=feature,
                status=exc.status, message=str(exc), failure_reason=exc.status,
                attempts=exc.attempts or history,
            )
        last_result = result
        if not result.ok:
            if result.status in ("AUTH_FAILED", "RATE_LIMITED"):
                return outcome_from_result(result, "ember", country, feature, attempts=history)
            continue
        raw = pd.DataFrame(result.data)
        if _SERIES_COL in raw.columns:
            seen_series |= {str(s).strip() for s in raw[_SERIES_COL].dropna()}
        out_df, err = _extract(raw, feature)
        if out_df is not None and not out_df.empty:
            df = out_df
            break
        if err:
            last_result = result
            continue

    if (df is None or df.empty) and feature == "electricity_demand":
        # Documented bulk-dataset fallback: the API may return zero rows (e.g.
        # entity/filter mismatch), but Ember's open long-format CSV still
        # carries the country's genuine "Demand" series.
        bulk = _bulk_demand(country, start_year, end_year, history)
        if bulk is not None and not bulk.empty:
            df = bulk
            entity = entity or {"entity_code": country, "resolution_method": "bulk_fallback"}

    if df is None or df.empty:
        # Dataset discovery: report what Ember ACTUALLY has for this country
        # instead of a blanket NO_RECORDS. Never manufacture demand from
        # generation.
        discovery = discover_ember_series(country, key, start_year, end_year)
        available = discovery["available_series"] or sorted(seen_series)
        avail_note = ", ".join(available) if available else "none reported"
        msg = (
            f"Ember does not expose a '{feature}' series for {country} "
            f"({start_year}-{end_year}). Available series: {avail_note}. "
            f"(demand={discovery['has_demand']}, generation={discovery['has_generation']})"
        )
        return AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status="NO_DATA",
            message=msg, failure_reason="NO_DATA",
            attempts=history,
            http_status=last_result.http_status if last_result else None,
            response_type=last_result.content_type if last_result else "",
            verification_notes=[
                f"Ember dataset discovery: available series [{avail_note}]",
                "generation is never used to fabricate demand",
            ],
            provenance={"available_series": available, "has_demand": discovery["has_demand"],
                        "has_generation": discovery["has_generation"],
                        "series_by_dataset": discovery.get("series_by_dataset", {}),
                        "resolved_entity": discovery.get("entity") or entity},
        )

    out_df = out_df.rename(columns={out_df.columns[0]: "date"})
    out_path = out_dir / "raw" / "electricity" / "demand" / "ember" / f"{country}.csv"
    if feature != "electricity_demand":
        out_path = out_dir / "raw" / "electricity" / feature / "ember" / f"{country}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)

    unit = "TWh" if feature != "renewable_generation_share" else "%"
    notes = [f"dataset {dataset}", "JSON valid", f"columns {list(out_df.columns)}"]
    if entity:
        notes.append(f"resolved entity {entity.get('entity_code')} ({entity.get('resolution_method')})")
    if seen_series:
        notes.append(f"Ember series discovered: {', '.join(sorted(seen_series))}")
    return AcquisitionOutcome(
        source_id="ember", country=country, feature=feature,
        status="SUCCESS", message=f"{len(out_df)} Ember monthly records for {feature}",
        records=len(out_df), path=str(out_path), frequency="monthly", unit=unit,
        requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
        received_start=str(out_df['date'].min())[:7] if len(out_df) else "",
        received_end=str(out_df['date'].max())[:7] if len(out_df) else "",
        schema_columns=list(out_df.columns),
        verification_notes=notes,
        provenance={"endpoint": build_ember_url(dataset, "monthly", country, start_year, end_year),
                    "available_series": sorted(seen_series),
                    "resolved_entity": entity},
        attempts=history,
    )


def diagnose_ember(
    country: str, feature: str, start_year: int, end_year: int, key: str | None,
) -> dict[str, Any]:
    """Diagnostic for `test-source ember`: exact endpoint, resolved entity,
    request parameters (key excluded), HTTP status, schema, record count,
    first/last record — without printing the key."""
    dataset = _DATASET_BY_FEATURE.get(feature, "electricity-generation")
    resolution = "monthly"
    history: list[Any] = []
    diag: dict[str, Any] = {
        "source": "ember",
        "country": country,
        "feature": feature,
        "dataset": dataset,
        "resolution": resolution,
        "endpoint": f"{BASE}{dataset}/{resolution}",
        "auth_supplied": bool(key),
        "resolved_entity": None,
        "request_params": {},
        "http_status": None,
        "response_schema": [],
        "records": 0,
        "first_record": None,
        "last_record": None,
        "failure_reason": "",
    }
    if not key:
        diag["failure_reason"] = "AUTH_FAILED: EMBER_API_KEY not configured"
        return diag

    entity = resolve_entity(country, key, dataset, resolution, history)
    diag["resolved_entity"] = entity
    diag["request_params"] = {
        "entity_code": entity["entity_code"],
        "start_date": _date_arg(start_year, resolution),
        "end_date": _date_arg(end_year, resolution, end=True),
        "api_key": "<redacted>",
    }
    try:
        resp, result, entity = _request(
            country, start_year, end_year, key, dataset, resolution, history, entity=entity)
    except ConnectorError as exc:
        diag["http_status"] = None
        diag["failure_reason"] = f"{exc.status}: {exc}"
        diag["attempts"] = exc.attempts or history
        return diag

    diag["http_status"] = result.http_status
    diag["response_type"] = result.content_type
    if not result.ok:
        diag["failure_reason"] = f"{result.status}: {result.message}"
        return diag
    rows = result.data if isinstance(result.data, list) else []
    diag["records"] = len(rows)
    if rows:
        diag["response_schema"] = sorted(rows[0].keys())
        diag["first_record"] = rows[0]
        diag["last_record"] = rows[-1]
    out_df, err = _extract(pd.DataFrame(rows), feature)
    diag["country_level_records"] = 0 if out_df is None else len(out_df)
    diag["output_path"] = ""
    if out_df is not None and not out_df.empty:
        out_path = Path("hgt_qf_data") / "raw" / "electricity" / "ember" / f"{country}_{feature}.csv"
        diag["output_path"] = str(out_path)
    if err:
        diag["failure_reason"] = f"SCHEMA_MISMATCH: {err}"
    return diag


def ember_connector(
    country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path,
    **_: Any,
) -> tuple[EndpointVerification, AcquisitionOutcome]:
    key = get_credential(credentials, KEY_ENV)
    verification = verify_ember(country, feature, key)
    if verification.status != "VERIFIED":
        status = acquisition_status_for_verification(verification.status)
        return verification, AcquisitionOutcome(
            source_id="ember", country=country, feature=feature, status=status,
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_ember(country, feature, start, end, key, out_dir)
    return verification, outcome


__all__ = [
    "BASE", "KEY_ENV", "build_ember_url", "resolve_entity", "discover_ember_series",
    "verify_ember", "acquire_ember", "ember_connector", "diagnose_ember",
    "_entity_candidates", "_extract", "_request",
]
