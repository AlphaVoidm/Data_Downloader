"""Spatial helpers: country geometry, bounding boxes, raster aggregation.

These provide the country -> bbox -> area-weighted aggregate chain used by the
gridded climate connectors (ERA5/CDS). No network, no heavy dependencies beyond
numpy/pandas (xarray objects are accepted by ``raster_aggregate``).
"""
from .bbox import BBox, bbox_from_iso3
from .country_geometry import country_bbox, country_centroid, country_name
from .raster_aggregate import area_weights, aggregate_grid_to_series

__all__ = [
    "BBox", "bbox_from_iso3",
    "country_bbox", "country_centroid", "country_name",
    "area_weights", "aggregate_grid_to_series",
]
