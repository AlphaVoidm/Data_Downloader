"""Three acquisition engines (the source-execution layer).

The downloader no longer treats every source as "country → download country
data". Sources are dispatched by their declared acquisition mode to the engine
that knows how to execute them:

    country_api_engine    per-country API/tabular sources
                          (Ember, World Bank, Nager, ENTSO-E, EIA, NESO, Eurostat)
    grid_engine           gridded/point sources -> spatial subset -> zonal/area
                          aggregation (ERA5/CDS, CMIP6/CDS, GPWv4, NASA POWER)
    scenario_bulk_engine  bulk/scenario datasets -> cache once -> extract rows
                          (IIASA SSP, OWID, IRENA, AEMO)

``acquire`` returns the standard ``(EndpointVerification, AcquisitionOutcome)``
pair produced by the underlying connector.
"""
from __future__ import annotations

from typing import Any

from source_registry import (
    MODE_BULK_DATASET,
    MODE_COUNTRY_API,
    MODE_GRID_SPATIAL_SUBSET,
    MODE_POINT_API,
    MODE_RESTRICTED,
    get_source,
)


def get_engine(source_id: str):
    """Return the engine module responsible for a source's acquisition mode."""
    src = get_source(source_id)
    mode = src.acquisition_mode if src else ""
    if mode == MODE_RESTRICTED:
        from . import restricted
        return restricted
    if mode in (MODE_GRID_SPATIAL_SUBSET, MODE_POINT_API):
        from . import grid_engine
        return grid_engine
    if mode == MODE_BULK_DATASET:
        from . import scenario_bulk_engine
        return scenario_bulk_engine
    from . import country_api_engine
    return country_api_engine


def acquire(
    source_id: str,
    country: str,
    feature: str,
    start: int,
    end: int,
    credentials: dict[str, str] | None,
    out_dir,
    **kwargs: Any,
):
    """Acquire a feature from a source via the appropriate engine."""
    engine = get_engine(source_id)
    return engine.acquire(source_id, country, feature, start, end, credentials, out_dir, **kwargs)


__all__ = ["get_engine", "acquire"]
