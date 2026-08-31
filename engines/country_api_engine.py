"""COUNTRY/API acquisition engine.

Executes per-country API/tabular sources: each connector resolves the country's
provider identifier and queries the documented endpoint, with production HTTP
retry/backoff, then validates the response before saving.
"""
from __future__ import annotations

from typing import Any

from connectors.base import (
    AcquisitionOutcome,
    EndpointVerification,
    NOT_VERIFIED,
)

_CONNECTORS: dict[str, str] = {
    "ember": "connectors.ember.ember_connector",
    "world_bank": "connectors.world_bank.world_bank_connector",
    "nager": "connectors.misc.nager_connector",
    "entsoe": "connectors.entsoe.entsoe_connector",
    "eia": "connectors.eia.eia_connector",
    "neso": "connectors.neso.neso_connector",
    "eurostat": "connectors.misc.eurostat_connector",
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
                                 message=f"No country-api connector registered for {source_id}"),
            AcquisitionOutcome(source_id=source_id, country=country, feature=feature,
                               status=CONFIGURATION_ERROR,
                               message=f"No country-api connector registered for {source_id}",
                               failure_reason=CONFIGURATION_ERROR),
        )
    connector = _load(fqn)
    return connector(country, feature, start, end, credentials, out_dir, **kwargs)


__all__ = ["acquire"]
