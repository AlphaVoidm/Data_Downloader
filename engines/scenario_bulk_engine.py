"""SCENARIO/BULK acquisition engine.

Executes bulk/scenario datasets: download/cache the bulk artifact once, then
extract the requested country's rows. Never downloads the whole archive per
country and never asks "does this dataset contain a country row?" — for bulk
scenario/raster data the question is "extract country X's rows/cells".
"""
from __future__ import annotations

from typing import Any

from connectors.base import (
    AcquisitionOutcome,
    EndpointVerification,
    NOT_VERIFIED,
)

_CONNECTORS: dict[str, str] = {
    "iiasa": "connectors.iiasa.iiasa_connector",
    "owid": "connectors.misc.owid_connector",
    "irena": "connectors.misc.irena_connector",
    "aemo": "connectors.misc.aemo_connector",
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
                                 message=f"No bulk connector registered for {source_id}"),
            AcquisitionOutcome(source_id=source_id, country=country, feature=feature,
                               status=CONFIGURATION_ERROR,
                               message=f"No bulk connector registered for {source_id}",
                               failure_reason=CONFIGURATION_ERROR),
        )
    connector = _load(fqn)
    return connector(country, feature, start, end, credentials, out_dir, **kwargs)


__all__ = ["acquire"]
