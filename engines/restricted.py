"""RESTRICTED-source engine: report honestly, do not attempt acquisition.

Restricted sources (IEA, some price/sectoral datasets) are never queried. The
engine returns an honest status so the researcher sees "restricted access" —
never a fabricated download or a vague error.
"""
from __future__ import annotations

from typing import Any

from connectors.base import AcquisitionOutcome, EndpointVerification


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
    return (
        EndpointVerification(
            source_id=source_id, country=country, feature=feature,
            status="NOT_SUPPORTED",
            message=f"{source_id} is a restricted source — reporting only, not attempted",
        ),
        AcquisitionOutcome(
            source_id=source_id, country=country, feature=feature,
            status="NOT_SUPPORTED",
            message=f"{source_id} is a restricted source — reporting only, not attempted",
            failure_reason="NOT_SUPPORTED",
        ),
    )


__all__ = ["acquire"]
