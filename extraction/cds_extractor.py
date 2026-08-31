"""Copernicus CDS extraction engine.

Implements the targeted, chunked retrieval pipeline the HGT-QF downloader uses
for gridded climate data:

    country bbox -> CDS request (bbox + variables + period only)
                 -> download one small chunk (NetCDF)
                 -> area-weighted spatial aggregation
                 -> append month rows
                 -> DELETE the temporary chunk file

So a country's full 2000-2024 climate history is ~300 monthly rows, never a
multi-GB global raster. Chunking by year keeps every temp file tiny and keeps
CDS jobs small/queue-friendly.

Credentials: cdsapi's current client accepts a personal access token as
``key="<uid>:<api-key>"`` or reads ``~/.cdsapirc``. ``CDS_API_KEY`` (and the
optional ``CDS_API_URL``) are translated into that form here.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from spatial.bbox import BBox
from spatial.raster_aggregate import aggregate_grid_to_series

ERA5_MONTHLY_DATASET = "reanalysis-era5-single-levels-monthly-means"

AUTH_FAILED = "AUTH_FAILED"
TERMS_NOT_ACCEPTED = "TERMS_NOT_ACCEPTED"
TIMEOUT = "TIMEOUT"
NETWORK_ERROR = "NETWORK_ERROR"
DEPENDENCY_MISSING = "DEPENDENCY_MISSING"


def cds_credentials_available(credentials: dict[str, str] | None) -> bool:
    if credentials and credentials.get("CDS_API_KEY"):
        return True
    if os.getenv("CDS_API_KEY") or os.getenv("CDSAPI_KEY"):
        return True
    return (Path.home() / ".cdsapirc").exists()


def make_cds_client(credentials: dict[str, str] | None):
    """Construct a cdsapi.Client from CDS_API_KEY / CDS_API_URL / ~/.cdsapirc."""
    try:
        import cdsapi  # type: ignore
    except ImportError as exc:
        raise RuntimeError(f"{DEPENDENCY_MISSING}: cdsapi is not installed") from exc

    key = ((credentials or {}).get("CDS_API_KEY")
           or os.getenv("CDS_API_KEY") or os.getenv("CDSAPI_KEY"))
    url = ((credentials or {}).get("CDS_API_URL") or os.getenv("CDSAPI_URL"))
    if key:
        try:
            return cdsapi.Client(url=url or "https://cds.climate.copernicus.eu/api", key=key)
        except TypeError:
            # Older cdsapi without url/key kwargs; rely on ~/.cdsapirc.
            return cdsapi.Client()
    return cdsapi.Client()


def classify_cds_error(exc: Exception) -> tuple[str, str]:
    """Map a cdsapi/requests exception onto the granular failure vocabulary.

    Includes the "dataset licence terms not accepted" case, which is distinct
    from a bad credential and from a network problem.
    """
    text = str(exc)
    low = text.lower()
    if any(k in low for k in (
        "terms and conditions", "terms of use", "licence", "license",
        "accept the terms", "you must accept", "not accepted", "licence accepted",
    )):
        return TERMS_NOT_ACCEPTED, "CDS dataset licence terms not accepted"
    if any(k in low for k in (
        "401", "403", "authentication", "forbidden", "invalid api key",
        "the request you have submitted is not valid", "credentials",
        "not authorised", "unauthorized",
    )):
        return AUTH_FAILED, "CDS authentication/authorization failed"
    if any(k in low for k in ("timed out", "timeout", "connecttimeout", "readtimeout")):
        return TIMEOUT, "CDS request timed out"
    return NETWORK_ERROR, "CDS retrieval failed (network/connection error)"


def iter_year_chunks(start_year: int, end_year: int, size: int = 5):
    """Yield (start, end) year chunks of at most ``size`` years."""
    y = start_year
    while y <= end_year:
        y1 = min(y + size - 1, end_year)
        yield y, y1
        y = y1 + 1


def extract_monthly_chunked(
    *,
    dataset: str,
    bbox: BBox,
    variables: dict[str, dict[str, Any]],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None,
    chunk_size: int = 5,
    keep_temp: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    """Chunked CDS bbox extraction -> country-level monthly DataFrame.

    ``variables`` maps concept -> spec (see ``spatial.raster_aggregate``).
    Returns (df, notes) where notes describe each chunk and cleanup.
    """
    client = make_cds_client(credentials)
    import xarray as xr  # type: ignore

    months = [f"{m:02d}" for m in range(1, 13)]
    cds_variables = [spec["cds_name"] for spec in variables.values()]
    frames: list[pd.DataFrame] = []
    notes: list[str] = []

    for y0, y1 in iter_year_chunks(start_year, end_year, chunk_size):
        request: dict[str, Any] = {
            "product_type": "monthly_averaged_reanalysis",
            "variable": cds_variables,
            "year": [str(y) for y in range(y0, y1 + 1)],
            "month": months,
            "time": "00:00",
            "area": bbox.to_cds_area(),
            "format": "netcdf",
        }
        tmp_dir = Path(tempfile.mkdtemp(prefix="hgtqf_cds_"))
        tmp_file = tmp_dir / f"chunk_{y0}_{y1}.nc"
        try:
            client.retrieve(dataset, request, str(tmp_file))
            ds = xr.open_dataset(tmp_file)
            frame = aggregate_grid_to_series(ds, variables)
            ds.close()
            frames.append(frame)
            notes.append(f"chunk {y0}-{y1}: {len(frame)} month(s) aggregated")
        finally:
            if tmp_file.exists() and not keep_temp:
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

    if not frames:
        return pd.DataFrame(), notes

    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    notes.append(f"temp NetCDF chunk(s) deleted after aggregation ({'kept' if keep_temp else 'deleted'})")
    return df, notes


__all__ = [
    "ERA5_MONTHLY_DATASET", "AUTH_FAILED", "TERMS_NOT_ACCEPTED", "TIMEOUT",
    "NETWORK_ERROR", "DEPENDENCY_MISSING",
    "cds_credentials_available", "make_cds_client", "classify_cds_error",
    "iter_year_chunks", "extract_monthly_chunked",
]
