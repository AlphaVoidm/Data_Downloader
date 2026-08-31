"""NASA POWER daily climate connector (global, no key).

Fetches daily T2M / solar / wind / precipitation at the country centroid and
aggregates to a compact monthly country-level table (data extraction), including
CDD/HDD derived from daily temperature (base 18°C). No interpolation, no imputation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from country_registry import get_country_record
from response_validator import validate_response

from .base import (
    EndpointVerification,
    AcquisitionOutcome,
    ConnectorError,
    _HTTP,
    outcome_from_result,
    verification_from_result,
)

ENDPOINT = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMS = "T2M,ALLSKY_SFC_SW_DWN,WS10M,PRECTOTCORR"
SENTINEL = -999.0
CLIMATE_FEATURES = {"temperature_2m", "solar_radiation", "wind_speed_10m", "precipitation", "cooling_degree_days", "heating_degree_days"}


def _climate_path(out_dir: Path, country: str) -> Path:
    return out_dir / "climate" / f"{country}_nasa_power.csv"


def verify_nasa(country: str) -> EndpointVerification:
    rec = get_country_record(country)
    if rec is None:
        return EndpointVerification(
            source_id="nasa_power", country=country, feature="climate",
            status="MAPPING_REQUIRED", message=f"No centroid registered for {country}",
        )
    params = {
        "parameters": "T2M", "community": "RE",
        "longitude": rec.centroid_lon, "latitude": rec.centroid_lat,
        "start": "20240101", "end": "20240102", "format": "JSON",
    }
    try:
        resp = _HTTP.get(ENDPOINT, params=params, timeout=60)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="nasa_power", country=country, feature="climate", status=str(exc), message=str(exc),
        )
    result = validate_response(resp, expected_format="json", min_records=1)
    return verification_from_result(result, "nasa_power", country, "climate")


def _fetch_daily(country: str, start_year: int, end_year: int) -> pd.DataFrame:
    rec = get_country_record(country)
    if rec is None:
        raise ValueError(f"No centroid for {country}")
    rows: list[dict[str, Any]] = []
    for chunk_start in range(start_year, end_year + 1, 5):
        chunk_end = min(chunk_start + 4, end_year)
        params = {
            "parameters": PARAMS, "community": "RE",
            "longitude": rec.centroid_lon, "latitude": rec.centroid_lat,
            "start": f"{chunk_start}0101", "end": f"{chunk_end}1231", "format": "JSON",
        }
        resp = _HTTP.get(ENDPOINT, params=params, timeout=120)
        result = validate_response(resp, expected_format="json", min_records=0)
        if not result.ok:
            raise RuntimeError(result.message)
        parameter = resp.json().get("properties", {}).get("parameter", {})
        dates = sorted(set().union(*(v.keys() for v in parameter.values())))
        for d in dates:
            rows.append({
                "date": pd.to_datetime(d, format="%Y%m%d"),
                "t2m": parameter.get("T2M", {}).get(d),
                "solar": parameter.get("ALLSKY_SFC_SW_DWN", {}).get(d),
                "wind": parameter.get("WS10M", {}).get(d),
                "precip": parameter.get("PRECTOTCORR", {}).get(d),
            })
    df = pd.DataFrame(rows)
    for col in ("t2m", "solar", "wind", "precip"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df.loc[df[col] == SENTINEL, col] = pd.NA
    return df


def _aggregate_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()
    daily["month"] = daily["date"].dt.to_period("M").dt.to_timestamp()
    cdd = (daily["t2m"] - 18.0).clip(lower=0.0)
    hdd = (18.0 - daily["t2m"]).clip(lower=0.0)
    daily["cdd_daily"] = cdd
    daily["hdd_daily"] = hdd
    monthly = daily.groupby("month").agg(
        temperature_2m=("t2m", "mean"),
        solar_radiation=("solar", "mean"),
        wind_speed_10m=("wind", "mean"),
        precipitation=("precip", "sum"),
        cooling_degree_days=("cdd_daily", "sum"),
        heating_degree_days=("hdd_daily", "sum"),
    ).reset_index().rename(columns={"month": "date"})
    monthly = monthly.round(3)
    return monthly


def acquire_nasa(country: str, feature: str, start_year: int, end_year: int, out_dir: Path) -> AcquisitionOutcome:
    out_path = _climate_path(out_dir, country)
    if out_path.exists():
        existing = pd.read_csv(out_path)
        return AcquisitionOutcome(
            source_id="nasa_power", country=country, feature=feature,
            status="SUCCESS", message=f"Climate table already extracted ({len(existing)} months)",
            records=len(existing), path=str(out_path), frequency="monthly",
            unit="see columns", requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
            received_start=str(existing['date'].min())[:7], received_end=str(existing['date'].max())[:7],
            schema_columns=list(existing.columns),
            verification_notes=["cached climate extraction"],
            provenance={"source": "NASA POWER", "params": PARAMS},
        )
    try:
        daily = _fetch_daily(country, start_year, end_year)
    except Exception as exc:  # noqa: BLE001
        return AcquisitionOutcome(
            source_id="nasa_power", country=country, feature=feature,
            status="DOWNLOAD_ERROR", message=str(exc)[:200], failure_reason="NETWORK_ERROR",
        )
    if daily.empty:
        return AcquisitionOutcome(
            source_id="nasa_power", country=country, feature=feature,
            status="NO_RECORDS", message="NASA POWER returned no observations", failure_reason="NO_RECORDS",
        )
    monthly = _aggregate_monthly(daily)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(out_path, index=False)
    return AcquisitionOutcome(
        source_id="nasa_power", country=country, feature=feature,
        status="SUCCESS", message=f"{len(monthly)} monthly climate observations (from daily)",
        records=len(monthly), path=str(out_path), frequency="monthly",
        unit="°C / kWh/m²/day / m/s / mm / degree-days",
        requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
        received_start=str(monthly['date'].min())[:7], received_end=str(monthly['date'].max())[:7],
        schema_columns=list(monthly.columns),
        verification_notes=["JSON valid", "daily->monthly aggregation", "CDD/HDD from daily T2M (base 18°C)"],
        provenance={"source": "NASA POWER", "params": PARAMS, "spatial": "country centroid"},
    )


def nasa_power_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    verification = verify_nasa(country)
    if verification.status != "VERIFIED":
        return verification, AcquisitionOutcome(
            source_id="nasa_power", country=country, feature=feature,
            status=verification.status if verification.status in ("RATE_LIMITED", "NETWORK_ERROR", "TIMEOUT", "MAPPING_REQUIRED") else "NOT_VERIFIED",
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_nasa(country, feature, start, end, out_dir)
    return verification, outcome
