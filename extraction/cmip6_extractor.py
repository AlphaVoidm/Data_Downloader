"""CMIP6 / CDS spatial-subset extraction engine.

Same acquisition philosophy as ``cds_extractor`` but for the
``projections-cmip6`` catalogue: the user selects model + experiment +
variable + period + country bbox, and we return a compact country-level
monthly series — never a global archive.

CMIP6 request shape (official examples):

    c.retrieve("projections-cmip6", {
        "temporal_resolution": "monthly",
        "experiment": "historical" | "ssp1_2_6" | "ssp2_4_5" | ...,
        "variable": "near_surface_air_temperature" | "precipitation" | ...,
        "model": "mpi_esm1_2_hr" | ...,
        "year": [...], "month": [...],
        "area": [north, west, south, east],
        "format": "zip" | "netcdf",
    }, "download.zip")

The retrieved NetCDF names its data variable with the CMIP short name
(e.g. ``tas``, ``pr``), not the CDS request variable name, so the extractor
resolves the actual data variable from the opened dataset.
"""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from spatial.bbox import BBox
from spatial.raster_aggregate import aggregate_grid_to_series

CMIP6_DATASET = "projections-cmip6"

# Normalize user-friendly experiment names to CDS experiment identifiers.
EXPERIMENT_ALIASES: dict[str, str] = {
    "historical": "historical",
    "ssp126": "ssp1_2_6", "ssp1_2_6": "ssp1_2_6", "ssp1-2.6": "ssp1_2_6",
    "ssp245": "ssp2_4_5", "ssp2_4_5": "ssp2_4_5", "ssp2-4.5": "ssp2_4_5",
    "ssp370": "ssp3_7_0", "ssp3_7_0": "ssp3_7_0", "ssp3-7.0": "ssp3_7_0",
    "ssp585": "ssp5_8_5", "ssp5_8_5": "ssp5_8_5", "ssp5-8.5": "ssp5_8_5",
}

# Feature/variable concept -> CMIP6 variable spec.
CMIP6_VARIABLES: dict[str, dict[str, Any]] = {
    "tas": {
        "cds_name": "near_surface_air_temperature",
        "nc_names": ["tas", "near_surface_air_temperature"],
        "output_col": "temperature_2m",
        "aggregation": "mean",
        "convert": lambda x: x - 273.15,
        "unit": "°C",
    },
    "temperature": {
        "cds_name": "near_surface_air_temperature",
        "nc_names": ["tas", "near_surface_air_temperature"],
        "output_col": "temperature_2m",
        "aggregation": "mean",
        "convert": lambda x: x - 273.15,
        "unit": "°C",
    },
    "pr": {
        "cds_name": "precipitation",
        "nc_names": ["pr", "precipitation"],
        "output_col": "precipitation",
        "aggregation": "mean",
        "convert": lambda x: x * 86400.0,  # kg m-2 s-1 -> mm/day
        "unit": "mm/day",
    },
    "precipitation": {
        "cds_name": "precipitation",
        "nc_names": ["pr", "precipitation"],
        "output_col": "precipitation",
        "aggregation": "mean",
        "convert": lambda x: x * 86400.0,
        "unit": "mm/day",
    },
}


def normalize_experiment(experiment: str) -> str:
    key = experiment.strip().lower().replace(".", "_")
    for alias, canon in EXPERIMENT_ALIASES.items():
        if key == alias or key.replace("-", "_") == alias:
            return canon
    return experiment.strip()


def resolve_variable(variable: str) -> dict[str, Any] | None:
    key = variable.strip().lower()
    if key in CMIP6_VARIABLES:
        return dict(CMIP6_VARIABLES[key])
    # direct CDS variable name fallback (e.g. "near_surface_air_temperature")
    for spec in CMIP6_VARIABLES.values():
        if spec["cds_name"].lower() == key or key in {n.lower() for n in spec["nc_names"]}:
            return dict(spec)
    return None


def _iter_netcdf_files(path: Path) -> list[Path]:
    """Return the NetCDF file(s) inside a retrieved archive or the file itself."""
    if not path.exists():
        return []
    if path.suffix.lower() == ".nc":
        return [path]
    if zipfile.is_zipfile(path):
        out_dir = path.parent
        try:
            with zipfile.ZipFile(path) as zf:
                zf.extractall(out_dir)
        except Exception:  # noqa: BLE001
            return []
        return sorted(p for p in out_dir.rglob("*.nc"))
    return []


def _pick_nc_name(ds: Any, candidates: list[str]) -> str | None:
    for name in candidates:
        if name in ds:
            return name
    # fall back to the first non-coordinate data variable
    for name, var in ds.data_vars.items():
        if name.endswith("_bnds") or name == "time_bnds":
            continue
        if set(getattr(var, "dims", ())).intersection({"time", "valid_time"}):
            return name
    return None


def extract_cmip6_monthly_chunked(
    *,
    bbox: BBox,
    variable: str,
    experiment: str,
    model: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None,
    chunk_size: int = 10,
    level: str | None = None,
    data_format: str = "netcdf",
    keep_temp: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    """Chunked CMIP6 bbox retrieval -> country-level monthly DataFrame."""
    from extraction.cds_extractor import make_cds_client, iter_year_chunks

    client = make_cds_client(credentials)
    import xarray as xr  # type: ignore

    spec = resolve_variable(variable)
    if spec is None:
        raise ValueError(f"Unsupported CMIP6 variable {variable!r}; choose from {sorted(CMIP6_VARIABLES)}")

    experiment = normalize_experiment(experiment)
    months = [f"{m:02d}" for m in range(1, 13)]
    frames: list[pd.DataFrame] = []
    notes: list[str] = []

    for y0, y1 in iter_year_chunks(start_year, end_year, chunk_size):
        request: dict[str, Any] = {
            "temporal_resolution": "monthly",
            "experiment": experiment,
            "variable": spec["cds_name"],
            "model": model,
            "year": [str(y) for y in range(y0, y1 + 1)],
            "month": months,
            "area": bbox.to_cds_area(),
            "format": data_format,
        }
        if level:
            request["level"] = level
        tmp_dir = Path(tempfile.mkdtemp(prefix="hgtqf_cmip6_"))
        tmp_file = tmp_dir / f"chunk_{y0}_{y1}.{'nc' if data_format == 'netcdf' else 'zip'}"
        try:
            client.retrieve(CMIP6_DATASET, request, str(tmp_file))
            n_opened = 0
            for nc_path in _iter_netcdf_files(tmp_file):
                ds = xr.open_dataset(nc_path)
                nc_name = _pick_nc_name(ds, spec["nc_names"])
                if nc_name is None:
                    ds.close()
                    continue
                out_spec = {
                    "cds_name": nc_name,
                    "output_col": spec["output_col"],
                    "aggregation": spec.get("aggregation", "mean"),
                    "convert": spec["convert"],
                    "unit": spec["unit"],
                }
                frame = aggregate_grid_to_series(ds, {"cmip6": out_spec})
                ds.close()
                frames.append(frame)
                n_opened += 1
            notes.append(f"chunk {y0}-{y1}: {n_opened} file(s), {frames[-1].shape[0] if frames else 0} month(s)")
        finally:
            if not keep_temp:
                for p in tmp_dir.rglob("*"):
                    try:
                        if p.is_file():
                            p.unlink()
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
    notes.append(f"temporary CMIP6 archive(s) deleted after aggregation ({'kept' if keep_temp else 'deleted'})")
    return df, notes


__all__ = [
    "CMIP6_DATASET", "CMIP6_VARIABLES", "EXPERIMENT_ALIASES",
    "normalize_experiment", "resolve_variable", "extract_cmip6_monthly_chunked",
]
