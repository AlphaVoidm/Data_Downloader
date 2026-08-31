"""Country geometry accessors (thin wrappers over the country registry).

Kept separate from `bbox.py` so gridded connectors can ask for "the country's
bounding box / centroid / name" without knowing registry internals, and so a
future polygon dataset (Natural Earth / GADM) can replace the registry without
touching connector code.
"""
from __future__ import annotations

from country_registry import get_country_bbox, get_country_record


def country_bbox(iso3: str) -> tuple[float, float, float, float] | None:
    """Return (north, west, south, east) for a country, or None."""
    return get_country_bbox(iso3)


def country_centroid(iso3: str) -> tuple[float, float] | None:
    """Return (latitude, longitude) of the country centroid, or None."""
    rec = get_country_record(iso3)
    if rec is None:
        return None
    return rec.centroid_lat, rec.centroid_lon


def country_name(iso3: str) -> str:
    """Return the display name for a country (falls back to the code)."""
    rec = get_country_record(iso3)
    return rec.country_name if rec else iso3


__all__ = ["country_bbox", "country_centroid", "country_name"]
