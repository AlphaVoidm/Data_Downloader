"""Backward-compatible shim for the legacy scientific extractor.

The real implementation now lives in:

    spatial/          country geometry, bounding boxes, raster aggregation
    extraction/       CDS chunked retrieval + temporal aggregation
    validation/       coverage / completeness / units checks

This module re-exports the public names so legacy imports keep working.
CMIP6 remains a FUTURE-SCENARIO source only and is never part of the
historical acquisition path.
"""
from __future__ import annotations

from extraction.cds_extractor import (
    AUTH_FAILED as ACCESS_REQUIRES_AUTH_ALIAS,
    ERA5_MONTHLY_DATASET as ERA5_DATASET,
    NETWORK_ERROR as DOWNLOAD_ERROR_ALIAS,
    classify_cds_error,
    extract_monthly_chunked,
)
from spatial.bbox import BBox, bbox_from_iso3
from spatial.raster_aggregate import aggregate_grid_to_series, area_weights as _area_weights

CMIP6_DATASET = "projections-cmip6"
MODE_RAW_SUBSET = "RAW_SUBSET"
MODE_COUNTRY_AGGREGATE = "COUNTRY_AGGREGATE"

ERA5_VARIABLES = {
    "temperature": {"cds_name": "2m_temperature", "output_col": "temperature_c",
                    "aggregation": "mean", "convert": lambda x: x - 273.15, "unit": "°C"},
    "precipitation": {"cds_name": "total_precipitation", "output_col": "precipitation_mm",
                      "aggregation": "sum", "convert": lambda x: x * 1000.0, "unit": "mm"},
    "wind_speed": {"cds_name": "10m_wind_speed", "output_col": "wind_speed_m_s",
                   "aggregation": "mean", "convert": lambda x: x, "unit": "m/s"},
    "solar_radiation": {"cds_name": "surface_solar_radiation_downwards",
                        "output_col": "solar_radiation_w_m2", "aggregation": "mean",
                        "convert": lambda x: x, "unit": "W/m²"},
}


def extract_era5_monthly_country(
    country_iso3: str, variables=None, start_year: int = 2000, end_year: int = 2024,
    mode: str = MODE_COUNTRY_AGGREGATE, output_dir=None, credentials=None,
    dataset: str = ERA5_DATASET, extra_request=None, keep_temp: bool = False,
):
    """Legacy entry point — delegates to the chunked extraction engine.

    MODE_B (COUNTRY_AGGREGATE) returns a country-level monthly DataFrame saved
    to `output_dir/climate/{ISO3}.parquet` (CSV fallback). MODE_A (RAW_SUBSET)
    is not supported by the chunked engine and returns an explicit error.
    """
    from dataclasses import dataclass, field
    from datetime import datetime, timezone

    @dataclass
    class ExtractionResult:
        country: str
        source: str
        status: str
        message: str
        records: int = 0
        output_path: str = ""
        variables: list = field(default_factory=list)
        mode: str = MODE_COUNTRY_AGGREGATE
        temp_file_deleted: bool = False
        retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

        def to_dict(self):
            return self.__dict__.copy()

    if mode == MODE_RAW_SUBSET:
        return ExtractionResult(
            country=country_iso3, source=dataset, status="DOWNLOAD_ERROR",
            message="RAW_SUBSET mode unsupported by the chunked extractor; use COUNTRY_AGGREGATE",
            mode=mode,
        )

    bbox = bbox_from_iso3(country_iso3)
    if bbox is None:
        return ExtractionResult(
            country=country_iso3, source=dataset, status="PERIOD_NOT_AVAILABLE",
            message=f"No bounding box registered for {country_iso3}", mode=mode,
        )

    variables = variables or list(ERA5_VARIABLES.keys())
    variable_map = {v: ERA5_VARIABLES[v] for v in variables if v in ERA5_VARIABLES}
    if not variable_map:
        return ExtractionResult(
            country=country_iso3, source=dataset, status="PARSE_ERROR",
            message=f"No supported variables among {variables}", mode=mode,
        )

    try:
        df, notes = extract_monthly_chunked(
            dataset=dataset, bbox=bbox, variables=variable_map,
            start_year=start_year, end_year=end_year, credentials=credentials,
            keep_temp=keep_temp,
        )
    except RuntimeError as exc:
        return ExtractionResult(
            country=country_iso3, source=dataset, status="DEPENDENCY_MISSING",
            message=str(exc), mode=mode, variables=list(variable_map),
        )
    except Exception as exc:  # noqa: BLE001
        status, reason = classify_cds_error(exc)
        mapped = {"AUTH_FAILED": "ACCESS_REQUIRES_AUTH",
                  "NETWORK_ERROR": "DOWNLOAD_ERROR"}.get(status, "DOWNLOAD_ERROR")
        return ExtractionResult(
            country=country_iso3, source=dataset, status=mapped,
            message=f"{reason}: {str(exc)[:160]}", mode=mode, variables=list(variable_map),
        )

    if output_dir is not None:
        import pandas as pd
        out_path = Path(output_dir) / "climate" / f"{country_iso3}.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.to_parquet(out_path)
        except ImportError:
            out_path = out_path.with_suffix(".csv")
            df.to_csv(out_path, index=True)
        return ExtractionResult(
            country=country_iso3, source=dataset, status="SUCCESS",
            message=f"{len(df)} monthly records reduced to {out_path.name}",
            output_path=str(out_path), mode=mode, variables=list(variable_map),
            records=len(df), temp_file_deleted=True,
        )
    return ExtractionResult(
        country=country_iso3, source=dataset, status="SUCCESS",
        message=f"{len(df)} monthly records reduced in memory",
        mode=mode, variables=list(variable_map), records=len(df),
        temp_file_deleted=True,
    )


__all__ = [
    "ExtractionResult", "ERA5_VARIABLES", "ERA5_DATASET", "CMIP6_DATASET",
    "MODE_RAW_SUBSET", "MODE_COUNTRY_AGGREGATE",
    "extract_era5_monthly_country", "aggregate_grid_to_series", "_area_weights",
]
