"""Additional connectors: AEMO, Nager, Eurostat, OWID, IRENA, IEA.

These return verification + acquisition outcomes. Sources without a stable
public API (AEMO bulk, IRENA bulk, IEA restricted) surface honest statuses
(BULK_MANUAL / AUTH_FAILED) rather than fabricating coverage or data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (
    AUTH_FAILED,
    BULK_MANUAL,
    EndpointVerification,
    AcquisitionOutcome,
    _HTTP,
)

AEMO_DATA_URL = "https://aemo.com.au/en/energy-systems/electricity/national-electricity-market-nem/data-nem"
NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{iso2}"
EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"


def _iso2(iso3: str) -> str | None:
    import pycountry
    if iso3 == "XKX":
        return "XK"
    rec = pycountry.countries.get(alpha_3=iso3)
    return rec.alpha_2 if rec else None


# --- AEMO (Australia) --------------------------------------------------------
def aemo_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    if country != "AUS":
        return (
            EndpointVerification(source_id="aemo", country=country, feature=feature, status="NOT_SUPPORTED",
                                 message=f"AEMO covers Australia NEM only, not {country}"),
            AcquisitionOutcome(source_id="aemo", country=country, feature=feature, status="NOT_SUPPORTED",
                               message=f"AEMO covers Australia NEM only, not {country}", failure_reason="NOT_SUPPORTED"),
        )
    return (
        EndpointVerification(source_id="aemo", country=country, feature=feature, status=BULK_MANUAL,
                             message="AEMO NEM data available via bulk NEMWEB CSV (connector deferred)"),
        AcquisitionOutcome(source_id="aemo", country=country, feature=feature, status=BULK_MANUAL,
                           message="AEMO NEM 5-minute demand requires bulk NEMWEB file download (implemented as a later connector)",
                           frequency="five-minute", unit="MW",
                           provenance={"endpoint": AEMO_DATA_URL}),
    )


# --- Nager.Date (optional calendar) -------------------------------------------
def nager_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    iso2 = _iso2(country)
    if not iso2:
        return (
            EndpointVerification(source_id="nager", country=country, feature=feature, status="MAPPING_REQUIRED",
                                 message=f"No ISO-2 mapping for {country}"),
            AcquisitionOutcome(source_id="nager", country=country, feature=feature, status="MAPPING_REQUIRED",
                               message=f"No ISO-2 mapping for {country}", failure_reason="MAPPING_REQUIRED"),
        )
    return (
        EndpointVerification(source_id="nager", country=country, feature=feature, status=BULK_MANUAL,
                             message=f"Nager.Date holiday coverage available for {iso2} (deferred acquisition)"),
        AcquisitionOutcome(source_id="nager", country=country, feature=feature, status=BULK_MANUAL,
                           message="Optional calendar feature (deferred acquisition)", frequency="annual"),
    )


# --- Eurostat (EU optional) ---------------------------------------------------
def eurostat_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    return (
        EndpointVerification(source_id="eurostat", country=country, feature=feature, status=BULK_MANUAL,
                             message="Eurostat dissemination API available (deferred acquisition)"),
        AcquisitionOutcome(source_id="eurostat", country=country, feature=feature, status=BULK_MANUAL,
                           message="Optional feature (prices / sectoral) via Eurostat dissemination API (deferred acquisition)",
                           frequency="monthly"),
    )


# --- OWID (optional EV) --------------------------------------------------------
def owid_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    return (
        EndpointVerification(source_id="owid", country=country, feature=feature, status=BULK_MANUAL,
                             message="OWID catalogue CSV available (deferred acquisition)"),
        AcquisitionOutcome(source_id="owid", country=country, feature=feature, status=BULK_MANUAL,
                           message="Optional EV stock via OWID CSV (deferred acquisition)", frequency="annual"),
    )


# --- IRENA (renewable share fallback, annual) ---------------------------------
def irena_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    return (
        EndpointVerification(source_id="irena", country=country, feature=feature, status=BULK_MANUAL,
                             message="IRENA Renewable Energy Statistics (bulk download, deferred acquisition)"),
        AcquisitionOutcome(source_id="irena", country=country, feature=feature, status=BULK_MANUAL,
                           message="Annual renewable statistics via IRENA bulk CSV (deferred acquisition)",
                           frequency="annual"),
    )


# --- IEA (restricted) ----------------------------------------------------------
def iea_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    return (
        EndpointVerification(source_id="iea", country=country, feature=feature, status=AUTH_FAILED,
                             message="IEA data requires subscription/licensing (no programmatic public API)"),
        AcquisitionOutcome(source_id="iea", country=country, feature=feature, status=AUTH_FAILED,
                           message="IEA data requires subscription/licensing", failure_reason="AUTH_FAILED",
                           frequency="annual"),
    )



