"""ENTSO-E Transparency connector.

Full verification pipeline per spec §8:
  1. verify endpoint, 2. verify auth, 3. verify area mapping,
  4. verify document type, 5. verify Content-Type, 6. verify XML validity,
  7. verify XML schema/content, 8. verify records exist.

HTTP 200 + HTML is classified PORTAL_HTML / NON_DATA_RESPONSE, never SUCCESS.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pandas as pd

from response_validator import validate_response
from source_mapping import get_primary_area_code

from .base import (
    AUTH_FAILED,
    BULK_MANUAL,
    EndpointVerification,
    AcquisitionOutcome,
    ConnectorError,
    _HTTP,
    get_credential,
    outcome_from_result,
    verification_from_result,
)

ENDPOINT = "https://web-api.tp.entsoe.eu/api"
TOKEN_ENV = "ENTSOE_API_TOKEN"
DOCUMENT_TYPE = "A65"
PROCESS_TYPE = "A16"


def _parse_load_points(body: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    records = []
    for ts in root.findall(".//{*}TimeSeries"):
        period = ts.find("{*}Period")
        if period is None:
            continue
        start_str = period.findtext("{*}timeInterval/{*}start")
        resolution = period.findtext("{*}resolution")
        for point in period.findall("{*}Point"):
            pos = point.findtext("{*}position")
            qty = point.findtext("{*}quantity")
            if qty is not None:
                records.append({
                    "period_start_utc": start_str,
                    "position": int(pos) if pos else None,
                    "resolution": resolution,
                    "load_mw": float(qty),
                })
    return records


def _agg_monthly_twh(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Aggregate hourly MW -> monthly total TWh (data extraction, not preprocessing)."""
    df = pd.DataFrame(records)
    df["ts"] = pd.to_datetime(df["period_start_utc"], errors="coerce")
    df = df.dropna(subset=["ts"])
    df["month"] = df["ts"].dt.to_period("M").dt.to_timestamp()
    # MW * 1h = MWh; sum over month / 1e6 = TWh
    monthly = df.groupby("month")["load_mw"].sum() / 1e6
    out = monthly.reset_index().rename(columns={"month": "date", "load_mw": "demand_twh"})
    out["unit"] = "TWh"
    return out


def verify_entsoe(country: str, token: str | None) -> EndpointVerification:
    eic = get_primary_area_code(country, "ENTSO-E Transparency")
    if not eic:
        return EndpointVerification(
            source_id="entsoe", country=country, feature="electricity_demand",
            status="MAPPING_REQUIRED",
            message=f"No EIC area code registered for {country}",
        )
    if not token:
        return EndpointVerification(
            source_id="entsoe", country=country, feature="electricity_demand",
            status=AUTH_FAILED, message=f"{TOKEN_ENV} not configured",
        )
    params = {
        "securityToken": token,
        "documentType": DOCUMENT_TYPE,
        "processType": PROCESS_TYPE,
        "outBiddingZone_Domain": eic,
        "periodStart": "202401010000",
        "periodEnd": "202401020000",
    }
    try:
        resp = _HTTP.get(ENDPOINT, params=params, timeout=60)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="entsoe", country=country, feature="electricity_demand",
            status=str(exc), message=str(exc),
        )
    result = validate_response(resp, expected_format="xml", min_records=1)
    return verification_from_result(result, "entsoe", country, "electricity_demand")


def acquire_entsoe(
    country: str,
    start_year: int,
    end_year: int,
    token: str | None,
    out_dir: Path,
) -> AcquisitionOutcome:
    eic = get_primary_area_code(country, "ENTSO-E Transparency")
    if not eic:
        return AcquisitionOutcome(
            source_id="entsoe", country=country, feature="electricity_demand",
            status=BULK_MANUAL, message=f"No EIC area code registered for {country}",
            failure_reason="MAPPING_REQUIRED",
        )
    if not token:
        return AcquisitionOutcome(
            source_id="entsoe", country=country, feature="electricity_demand",
            status=AUTH_FAILED, message=f"{TOKEN_ENV} not configured", failure_reason="AUTH_FAILED",
        )

    all_records: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        params = {
            "securityToken": token,
            "documentType": DOCUMENT_TYPE,
            "processType": PROCESS_TYPE,
            "outBiddingZone_Domain": eic,
            "periodStart": f"{year}01010000",
            "periodEnd": f"{year}12312300",
        }
        try:
            resp = _HTTP.get(ENDPOINT, params=params, timeout=60)
        except ConnectorError as exc:
            if all_records:
                break  # partial data already collected
            return AcquisitionOutcome(
                source_id="entsoe", country=country, feature="electricity_demand",
                status=str(exc), message=str(exc), failure_reason=str(exc),
            )
        result = validate_response(resp, expected_format="xml", min_records=0)
        if result.ok:
            all_records.extend(_parse_load_points(resp.content))
        elif result.status in ("NO_RECORDS",):
            continue  # empty year is fine
        else:
            if all_records:
                break
            return outcome_from_result(result, "entsoe", country, "electricity_demand")

    if not all_records:
        return AcquisitionOutcome(
            source_id="entsoe", country=country, feature="electricity_demand",
            status="NO_RECORDS", message="ENTSO-E returned no load points", failure_reason="NO_RECORDS",
        )

    monthly = _agg_monthly_twh(all_records)
    out_path = out_dir / "raw" / "electricity" / "demand" / "entsoe" / f"{country}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(out_path, index=False)

    return AcquisitionOutcome(
        source_id="entsoe", country=country, feature="electricity_demand",
        status="SUCCESS", message=f"{len(monthly)} monthly demand observations (from hourly)",
        records=len(monthly), path=str(out_path), frequency="monthly", unit="TWh",
        requested_start=f"{start_year}-01", requested_end=f"{end_year}-12",
        received_start=str(monthly['date'].min())[:7], received_end=str(monthly['date'].max())[:7],
        schema_columns=list(monthly.columns),
        verification_notes=["XML valid", f"EIC {eic}", "hourly->monthly TWh aggregation"],
        provenance={"eic_code": eic, "document_type": DOCUMENT_TYPE},
    )


def entsoe_connector(
    country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path,
    **_: Any,
) -> tuple[EndpointVerification, AcquisitionOutcome]:
    token = get_credential(credentials, TOKEN_ENV)
    verification = verify_entsoe(country, token)
    if verification.status != "VERIFIED":
        return verification, AcquisitionOutcome(
            source_id="entsoe", country=country, feature=feature,
            status=verification.status if verification.status in ("AUTH_FAILED", "MAPPING_REQUIRED") else "NOT_VERIFIED",
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_entsoe(country, start, end, token, out_dir)
    return verification, outcome
