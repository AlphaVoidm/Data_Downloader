"""Component 6 — Scientific Geospatial Data Extractor (ERA5 / CMIP6).

Implements the plan's "download -> extract -> discard the huge source file" rule.

Two extraction modes:

    MODE A  RAW_SUBSET        request the country bounding box / grid subset and
                              store it compactly (still grid data, but bounded).
    MODE B  COUNTRY_AGGREGATE (default) request, immediately reduce to a
                              country-level monthly series, store CSV/Parquet,
                              and DELETE the temporary bulk NetCDF.

The module degrades gracefully: without CDS credentials or the optional
dependencies (cdsapi, xarray) it returns an explicit status instead of crashing,
so the acquisition engine can record a proper provenance reason.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from country_registry import get_country_bbox

# ERA5 monthly single-level product & the variables HGT-QF consumes.
ERA5_DATASET = "reanalysis-era5-single-levels-monthly-means"
CMIP6_DATASET = "projections-cmip6"

# concept -> CDS variable + aggregation + unit handling
ERA5_VARIABLES: dict[str, dict[str, Any]] = {
    "temperature": {
        "cds_name": "2m_temperature",
        "output_col": "temperature_c",
        "aggregation": "mean",
        "convert": lambda x: x - 273.15,  # K -> °C
        "unit": "°C",
    },
    "precipitation": {
        "cds_name": "total_precipitation",
        "output_col": "precipitation_mm",
        "aggregation": "sum",
        "convert": lambda x: x * 1000.0,  # m -> mm
        "unit": "mm",
    },
    "wind_speed": {
        "cds_name": "10m_wind_speed",
        "output_col": "wind_speed_m_s",
        "aggregation": "mean",
        "convert": lambda x: x,
        "unit": "m/s",
    },
    "solar_radiation": {
        "cds_name": "surface_solar_radiation_downwards",
        "output_col": "solar_radiation_w_m2",
        "aggregation": "mean",
        "convert": lambda x: x,
        "unit": "W/m²",
    },
}

MODE_RAW_SUBSET = "RAW_SUBSET"
MODE_COUNTRY_AGGREGATE = "COUNTRY_AGGREGATE"


@dataclass
class ExtractionResult:
    country: str
    source: str
    status: str  # SUCCESS | PARTIAL_SUCCESS | ACCESS_REQUIRES_AUTH | DEPENDENCY_MISSING | PERIOD_NOT_AVAILABLE | DOWNLOAD_ERROR | PARSE_ERROR
    message: str
    records: int = 0
    output_path: str = ""
    variables: list[str] = field(default_factory=list)
    mode: str = MODE_COUNTRY_AGGREGATE
    temp_file_deleted: bool = False
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _cds_credentials_available(credentials: dict[str, str] | None) -> bool:
    if credentials and credentials.get("CDS_API_KEY"):
        return True
    if os.getenv("CDS_API_KEY") or os.getenv("CDSAPI_KEY"):
        return True
    # A ~/.cdsapirc file also satisfies cdsapi.
    rc = Path.home() / ".cdsapirc"
    return rc.exists()


def _make_cds_client(credentials: dict[str, str] | None):
    """Construct a cdsapi.Client, preferring an explicit key/url."""
    try:
        import cdsapi  # type: ignore
    except ImportError as exc:
        raise RuntimeError("DEPENDENCY_MISSING: cdsapi is not installed") from exc

    key = (credentials or {}).get("CDS_API_KEY") or os.getenv("CDS_API_KEY") or os.getenv("CDSAPI_KEY")
    url = (credentials or {}).get("CDS_API_URL") or os.getenv("CDSAPI_URL")
    if key:
        try:
            return cdsapi.Client(url=url or "https://cds.climate.copernicus.eu/api", key=key)
        except TypeError:
            # Older cdsapi without url/key kwargs; rely on ~/.cdsapirc
            return cdsapi.Client()
    return cdsapi.Client()


def _area_weights(latitudes: np.ndarray) -> np.ndarray:
    """Cos(latitude) area weights for a regular lat/lon grid."""
    return np.cos(np.deg2rad(latitudes)).clip(min=0.0)


def aggregate_grid_to_series(
    ds: Any,
    variable_map: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    """Reduce an xarray Dataset (dims: time, latitude, longitude) to a monthly
    country/region-level series using cos(latitude) area weighting.

    `ds` is an xarray.Dataset whose data variables use the CDS variable names.
    """
    import xarray as xr  # type: ignore

    lat = np.asarray(ds["latitude"].values, dtype=float)
    w = _area_weights(lat)[:, None, None]  # (lat, 1, 1)

    series: dict[str, np.ndarray] = {}
    for concept, spec in variable_map.items():
        cds_name = spec["cds_name"]
        if cds_name not in ds:
            continue
        arr = ds[cds_name].values  # (time, lat, lon)
        finite = np.isfinite(arr)
        num = np.nansum(np.where(finite, arr, 0.0) * w, axis=(1, 2))
        den = np.nansum(np.where(finite, 1.0, 0.0) * w, axis=(1, 2))
        mean = num / np.where(den == 0, np.nan, den)
        series[spec["output_col"]] = spec["convert"](mean)

    time_values = ds["time"].values
    df = pd.DataFrame(series, index=pd.DatetimeIndex(time_values).to_period("M").to_timestamp("M"))
    df.index.name = "date"
    return df


def _write_compact(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix == ".parquet":
        try:
            df.to_parquet(output_path)
            return
        except ImportError:
            pass
    df.to_csv(output_path.with_suffix(".csv"), index=True)


def extract_era5_monthly_country(
    country_iso3: str,
    variables: list[str] | None = None,
    start_year: int = 2000,
    end_year: int = 2024,
    mode: str = MODE_COUNTRY_AGGREGATE,
    output_dir: Path | str | None = None,
    credentials: dict[str, str] | None = None,
    dataset: str = ERA5_DATASET,
    extra_request: dict[str, Any] | None = None,
    keep_temp: bool = False,
) -> ExtractionResult:
    """Retrieve a minimal ERA5 monthly subset for a country and reduce it.

    The temporary bulk NetCDF is deleted after extraction unless keep_temp=True.
    """
    country_iso3 = country_iso3.strip().upper()
    bbox = get_country_bbox(country_iso3)
    if bbox is None:
        return ExtractionResult(
            country=country_iso3, source=dataset, status="PERIOD_NOT_AVAILABLE",
            message=f"No bounding box registered for {country_iso3}",
            mode=mode,
        )
    north, west, south, east = bbox

    variables = variables or list(ERA5_VARIABLES.keys())
    variable_map = {v: ERA5_VARIABLES[v] for v in variables if v in ERA5_VARIABLES}
    if not variable_map:
        return ExtractionResult(
            country=country_iso3, source=dataset, status="PARSE_ERROR",
            message=f"No supported variables among {variables}", mode=mode,
        )

    if not _cds_credentials_available(credentials):
        return ExtractionResult(
            country=country_iso3, source=dataset, status="ACCESS_REQUIRES_AUTH",
            message="CDS credentials required (CDS_API_KEY / CDSAPI_KEY or ~/.cdsapirc)",
            mode=mode, variables=list(variable_map),
        )

    try:
        client = _make_cds_client(credentials)
    except RuntimeError as exc:
        return ExtractionResult(
            country=country_iso3, source=dataset, status="DEPENDENCY_MISSING",
            message=str(exc), mode=mode, variables=list(variable_map),
        )

    years = [str(y) for y in range(start_year, end_year + 1)]
    months = [f"{m:02d}" for m in range(1, 13)]

    request: dict[str, Any] = {
        "product_type": "monthly_averaged_reanalysis",
        "variable": [spec["cds_name"] for spec in variable_map.values()],
        "year": years,
        "month": months,
        "time": "00:00",
        "area": [north, west, south, east],
        "format": "netcdf",
    }
    if extra_request:
        request.update(extra_request)

    # Download to a temporary directory (outside the data tree).
    tmp_dir = Path(tempfile.mkdtemp(prefix="hgtqf_era5_"))
    tmp_file = tmp_dir / f"{country_iso3}_{dataset.split('-')[0]}.nc"
    try:
        client.retrieve(dataset, request, str(tmp_file))

        if mode == MODE_RAW_SUBSET:
            # MODE A: keep the (bounded) subset as compact raw output.
            if output_dir is None:
                raise ValueError("output_dir is required for RAW_SUBSET mode")
            out_path = Path(output_dir) / "climate" / f"{country_iso3}_raw_subset.nc"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(tmp_file, out_path)
            result = ExtractionResult(
                country=country_iso3, source=dataset, status="SUCCESS",
                message=f"Raw subset retained at {out_path.name} ({out_path.stat().st_size} bytes)",
                output_path=str(out_path), mode=mode, variables=list(variable_map),
                temp_file_deleted=False, records=0,
            )
        else:
            # MODE B: reduce to country-level series, then delete the bulk file.
            import xarray as xr  # type: ignore
            ds = xr.open_dataset(tmp_file)
            df = aggregate_grid_to_series(ds, variable_map)
            ds.close()

            if output_dir is None:
                raise ValueError("output_dir is required for COUNTRY_AGGREGATE mode")
            out_path = Path(output_dir) / "climate" / f"{country_iso3}.parquet"
            _write_compact(df, out_path)
            written = out_path if out_path.exists() else out_path.with_suffix(".csv")

            result = ExtractionResult(
                country=country_iso3, source=dataset, status="SUCCESS",
                message=f"{len(df)} monthly records reduced to {written.name}",
                output_path=str(written), mode=mode, variables=list(variable_map),
                records=len(df), temp_file_deleted=False,
            )
    except ImportError as exc:
        result = ExtractionResult(
            country=country_iso3, source=dataset, status="DEPENDENCY_MISSING",
            message=f"Optional dependency missing: {exc}", mode=mode, variables=list(variable_map),
        )
    except Exception as exc:
        result = ExtractionResult(
            country=country_iso3, source=dataset, status="DOWNLOAD_ERROR",
            message=f"CDS retrieval/extraction failed: {str(exc)[:200]}", mode=mode,
            variables=list(variable_map),
        )

    # Always discard the temporary bulk object unless explicitly kept.
    if tmp_file.exists() and not keep_temp:
        try:
            tmp_file.unlink()
            result.temp_file_deleted = True
        except OSError:
            pass
    try:
        tmp_dir.rmdir()
    except OSError:
        pass
    return result


def _copy_file(src: Path, dst: Path) -> None:
    import shutil
    shutil.copy2(src, dst)


__all__ = [
    "ExtractionResult", "ERA5_VARIABLES", "ERA5_DATASET", "CMIP6_DATASET",
    "MODE_RAW_SUBSET", "MODE_COUNTRY_AGGREGATE",
    "extract_era5_monthly_country", "aggregate_grid_to_series", "_area_weights",
]
