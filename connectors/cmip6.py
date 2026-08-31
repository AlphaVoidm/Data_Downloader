"""CMIP6 / CDS connector (model + experiment + spatial-subset extraction).

CMIP6 is a *globally gridded* model archive — a country is never "uncovered".
The correct acquisition question is:

    Which model?  Which experiment?  Which variable?  Which period?
    Which geographic region (country bbox)?

This connector answers those with explicit arguments and then runs the same
chunked spatial-subset pipeline as ERA5:

    country bbox -> CDS request (model/experiment/variable/period + bbox)
                 -> chunked downloads (small archive per year-chunk)
                 -> area-weighted spatial aggregation
                 -> compact country-level monthly Parquet/CSV
                 -> temporary archives deleted after every chunk

Historical usage:    historical + tas + EGY bbox + 2000-2014
Scenario usage:      ssp2_4_5   + tas + EGY bbox + 2015-2100
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from country_registry import get_country_record
from extraction.cds_extractor import (
    AUTH_FAILED,
    DEPENDENCY_MISSING,
    cds_credentials_available,
    classify_cds_error,
)
from extraction.cmip6_extractor import (
    CMIP6_DATASET,
    extract_cmip6_monthly_chunked,
    normalize_experiment,
    resolve_variable,
)
from spatial.bbox import bbox_from_iso3

from .base import EndpointVerification, AcquisitionOutcome

# Reasonable defaults (documented models from the official CDS examples).
DEFAULT_MODELS = {
    "historical": "mpi_esm1_2_hr",
    "ssp1_2_6": "mpi_esm1_2_hr",
    "ssp2_4_5": "mpi_esm1_2_hr",
    "ssp3_7_0": "mpi_esm1_2_hr",
    "ssp5_8_5": "mpi_esm1_2_hr",
}


def _output_path(out_dir: Path, country: str, experiment: str, variable: str, model: str) -> Path:
    safe_model = model.replace("/", "_").replace(" ", "_")
    return (out_dir / "climate" / "cmip6"
            / f"{country}_{experiment}_{variable}_{safe_model}.parquet")


def verify_cmip6(country: str, credentials: dict[str, str] | None) -> EndpointVerification:
    if not cds_credentials_available(credentials):
        return EndpointVerification(
            source_id="cmip6", country=country, feature="climate_scenario",
            status=AUTH_FAILED,
            message="CDS credentials required (CDS_API_KEY / CDSAPI_KEY or ~/.cdsapirc)",
        )
    try:
        import cdsapi  # noqa: F401
        import xarray  # noqa: F401
    except ImportError as exc:
        return EndpointVerification(
            source_id="cmip6", country=country, feature="climate_scenario",
            status=DEPENDENCY_MISSING,
            message=f"Optional dependency missing: {getattr(exc, 'name', str(exc))}",
        )
    if bbox_from_iso3(country) is None:
        return EndpointVerification(
            source_id="cmip6", country=country, feature="climate_scenario",
            status="MAPPING_REQUIRED", message=f"No bounding box registered for {country}",
        )
    return EndpointVerification(
        source_id="cmip6", country=country, feature="climate_scenario",
        status="VERIFIED",
        message="CDS credentials + deps + bbox present (retrieval verified at download time)",
    )


def acquire_cmip6(
    country: str,
    variable: str,
    experiment: str,
    model: str | None,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None,
    out_dir: Path,
    level: str | None = None,
) -> AcquisitionOutcome:
    spec = resolve_variable(variable)
    if spec is None:
        return AcquisitionOutcome(
            source_id="cmip6", country=country, feature=variable, status="SCHEMA_MISMATCH",
            message=f"Unsupported CMIP6 variable {variable!r}", failure_reason="SCHEMA_MISMATCH",
        )
    experiment = normalize_experiment(experiment)
    model = model or DEFAULT_MODELS.get(experiment, "mpi_esm1_2_hr")

    if not cds_credentials_available(credentials):
        return AcquisitionOutcome(
            source_id="cmip6", country=country, feature=variable, status=AUTH_FAILED,
            message="CDS credentials required", failure_reason=AUTH_FAILED,
        )
    bbox = bbox_from_iso3(country)
    if bbox is None:
        return AcquisitionOutcome(
            source_id="cmip6", country=country, feature=variable, status="NOT_VERIFIED",
            message=f"No bounding box registered for {country}", failure_reason="MAPPING_REQUIRED",
        )

    out_path = _output_path(out_dir, country, experiment, variable, model)
    if out_path.exists() or out_path.with_suffix(".csv").exists():
        return AcquisitionOutcome(
            source_id="cmip6", country=country, feature=variable, status="SUCCESS",
            message="CMIP6 table already extracted", path=str(out_path),
            frequency="monthly", verification_notes=["cached extraction"],
        )

    try:
        df, notes = extract_cmip6_monthly_chunked(
            bbox=bbox, variable=variable, experiment=experiment, model=model,
            start_year=start_year, end_year=end_year, credentials=credentials,
            level=level,
        )
    except RuntimeError as exc:
        return AcquisitionOutcome(
            source_id="cmip6", country=country, feature=variable, status=DEPENDENCY_MISSING,
            message=str(exc), failure_reason=DEPENDENCY_MISSING,
        )
    except ValueError as exc:
        return AcquisitionOutcome(
            source_id="cmip6", country=country, feature=variable, status="SCHEMA_MISMATCH",
            message=str(exc), failure_reason="SCHEMA_MISMATCH",
        )
    except Exception as exc:  # noqa: BLE001
        status, reason = classify_cds_error(exc)
        return AcquisitionOutcome(
            source_id="cmip6", country=country, feature=variable, status=status,
            message=f"{reason}: {str(exc)[:160]}", failure_reason=status,
        )

    if df.empty:
        return AcquisitionOutcome(
            source_id="cmip6", country=country, feature=variable, status="NO_RECORDS",
            message="CDS returned no records for the requested model/experiment/bbox/period",
            failure_reason="NO_RECORDS", verification_notes=notes,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(out_path)
    except ImportError:
        out_path = out_path.with_suffix(".csv")
        df.to_csv(out_path, index=True)

    rec = get_country_record(country)
    return AcquisitionOutcome(
        source_id="cmip6", country=country, feature=variable, status="SUCCESS",
        message=f"{len(df)} monthly country-level CMIP6 records "
                f"({experiment} {variable}, {model}, area-weighted)",
        records=len(df), path=str(out_path), frequency="monthly", unit=spec["unit"],
        requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
        received_start=str(df.index.min())[:7], received_end=str(df.index.max())[:7],
        schema_columns=list(df.columns),
        verification_notes=list(notes),
        provenance={
            "dataset": CMIP6_DATASET,
            "experiment": experiment,
            "variable": variable,
            "model": model,
            "area": bbox.to_cds_area(),
            "bbox_source": bbox.source,
            "country_name": rec.country_name if rec else country,
        },
    )


def acquire_cmip6_scenario(
    iso3: str,
    experiment: str = "ssp2_4_5",
    variable: str = "tas",
    model: str | None = None,
    start: int = 2015,
    end: int = 2100,
    out_dir: Path | str = Path("hgt_qf_data"),
    credentials: dict[str, str] | None = None,
) -> AcquisitionOutcome:
    """Convenience entry point: a future-climate scenario for any ISO-3.

    e.g. acquire_cmip6_scenario("EGY", experiment="ssp245", variable="tas",
    start=2015, end=2100) — or experiment="historical", start=2000, end=2014.
    """
    return acquire_cmip6(iso3, variable, experiment, model, start, end, credentials, Path(out_dir))


def diagnose_cmip6(
    country: str, variable: str, experiment: str, model: str | None,
    start_year: int, end_year: int, credentials: dict[str, str] | None,
) -> dict[str, Any]:
    """Diagnostic for `test-source cmip6`: resolved bbox, request params,
    auth status, dependency status, and (if run) record count."""
    spec = resolve_variable(variable)
    experiment = normalize_experiment(experiment)
    model = model or DEFAULT_MODELS.get(experiment, "mpi_esm1_2_hr")
    bbox = bbox_from_iso3(country)
    diag: dict[str, Any] = {
        "source": "cmip6",
        "country": country,
        "dataset": CMIP6_DATASET,
        "variable": variable,
        "cds_variable": spec["cds_name"] if spec else None,
        "experiment": experiment,
        "model": model,
        "auth_supplied": cds_credentials_available(credentials),
        "bbox": bbox.to_cds_area() if bbox else None,
        "bbox_source": bbox.source if bbox else None,
        "request_params": {},
        "records": 0,
        "failure_reason": "",
    }
    if spec is None:
        diag["failure_reason"] = f"SCHEMA_MISMATCH: unsupported variable {variable!r}"
        return diag
    if bbox is None:
        diag["failure_reason"] = f"MAPPING_REQUIRED: no bbox registered for {country}"
        return diag
    if not cds_credentials_available(credentials):
        diag["failure_reason"] = "AUTH_FAILED: CDS credentials required"
        return diag
    try:
        import cdsapi  # noqa: F401
        import xarray  # noqa: F401
    except ImportError as exc:
        diag["failure_reason"] = f"DEPENDENCY_MISSING: {getattr(exc, 'name', str(exc))}"
        return diag

    diag["request_params"] = {
        "temporal_resolution": "monthly",
        "experiment": experiment,
        "variable": spec["cds_name"],
        "model": model,
        "year": [str(y) for y in range(start_year, end_year + 1)],
        "month": [f"{m:02d}" for m in range(1, 13)],
        "area": bbox.to_cds_area(),
        "format": "netcdf",
    }
    outcome = acquire_cmip6(country, variable, experiment, model, start_year, end_year,
                            credentials, Path("hgt_qf_data"))
    diag["status"] = outcome.status
    diag["records"] = outcome.records
    diag["output_path"] = outcome.path
    if outcome.status != "SUCCESS":
        diag["failure_reason"] = f"{outcome.failure_reason}: {outcome.message}"
    return diag


def cmip6_connector(
    country: str, feature: str, start: int, end: int,
    credentials: dict[str, str] | None, out_dir: Path, **kwargs: Any,
) -> tuple[EndpointVerification, AcquisitionOutcome]:
    verification = verify_cmip6(country, credentials)
    if verification.status != "VERIFIED":
        from .base import acquisition_status_for_verification
        status = acquisition_status_for_verification(verification.status)
        return verification, AcquisitionOutcome(
            source_id="cmip6", country=country, feature=feature, status=status,
            message=verification.message, failure_reason=verification.status,
        )
    variable = kwargs.get("variable", "tas")
    experiment = kwargs.get("experiment", "historical")
    model = kwargs.get("model")
    outcome = acquire_cmip6(country, variable, experiment, model, start, end,
                            credentials, out_dir)
    return verification, outcome


__all__ = [
    "CMIP6_DATASET", "DEFAULT_MODELS", "verify_cmip6", "acquire_cmip6",
    "acquire_cmip6_scenario", "diagnose_cmip6", "cmip6_connector",
]
