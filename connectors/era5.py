"""ERA5 / CDS climate connector (targeted extraction).

Downloads ONLY the country bounding box + required variables + required period
from the monthly-means product, area-aggregates to a country-level monthly table,
writes compact Parquet/CSV, and deletes the temporary NetCDF (spec §9, §19).

Credential loaded from CDS_API_KEY (or CDSAPI_KEY / ~/.cdsapirc); never printed.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from country_registry import get_country_bbox

from .base import (
    AUTH_FAILED,
    EndpointVerification,
    AcquisitionOutcome,
    acquisition_status_for_verification,
)

ERA5_DATASET = "reanalysis-era5-single-levels-monthly-means"

# concept -> (CDS variable, output column, unit conversion)
ERA5_VARIABLES: dict[str, dict[str, Any]] = {
    "temperature_2m": {"cds": "2m_temperature", "col": "temperature_2m", "convert": lambda x: x - 273.15, "unit": "°C"},
    "precipitation": {"cds": "total_precipitation", "col": "precipitation", "convert": lambda x: x * 1000.0, "unit": "mm"},
    "wind_speed_10m": {"cds": "10m_wind_speed", "col": "wind_speed_10m", "convert": lambda x: x, "unit": "m/s"},
    "solar_radiation": {"cds": "surface_solar_radiation_downwards", "col": "solar_radiation", "convert": lambda x: x * 0.024, "unit": "kWh/m²/day"},
}


def _creds_available(credentials: dict[str, str] | None) -> bool:
    if credentials and credentials.get("CDS_API_KEY"):
        return True
    if os.getenv("CDS_API_KEY") or os.getenv("CDSAPI_KEY"):
        return True
    return (Path.home() / ".cdsapirc").exists()


def _make_client(credentials: dict[str, str] | None):
    import cdsapi  # type: ignore
    key = (credentials or {}).get("CDS_API_KEY") or os.getenv("CDS_API_KEY") or os.getenv("CDSAPI_KEY")
    url = (credentials or {}).get("CDS_API_URL") or os.getenv("CDSAPI_URL")
    if key:
        try:
            return cdsapi.Client(url=url or "https://cds.climate.copernicus.eu/api", key=key)
        except TypeError:
            return cdsapi.Client()
    return cdsapi.Client()


def _classify_cds_error(exc: Exception) -> tuple[str, str]:
    """Map a cdsapi/requests exception onto the granular failure vocabulary."""
    text = str(exc)
    low = text.lower()
    if any(k in low for k in ("401", "403", "authentication", "forbidden", "invalid api key",
                               "the request you have submitted is not valid", "credentials")):
        return AUTH_FAILED, "CDS authentication/authorization failed"
    if any(k in low for k in ("timed out", "timeout", "connecttimeout", "readtimeout")):
        return "TIMEOUT", "CDS request timed out"
    return "NETWORK_ERROR", "CDS retrieval failed (network/connection error)"


def _area_weights(lat: np.ndarray) -> np.ndarray:
    return np.cos(np.deg2rad(lat)).clip(min=0.0)


def _climate_path(out_dir: Path, country: str) -> Path:
    return out_dir / "climate" / f"{country}_era5.parquet"


def verify_era5(country: str, credentials: dict[str, str] | None) -> EndpointVerification:
    if not _creds_available(credentials):
        return EndpointVerification(
            source_id="era5", country=country, feature="climate",
            status=AUTH_FAILED, message="CDS credentials required (CDS_API_KEY / CDSAPI_KEY or ~/.cdsapirc)",
        )
    try:
        import cdsapi  # noqa: F401
        import xarray  # noqa: F401
    except ImportError as exc:
        return EndpointVerification(
            source_id="era5", country=country, feature="climate",
            status="DEPENDENCY_MISSING", message=f"Optional dependency missing: {exc.name}",
        )
    bbox = get_country_bbox(country)
    if bbox is None:
        return EndpointVerification(
            source_id="era5", country=country, feature="climate",
            status="MAPPING_REQUIRED", message=f"No bounding box registered for {country}",
        )
    return EndpointVerification(
        source_id="era5", country=country, feature="climate",
        status="VERIFIED", message="CDS credentials + dependencies present (full retrieval verified at download time)",
    )


def acquire_era5(country: str, feature: str, start_year: int, end_year: int, credentials: dict[str, str] | None, out_dir: Path) -> AcquisitionOutcome:
    if feature in ("cooling_degree_days", "heating_degree_days"):
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="NOT_VERIFIED",
            message="CDD/HDD require DAILY temperature; use NASA POWER (ERA5 monthly means cannot derive degree-days)",
            failure_reason="SCHEMA_MISMATCH",
        )
    if not _creds_available(credentials):
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status=AUTH_FAILED,
            message="CDS credentials required", failure_reason="AUTH_FAILED",
        )
    bbox = get_country_bbox(country)
    if bbox is None:
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="NOT_VERIFIED",
            message=f"No bounding box registered for {country}", failure_reason="MAPPING_REQUIRED",
        )
    out_path = _climate_path(out_dir, country)
    if out_path.exists() or out_path.with_suffix(".csv").exists():
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="SUCCESS",
            message="ERA5 climate table already extracted", path=str(out_path), frequency="monthly",
            verification_notes=["cached extraction"],
        )

    try:
        client = _make_client(credentials)
        import xarray as xr  # type: ignore
    except ImportError as exc:
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="DEPENDENCY_MISSING",
            message=f"Optional dependency missing: {getattr(exc, 'name', exc)}", failure_reason="DEPENDENCY_MISSING",
        )

    north, west, south, east = bbox
    request = {
        "product_type": "monthly_averaged_reanalysis",
        "variable": [spec["cds"] for spec in ERA5_VARIABLES.values()],
        "year": [str(y) for y in range(start_year, end_year + 1)],
        "month": [f"{m:02d}" for m in range(1, 13)],
        "time": "00:00",
        "area": [north, west, south, east],
        "format": "netcdf",
    }

    tmp_dir = Path(tempfile.mkdtemp(prefix="hgtqf_era5_"))
    tmp_file = tmp_dir / f"{country}.nc"
    try:
        client.retrieve(ERA5_DATASET, request, str(tmp_file))
        ds = xr.open_dataset(tmp_file)
        time_values = pd.DatetimeIndex(ds["time"].values)
        lat = np.asarray(ds["latitude"].values, dtype=float)
        w = _area_weights(lat)[:, None, None]
        series: dict[str, np.ndarray] = {}
        for concept, spec in ERA5_VARIABLES.items():
            if spec["cds"] not in ds:
                continue
            arr = ds[spec["cds"]].values
            finite = np.isfinite(arr)
            num = np.nansum(np.where(finite, arr, 0.0) * w, axis=(1, 2))
            den = np.nansum(np.where(finite, 1.0, 0.0) * w, axis=(1, 2))
            series[spec["col"]] = spec["convert"](num / np.where(den == 0, np.nan, den))
        ds.close()
        df = pd.DataFrame(series, index=time_values.to_period("M").to_timestamp("M"))
        df.index.name = "date"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.to_parquet(out_path)
        except ImportError:
            out_path = out_path.with_suffix(".csv")
            df.to_csv(out_path, index=True)
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="SUCCESS",
            message=f"{len(df)} monthly country-level climate records (ERA5, area-weighted)",
            records=len(df), path=str(out_path), frequency="monthly",
            unit="see columns", requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
            received_start=str(df.index.min())[:7], received_end=str(df.index.max())[:7],
            schema_columns=list(df.columns),
            verification_notes=["NetCDF valid", "area-weighted country aggregate", "temp NetCDF deleted"],
            provenance={"dataset": ERA5_DATASET, "area": [north, west, south, east]},
        )
    except Exception as exc:  # noqa: BLE001
        status, reason = _classify_cds_error(exc)
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status=status,
            message=f"{reason}: {str(exc)[:160]}", failure_reason=status,
        )
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


def era5_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    verification = verify_era5(country, credentials)
    if verification.status != "VERIFIED":
        status = acquisition_status_for_verification(verification.status)
        return verification, AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status=status,
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_era5(country, feature, start, end, credentials, out_dir)
    return verification, outcome
