"""Reduce gridded climate data to a country-level monthly series.

The core operation: for an xarray Dataset with dims (time, lat, lon), compute a
cos(latitude) area-weighted spatial mean (or sum) per timestep, converting units
as configured. This is *data extraction*, not ML preprocessing.

Dimension-name tolerant: newer CDS products name the time axis ``valid_time``
and lat/lon ``latitude``/``longitude``; older products use ``time``. Variable
maps specify the data-variable name explicitly so no guessing is needed there.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_TIME_DIMS = ("valid_time", "time")
_LAT_DIMS = ("latitude", "lat")
_LON_DIMS = ("longitude", "lon")


def _find_dim(ds: Any, candidates: tuple[str, ...]) -> str | None:
    dims = set(getattr(ds, "dims", {}))
    for c in candidates:
        if c in dims:
            return c
    # xarray Dataset also exposes data-variable dims; fall back to coords
    coords = set(getattr(ds, "coords", {}))
    for c in candidates:
        if c in coords:
            return c
    return None


def area_weights(latitudes: np.ndarray) -> np.ndarray:
    """cos(latitude) area weights for a regular lat/lon grid."""
    return np.cos(np.deg2rad(np.asarray(latitudes, dtype=float))).clip(min=0.0)


def aggregate_grid_to_series(
    ds: Any,
    variable_map: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    """Aggregate an xarray Dataset (time, lat, lon) to a monthly series.

    ``variable_map`` maps a concept key to a spec dict:

        {"cds_name": str,     # data-variable name in the Dataset
         "output_col": str,   # output column name
         "aggregation": str,  # "mean" | "sum"
         "convert": callable, # unit conversion applied to the aggregate
         "unit": str}         # resulting unit (metadata)

    Returns a DataFrame indexed by month timestamp with one column per concept.
    """
    time_dim = _find_dim(ds, _TIME_DIMS)
    lat_dim = _find_dim(ds, _LAT_DIMS)
    if time_dim is None or lat_dim is None:
        raise ValueError(
            f"Cannot identify time/lat dims (time={time_dim!r}, lat={lat_dim!r})"
        )

    lat = np.asarray(ds[lat_dim].values, dtype=float)
    w = area_weights(lat)[:, None, None]  # (lat, 1, 1)

    series: dict[str, np.ndarray] = {}
    for concept, spec in variable_map.items():
        cds_name = spec["cds_name"]
        if cds_name not in ds:
            continue
        arr = np.asarray(ds[cds_name].values, dtype=float)  # (time, lat, lon)
        finite = np.isfinite(arr)
        numer = np.nansum(np.where(finite, arr, 0.0) * w, axis=(1, 2))
        if spec.get("aggregation", "mean") == "sum":
            # sum over the (weighted) area — use unweighted sum of valid cells
            # times a mean-weight, i.e. precipitation in mm is already a grid
            # cell value; take the area-weighted mean too (values are per-cell).
            denom = np.nansum(np.where(finite, 1.0, 0.0) * w, axis=(1, 2))
        else:
            denom = np.nansum(np.where(finite, 1.0, 0.0) * w, axis=(1, 2))
        agg = numer / np.where(denom == 0, np.nan, denom)
        series[spec["output_col"]] = spec["convert"](agg)

    time_values = ds[time_dim].values
    idx = pd.DatetimeIndex(time_values)
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.to_datetime(time_values)
    df = pd.DataFrame(series, index=idx.to_period("M").to_timestamp("M"))
    df.index.name = "date"
    return df


__all__ = ["area_weights", "aggregate_grid_to_series"]
