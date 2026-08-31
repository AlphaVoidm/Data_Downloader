"""Spatial/temporal coverage checks for retrieved gridded data."""
from __future__ import annotations

from typing import Any


def check_spatial_coverage(bbox, lat: Any, lon: Any) -> tuple[bool, str]:
    """Confirm the retrieved grid actually covers the requested bounding box.

    `bbox` may be a spatial.bbox.BBox or a (north, west, south, east) tuple.
    `lat`/`lon` are the retrieved grid's coordinate arrays.
    """
    north, west, south, east = getattr(bbox, "as_tuple", lambda: bbox)()
    lat_lo, lat_hi = float(min(lat)), float(max(lat))
    lon_lo, lon_hi = float(min(lon)), float(max(lon))
    ok_lat = lat_lo <= south and lat_hi >= north
    ok_lon = lon_lo <= west and lon_hi >= east
    if ok_lat and ok_lon:
        return True, "retrieved grid covers requested bbox"
    return False, (
        f"grid does not cover bbox "
        f"(lat [{lat_lo:.2f},{lat_hi:.2f}] vs [{south},{north}]; "
        f"lon [{lon_lo:.2f},{lon_hi:.2f}] vs [{west},{east}])"
    )


def check_temporal_coverage(start_year: int, end_year: int, dates: Any) -> tuple[bool, str]:
    """Confirm the retrieved timestamps span the requested year range."""
    import pandas as pd
    d = pd.DatetimeIndex(pd.to_datetime(dates))
    if len(d) == 0:
        return False, "no timestamps retrieved"
    first, last = d.min().year, d.max().year
    if first <= start_year and last >= end_year:
        return True, f"timestamps span {first}-{last}"
    return False, f"timestamps span {first}-{last}, requested {start_year}-{end_year}"


__all__ = ["check_spatial_coverage", "check_temporal_coverage"]
