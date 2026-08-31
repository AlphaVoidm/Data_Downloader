"""Additional connectors: AEMO, Nager, Eurostat, OWID, IRENA, IEA.

These return verification + acquisition outcomes. Sources without a stable
public API (AEMO bulk, IRENA bulk, IEA restricted) surface honest statuses
(BULK_MANUAL / AUTH_FAILED) rather than fabricating coverage or data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from response_validator import validate_response

from .base import (
    AUTH_FAILED,
    BULK_MANUAL,
    NO_DATA,
    EndpointVerification,
    AcquisitionOutcome,
    ConnectorError,
    _HTTP,
    acquisition_status_for_verification,
    verification_from_result,
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
def _nager_year(iso2: str, year: int, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch one year of public holidays for an ISO-2 country code.

    A 204 (no holidays that year) is legitimate and yields an empty list.
    """
    url = NAGER_URL.format(year=year, iso2=iso2)
    resp = _HTTP.get(url, timeout=30, history=history)
    if resp.status_code == 204:
        return []
    result = validate_response(resp, expected_format="json", min_records=0)
    if not result.ok:
        raise RuntimeError(result.message)
    data = resp.json()
    return data if isinstance(data, list) else []


def verify_nager(country: str, feature: str) -> EndpointVerification:
    iso2 = _iso2(country)
    if not iso2:
        return EndpointVerification(
            source_id="nager", country=country, feature=feature, status="MAPPING_REQUIRED",
            message=f"No ISO-2 mapping for {country}",
        )
    history: list[dict[str, Any]] = []
    try:
        _nager_year(iso2, 2024, history)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="nager", country=country, feature=feature,
            status=exc.status, message=str(exc), attempts=exc.attempts or history,
        )
    except RuntimeError as exc:
        return EndpointVerification(
            source_id="nager", country=country, feature=feature,
            status="NOT_VERIFIED", message=str(exc), attempts=history,
        )
    return EndpointVerification(
        source_id="nager", country=country, feature=feature, status="VERIFIED",
        message=f"Nager.Date reachable for {iso2}", attempts=history,
    )


def nager_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    iso2 = _iso2(country)
    if not iso2:
        return (
            EndpointVerification(source_id="nager", country=country, feature=feature, status="MAPPING_REQUIRED",
                                 message=f"No ISO-2 mapping for {country}"),
            AcquisitionOutcome(source_id="nager", country=country, feature=feature, status="MAPPING_REQUIRED",
                               message=f"No ISO-2 mapping for {country}", failure_reason="MAPPING_REQUIRED"),
        )
    verification = verify_nager(country, feature)
    if verification.status != "VERIFIED":
        return verification, AcquisitionOutcome(
            source_id="nager", country=country, feature=feature,
            status=acquisition_status_for_verification(verification.status),
            message=verification.message, failure_reason=verification.status,
            attempts=verification.attempts,
        )

    history: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    try:
        for year in range(start, end + 1):
            for h in _nager_year(iso2, year, history):
                rows.append({
                    "iso3": country, "iso2": iso2, "year": year,
                    "date": h.get("date", ""), "name": h.get("name", ""),
                    "local_name": h.get("localName", ""),
                    "type": ";".join(h.get("types", []) or []),
                })
    except ConnectorError as exc:
        return verification, AcquisitionOutcome(
            source_id="nager", country=country, feature=feature,
            status=exc.status, message=str(exc), failure_reason=exc.status,
            attempts=exc.attempts or history,
        )
    except RuntimeError as exc:
        return verification, AcquisitionOutcome(
            source_id="nager", country=country, feature=feature,
            status="NOT_VERIFIED", message=str(exc), failure_reason="NOT_VERIFIED",
            attempts=history,
        )

    if not rows:
        # Legitimate: the country simply has no holidays recorded for the
        # period. Optional feature -> the country stays fully usable.
        return verification, AcquisitionOutcome(
            source_id="nager", country=country, feature=feature,
            status=NO_DATA,
            message=f"Nager.Date returned no holiday records for {iso2} ({start}-{end})",
            failure_reason=NO_DATA, frequency="annual", unit="count",
            attempts=history,
        )

    df = pd.DataFrame(rows)
    out_path = out_dir / "raw" / "calendar" / "holidays" / "nager" / f"{country}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return verification, AcquisitionOutcome(
        source_id="nager", country=country, feature=feature,
        status="SUCCESS", message=f"{len(df)} holiday records ({start}-{end})",
        records=len(df), path=str(out_path), frequency="annual", unit="count",
        requested_start=str(start), requested_end=str(end),
        received_start=str(rows[0]["year"]), received_end=str(rows[-1]["year"]),
        schema_columns=list(df.columns),
        verification_notes=[f"ISO-2 {iso2}", "JSON valid"],
        provenance={"endpoint": NAGER_URL},
        attempts=history,
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



