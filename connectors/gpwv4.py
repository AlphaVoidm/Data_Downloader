"""GPWv4 (Gridded Population of the World v4) raster connector.

GPWv4 is a *gridded raster* — a country is never "not covered"; the correct
question is "can I spatially extract this country's cells and sum them?". The
connector therefore does zonal statistics:

    download/cache one population-density GeoTIFF per epoch (once)
        -> clip the raster window to the country bounding box
        -> mask to the bbox (rasterio window read)
        -> zonal sum: population = sum(density[persons/km2] * cell_area[km2])
        -> write an annual country row

Rasters (SEDAC GPWv4 Population Density, Revision 11, 30 arc-second):
    https://sedac.ciesin.columbia.edu/downloads/data/gpw-v4/gpw-v4-population-density-rev11/
The URL list is configurable via ``GPW4_URL_TEMPLATE`` / ``GPW4_URLS`` env vars.
"""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from spatial.bbox import bbox_from_iso3

from .base import AcquisitionOutcome, EndpointVerification, _HTTP

GPW4_YEARS = (2000, 2005, 2010, 2015, 2020)

_DEFAULT_TEMPLATE = (
    "https://sedac.ciesin.columbia.edu/downloads/data/gpw-v4/"
    "gpw-v4-population-density-rev11/gpw-v4-population-density-rev11_{year}_30_sec_tif.zip"
)

# Population density is persons/km2 on a regular lat/lon grid.
_KM_PER_DEG = 111.320


def _url_for(year: int) -> str:
    template = os.getenv("GPW4_URL_TEMPLATE", _DEFAULT_TEMPLATE)
    return template.format(year=year)


def cell_area_km2(latitudes: np.ndarray, lat_step_deg: float, lon_step_deg: float) -> np.ndarray:
    """Area in km² of each grid cell at the given latitudes.

    height = lat_step_deg * 111.32 km; width = lon_step_deg * 111.32 * cos(lat).
    """
    lat = np.asarray(latitudes, dtype=float)
    height = _KM_PER_DEG * abs(lat_step_deg)
    width = _KM_PER_DEG * abs(lon_step_deg) * np.cos(np.deg2rad(lat)).clip(min=0.0)
    return (height * width)[:, None]


def zonal_sum(density: np.ndarray, latitudes: np.ndarray, lat_step: float, lon_step: float) -> dict[str, float]:
    """Zonal statistics over a density array (persons/km²).

    ``density`` may contain NaN/nodata cells, which are excluded from both the
    numerator and the denominator. Returns population (sum), mean density, and
    the number of valid cells.
    """
    dens = np.asarray(density, dtype=float)
    finite = np.isfinite(dens)
    area = cell_area_km2(latitudes, lat_step, lon_step)
    population = float(np.nansum(np.where(finite, dens, 0.0) * area))
    valid_area = float(np.nansum(np.where(finite, 1.0, 0.0) * area))
    density_mean = population / valid_area if valid_area else float("nan")
    return {
        "population": population,
        "density_mean_km2": density_mean,
        "cell_count": int(finite.sum()),
    }


def _years_in_range(start: int, end: int) -> list[int]:
    return [y for y in GPW4_YEARS if start <= y <= end]


def _cache_dir(out_dir: Path) -> Path:
    return out_dir / "population" / "gpwv4" / "_cache"


def _download_zip(year: int, out_dir: Path, history: list[dict[str, Any]] | None = None) -> Path:
    cache_dir = _cache_dir(out_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"gpw-v4-population-density-rev11_{year}_30_sec_tif.zip"
    if target.exists():
        return target
    urls = [u.strip() for u in os.getenv("GPW4_URLS", "").split(",") if u.strip()]
    urls.append(_url_for(year))
    last_err = ""
    for url in urls:
        try:
            resp = _HTTP.get(url, timeout=300, history=history)
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}"
                continue
            target.write_bytes(resp.content)
            return target
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"Could not download GPWv4 raster for {year}: {last_err}")


def _open_raster(zip_path: Path) -> Any:
    """Extract the GeoTIFF from the zip and open it with rasterio."""
    try:
        import rasterio  # type: ignore
    except ImportError as exc:
        raise RuntimeError("DEPENDENCY_MISSING: rasterio is not installed") from exc
    with zipfile.ZipFile(zip_path) as zf:
        tif_name = next((n for n in zf.namelist() if n.lower().endswith(".tif")), None)
        if not tif_name:
            raise ValueError("GPWv4 zip contained no GeoTIFF")
        tif_bytes = zf.read(tif_name)
    return rasterio.open(io.BytesIO(tif_bytes)), tif_name


def zonal_for_bbox(raster: Any, bbox) -> dict[str, float]:
    """Window-read the raster over a bbox and return zonal statistics."""
    window = raster.window(bbox.west, bbox.south, bbox.east, bbox.north)
    data = raster.read(1, window=window)                     # persons/km2
    rows, cols = data.shape
    transform = raster.window_transform(window)
    lat_step = transform.e                       # dy (negative for north-up)
    lon_step = transform.a                       # dx
    top_lat = transform.f
    latitudes = np.array([top_lat + (r + 0.5) * lat_step for r in range(rows)])
    return zonal_sum(data, latitudes, lat_step, lon_step)


def _output_path(out_dir: Path, country: str) -> Path:
    return out_dir / "population" / "gpwv4" / f"{country}.csv"


def acquire_gpwv4(
    country: str,
    feature: str,
    start_year: int,
    end_year: int,
    out_dir: Path,
) -> AcquisitionOutcome:
    if feature not in ("total_population", "population_density"):
        return AcquisitionOutcome(
            source_id="gpwv4", country=country, feature=feature, status="SCHEMA_MISMATCH",
            message=f"GPWv4 supports total_population / population_density, not {feature!r}",
            failure_reason="SCHEMA_MISMATCH",
        )
    bbox = bbox_from_iso3(country)
    if bbox is None:
        return AcquisitionOutcome(
            source_id="gpwv4", country=country, feature=feature, status="MAPPING_REQUIRED",
            message=f"No bounding box registered for {country}", failure_reason="MAPPING_REQUIRED",
        )
    try:
        import rasterio  # noqa: F401
    except ImportError:
        return AcquisitionOutcome(
            source_id="gpwv4", country=country, feature=feature, status="DEPENDENCY_MISSING",
            message="rasterio is not installed (pip install rasterio)",
            failure_reason="DEPENDENCY_MISSING",
        )

    years = _years_in_range(start_year, end_year)
    if not years:
        return AcquisitionOutcome(
            source_id="gpwv4", country=country, feature=feature, status="NO_DATA",
            message=f"No GPWv4 epochs in {start_year}-{end_year} (available {GPW4_YEARS})",
            failure_reason="NO_DATA",
        )

    history: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    try:
        for year in years:
            zip_path = _download_zip(year, out_dir, history)
            raster, tif_name = _open_raster(zip_path)
            try:
                stats = zonal_for_bbox(raster, bbox)
            finally:
                raster.close()
            rows.append({
                "iso3": country, "year": year,
                "population": stats["population"],
                "density_mean_km2": stats["density_mean_km2"],
                "cell_count": stats["cell_count"],
                "source": f"GPWv4 rev11 {tif_name}",
            })
    except RuntimeError as exc:
        if str(exc).startswith("DEPENDENCY_MISSING"):
            return AcquisitionOutcome(
                source_id="gpwv4", country=country, feature=feature, status="DEPENDENCY_MISSING",
                message=str(exc), failure_reason="DEPENDENCY_MISSING",
            )
        return AcquisitionOutcome(
            source_id="gpwv4", country=country, feature=feature, status="NETWORK_ERROR",
            message=str(exc), failure_reason="NETWORK_ERROR", attempts=history,
        )
    except Exception as exc:  # noqa: BLE001
        return AcquisitionOutcome(
            source_id="gpwv4", country=country, feature=feature, status="NETWORK_ERROR",
            message=f"GPWv4 zonal extraction failed: {exc}", failure_reason="NETWORK_ERROR",
            attempts=history,
        )

    import pandas as pd
    df = pd.DataFrame(rows)
    out_path = _output_path(out_dir, country)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    return AcquisitionOutcome(
        source_id="gpwv4", country=country, feature=feature, status="SUCCESS",
        message=f"{len(df)} GPWv4 zonal observations for {country} ({feature})",
        records=len(df), path=str(out_path), frequency="5-year",
        unit="persons" if feature == "total_population" else "persons/km²",
        requested_start=f"{start_year}", requested_end=f"{end_year}",
        received_start=str(df['year'].min()), received_end=str(df['year'].max()),
        schema_columns=list(df.columns),
        verification_notes=["raster zonal statistics over country bbox",
                            "density × cell area summed (no interpolation)",
                            "raster cached (downloaded once per epoch)"],
        provenance={"bbox": bbox.to_cds_area(), "bbox_source": bbox.source,
                    "epochs": years, "method": "zonal_sum"},
        attempts=history,
    )


def verify_gpwv4(country: str) -> EndpointVerification:
    if bbox_from_iso3(country) is None:
        return EndpointVerification(
            source_id="gpwv4", country=country, feature="population_raster",
            status="MAPPING_REQUIRED", message=f"No bounding box registered for {country}",
        )
    try:
        import rasterio  # noqa: F401
    except ImportError:
        return EndpointVerification(
            source_id="gpwv4", country=country, feature="population_raster",
            status="DEPENDENCY_MISSING", message="rasterio is not installed",
        )
    return EndpointVerification(
        source_id="gpwv4", country=country, feature="population_raster", status="VERIFIED",
        message="GPWv4 raster + bbox present (retrieval verified at download time)",
    )


def diagnose_gpwv4(country: str, feature: str, start_year: int, end_year: int, out_dir: Path) -> dict[str, Any]:
    bbox = bbox_from_iso3(country)
    try:
        import rasterio  # noqa: F401
        deps_ok = True
    except ImportError:
        deps_ok = False
    diag: dict[str, Any] = {
        "source": "gpwv4", "country": country, "feature": feature,
        "bbox": bbox.to_cds_area() if bbox else None, "bbox_source": bbox.source if bbox else None,
        "epochs": _years_in_range(start_year, end_year),
        "raster_url": _url_for(2020), "deps_available": deps_ok,
        "auth_supplied": False, "status": "", "records": 0, "output_path": "", "failure_reason": "",
    }
    outcome = acquire_gpwv4(country, feature, start_year, end_year, out_dir)
    diag["status"] = outcome.status
    diag["records"] = outcome.records
    diag["output_path"] = outcome.path
    if outcome.status != "SUCCESS":
        diag["failure_reason"] = f"{outcome.failure_reason}: {outcome.message}"
    return diag


def gpwv4_connector(
    country: str, feature: str, start: int, end: int,
    credentials: dict[str, str] | None, out_dir: Path, **kwargs: Any,
) -> tuple[EndpointVerification, AcquisitionOutcome]:
    verification = verify_gpwv4(country)
    if verification.status != "VERIFIED":
        from .base import acquisition_status_for_verification
        status = acquisition_status_for_verification(verification.status)
        return verification, AcquisitionOutcome(
            source_id="gpwv4", country=country, feature=feature, status=status,
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_gpwv4(country, feature, start_year=start, end_year=end, out_dir=out_dir)
    return verification, outcome


__all__ = [
    "GPW4_YEARS", "cell_area_km2", "zonal_sum", "zonal_for_bbox",
    "acquire_gpwv4", "verify_gpwv4", "diagnose_gpwv4", "gpwv4_connector",
]
