"""HGT-QF source connectors.

Each connector exposes:

    <source_id>_connector(country, feature, start, end, credentials, out_dir)
        -> (EndpointVerification, AcquisitionOutcome)

The registry of connectors is built dynamically so new sources can be added by
dropping in a new module — no downloader rewrite required.
"""
from __future__ import annotations

from pathlib import Path

from .base import EndpointVerification, AcquisitionOutcome


def get_connector(source_id: str):
    """Return the connector callable for a source_id (lazy import)."""
    if source_id == "entsoe":
        from .entsoe import entsoe_connector
        return entsoe_connector
    if source_id == "eia":
        from .eia import eia_connector
        return eia_connector
    if source_id == "neso":
        from .neso import neso_connector
        return neso_connector
    if source_id == "ember":
        from .ember import ember_connector
        return ember_connector
    if source_id == "world_bank":
        from .world_bank import world_bank_connector
        return world_bank_connector
    if source_id == "nasa_power":
        from .nasa_power import nasa_power_connector
        return nasa_power_connector
    if source_id == "era5":
        from .era5 import era5_connector
        return era5_connector
    from .misc import (
        aemo_connector, eurostat_connector, iea_connector,
        irena_connector, nager_connector, owid_connector,
    )
    return {
        "aemo": aemo_connector, "nager": nager_connector,
        "eurostat": eurostat_connector, "owid": owid_connector,
        "irena": irena_connector, "iea": iea_connector,
    }.get(source_id)


__all__ = ["get_connector", "EndpointVerification", "AcquisitionOutcome"]
