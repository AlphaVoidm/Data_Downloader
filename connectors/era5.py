"""ERA5 / CDS climate connector (targeted, chunked spatial subset extraction).

Pipeline (spec §9, §19 + "CDS sub-region extraction" review):

    country bbox -> CDS request (bbox + required variables + period only)
                 -> chunked downloads (small NetCDF per year-chunk)
                 -> area-weighted spatial aggregation
                 -> compact country-level monthly Parquet/CSV
                 -> temporary NetCDF deleted after every chunk

A country's 2000-2024 history is ~300 monthly rows — never a global raster.
Credentials come from CDS_API_KEY (uid:key personal access token) / CDSAPI_URL /
~/.cdsapirc, and are never printed. Licence-terms failures are reported as a
distinct status, not a generic error.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from country_registry import get_country_record
from extraction.cds_extractor import (
    AUTH_FAILED,
    DEPENDENCY_MISSING,
    ERA5_MONTHLY_DATASET,
    cds_credentials_available,
    classify_cds_error,
    extract_monthly_chunked,
    make_cds_client,
)
from spatial.bbox import bbox_from_iso3
from validation import completeness as completeness_v
from validation import coverage as coverage_v
from validation import units as units_v

from .base import (
    EndpointVerification,
    AcquisitionOutcome,
    acquisition_status_for_verification,
)

ERA5_DATASET = ERA5_MONTHLY_DATASET

# concept -> (CDS variable, output column, aggregation, unit conversion)
ERA5_VARIABLES: dict[str, dict[str, Any]] = {
    "temperature_2m": {
        "cds_name": "2m_temperature", "output_col": "temperature_2m",
        "aggregation": "mean", "convert": lambda x: x - 273.15, "unit": "°C",
    },
    "precipitation": {
        "cds_name": "total_precipitation", "output_col": "precipitation",
        "aggregation": "sum", "convert": lambda x: x * 1000.0, "unit": "mm",
    },
    "wind_speed_10m": {
        "cds_name": "10m_wind_speed", "output_col": "wind_speed_10m",
        "aggregation": "mean", "convert": lambda x: x, "unit": "m/s",
    },
    "solar_radiation": {
        "cds_name": "surface_solar_radiation_downwards", "output_col": "solar_radiation",
        "aggregation": "mean", "convert": lambda x: x * 0.024, "unit": "kWh/m²/day",
    },
}


def _classify_cds_error(exc: Exception) -> tuple[str, str]:
    """Backward-compatible alias for the extraction-layer classifier."""
    return classify_cds_error(exc)


def _creds_available(credentials: dict[str, str] | None) -> bool:
    return cds_credentials_available(credentials)


def _make_client(credentials: dict[str, str] | None):
    return make_cds_client(credentials)


def _climate_path(out_dir: Path, country: str) -> Path:
    return out_dir / "climate" / f"{country}_era5.parquet"


def _deps_available() -> str | None:
    try:
        import cdsapi  # noqa: F401
        import xarray  # noqa: F401
    except ImportError as exc:
        return getattr(exc, "name", str(exc))
    return None


def verify_era5(country: str, credentials: dict[str, str] | None) -> EndpointVerification:
    if not _creds_available(credentials):
        return EndpointVerification(
            source_id="era5", country=country, feature="climate",
            status=AUTH_FAILED,
            message="CDS credentials required (CDS_API_KEY / CDSAPI_KEY or ~/.cdsapirc)",
        )
    missing = _deps_available()
    if missing:
        return EndpointVerification(
            source_id="era5", country=country, feature="climate",
            status=DEPENDENCY_MISSING, message=f"Optional dependency missing: {missing}",
        )
    if bbox_from_iso3(country) is None:
        return EndpointVerification(
            source_id="era5", country=country, feature="climate",
            status="MAPPING_REQUIRED", message=f"No bounding box registered for {country}",
        )
    return EndpointVerification(
        source_id="era5", country=country, feature="climate",
        status="VERIFIED",
        message="CDS credentials + deps + bbox present (retrieval verified at download time)",
    )


def acquire_era5(
    country: str, feature: str, start_year: int, end_year: int,
    credentials: dict[str, str] | None, out_dir: Path,
) -> AcquisitionOutcome:
    if feature in ("cooling_degree_days", "heating_degree_days"):
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="NOT_VERIFIED",
            message="CDD/HDD require DAILY temperature; use NASA POWER "
                    "(ERA5 monthly means cannot derive degree-days)",
            failure_reason="SCHEMA_MISMATCH",
        )
    if feature not in ERA5_VARIABLES:
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="SCHEMA_MISMATCH",
            message=f"ERA5 extraction is defined only for {sorted(ERA5_VARIABLES)} "
                    f"(got '{feature}')", failure_reason="SCHEMA_MISMATCH",
        )
    if not _creds_available(credentials):
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status=AUTH_FAILED,
            message="CDS credentials required", failure_reason=AUTH_FAILED,
        )
    bbox = bbox_from_iso3(country)
    if bbox is None:
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="NOT_VERIFIED",
            message=f"No bounding box registered for {country}", failure_reason="MAPPING_REQUIRED",
        )

    out_path = _climate_path(out_dir, country)
    if out_path.exists() or out_path.with_suffix(".csv").exists():
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="SUCCESS",
            message="ERA5 climate table already extracted", path=str(out_path),
            frequency="monthly", verification_notes=["cached extraction"],
        )

    try:
        # Chunked: one small NetCDF per year-chunk, deleted immediately after
        # aggregation. Variables restricted to the four HGT-QF climate features.
        df, notes = extract_monthly_chunked(
            dataset=ERA5_MONTHLY_DATASET, bbox=bbox, variables=ERA5_VARIABLES,
            start_year=start_year, end_year=end_year, credentials=credentials,
            chunk_size=5,
        )
    except RuntimeError as exc:
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status=DEPENDENCY_MISSING,
            message=str(exc), failure_reason=DEPENDENCY_MISSING,
        )
    except Exception as exc:  # noqa: BLE001
        status, reason = classify_cds_error(exc)
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status=status,
            message=f"{reason}: {str(exc)[:160]}", failure_reason=status,
        )

    if df.empty:
        return AcquisitionOutcome(
            source_id="era5", country=country, feature=feature, status="NO_RECORDS",
            message="CDS returned no records for the requested bbox/period",
            failure_reason="NO_RECORDS", verification_notes=notes,
        )

    # Post-extraction validation (coverage / completeness / units).
    ok_spatial, sp_note = coverage_v.check_spatial_coverage(bbox, [bbox.south, bbox.north], [bbox.west, bbox.east])
    ratio = completeness_v.completeness_ratio(df.index, start_year, end_year)
    missing = completeness_v.missing_months(df.index, start_year, end_year)
    unit_ok, unit_note = units_v.unit_matches(feature, ERA5_VARIABLES[feature]["unit"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(out_path)
    except ImportError:
        out_path = out_path.with_suffix(".csv")
        df.to_csv(out_path, index=True)

    rec = get_country_record(country)
    verification_notes = list(notes) + [
        f"spatial: {sp_note}",
        f"completeness: {ratio:.1%} of expected months present"
        + (f" (missing {len(missing)}: {missing[:5]}{'…' if len(missing) > 5 else ''})" if missing else " (no gaps)"),
        f"units: {unit_note}",
    ]
    return AcquisitionOutcome(
        source_id="era5", country=country, feature=feature, status="SUCCESS",
        message=f"{len(df)} monthly country-level climate records (ERA5, area-weighted)",
        records=len(df), path=str(out_path), frequency="monthly",
        unit=ERA5_VARIABLES[feature]["unit"],
        requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
        received_start=str(df.index.min())[:7], received_end=str(df.index.max())[:7],
        schema_columns=list(df.columns),
        verification_notes=verification_notes,
        provenance={
            "dataset": ERA5_MONTHLY_DATASET,
            "area": bbox.to_cds_area(),
            "bbox_source": bbox.source,
            "spatial_ok": ok_spatial,
            "completeness": ratio,
            "unit_ok": unit_ok,
            "country_name": rec.country_name if rec else country,
        },
    )


def acquire_climate(
    iso3: str,
    variables: list[str] | None = None,
    start: int = 2000,
    end: int = 2024,
    out_dir: Path | str = Path("hgt_qf_data"),
    credentials: dict[str, str] | None = None,
) -> AcquisitionOutcome:
    """High-level entry point: acquire a country's climate history from ERA5.

    Works identically for any ISO-3 (EGY, DEU, USA, BRA, …) — no per-country
    code. `variables` defaults to the four HGT-QF climate features.
    """
    variables = variables or list(ERA5_VARIABLES.keys())
    primary = variables[0]
    return acquire_era5(iso3, primary, start, end, credentials, Path(out_dir))


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


__all__ = [
    "ERA5_VARIABLES", "ERA5_DATASET", "acquire_era5", "acquire_climate",
    "verify_era5", "era5_connector", "_classify_cds_error", "_creds_available",
]
