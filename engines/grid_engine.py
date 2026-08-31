"""GRID/RASTER acquisition engine.

Executes gridded and point sources the only correct way for a gridded dataset:

    country bbox (or centroid) -> targeted request -> spatial subset
    -> area-weighted / zonal aggregation -> compact country-level output
    -> temporary rasters deleted

A gridded source is NEVER "not covered" for a country — if it is globally
gridded, the country is extracted as a spatial subset.
"""
from __future__ import annotations

from typing import Any

from connectors.base import (
    AcquisitionOutcome,
    EndpointVerification,
    NOT_VERIFIED,
)

_CONNECTORS: dict[str, str] = {
    "era5": "connectors.era5.era5_connector",
    "cmip6": "connectors.cmip6.cmip6_connector",
    "gpwv4": "connectors.gpwv4.gpwv4_connector",
    "nasa_power": "connectors.nasa_power.nasa_power_connector",
}


def _load(fqn: str):
    import importlib
    module_name, attr = fqn.rsplit(".", 1)
    return getattr(importlib.import_module(module_name), attr)


def acquire(
    source_id: str,
    country: str,
    feature: str,
    start: int,
    end: int,
    credentials: dict[str, str] | None,
    out_dir,
    **kwargs: Any,
) -> tuple[EndpointVerification, AcquisitionOutcome]:
    fqn = _CONNECTORS.get(source_id)
    if fqn is None:
        from connectors.base import CONFIGURATION_ERROR
        return (
            EndpointVerification(source_id=source_id, country=country, feature=feature,
                                 status=NOT_VERIFIED,
                                 message=f"No grid connector registered for {source_id}"),
            AcquisitionOutcome(source_id=source_id, country=country, feature=feature,
                               status=CONFIGURATION_ERROR,
                               message=f"No grid connector registered for {source_id}",
                               failure_reason=CONFIGURATION_ERROR),
        )
    connector = _load(fqn)
    return connector(country, feature, start, end, credentials, out_dir, **kwargs)


__all__ = ["acquire"]
