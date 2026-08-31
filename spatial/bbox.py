"""Country bounding-box model.

The HGT-QF country registry stores national extents; this module wraps them in
a small value object and produces the `[north, west, south, east]` area order
the Copernicus CDS API expects for sub-region extraction.
"""
from __future__ import annotations

from dataclasses import dataclass

from country_registry import get_country_bbox, get_country_record


@dataclass(frozen=True)
class BBox:
    north: float
    south: float
    west: float
    east: float
    source: str = ""

    def to_cds_area(self) -> list[float]:
        """CDS `area` keyword order: [north, west, south, east]."""
        return [self.north, self.west, self.south, self.east]

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.north, self.west, self.south, self.east)

    def is_valid(self) -> bool:
        return self.north > self.south and self.east > self.west

    def padded(self, margin: float = 0.25) -> "BBox":
        """Return a bbox expanded by `margin` degrees on every side."""
        return BBox(
            north=min(90.0, self.north + margin),
            south=max(-90.0, self.south - margin),
            west=max(-180.0, self.west - margin),
            east=min(180.0, self.east + margin),
            source=f"{self.source}+pad{margin}",
        )


def bbox_from_iso3(iso3: str, pad: float = 0.0) -> BBox | None:
    """Resolve a country's bounding box from the registry.

    Returns None when the country has no registered extent. Use `pad` to expand
    the box by a margin (e.g. to include coastal grid cells).
    """
    b = get_country_bbox(iso3)
    if b is None:
        return None
    north, west, south, east = b
    rec = get_country_record(iso3)
    box = BBox(
        north=float(north), south=float(south), west=float(west), east=float(east),
        source=rec.bbox_source if rec else "unknown",
    )
    if pad:
        return box.padded(pad)
    return box


__all__ = ["BBox", "bbox_from_iso3"]
