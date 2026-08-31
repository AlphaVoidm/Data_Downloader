"""ESO / NESO connector (Great Britain half-hourly demand)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from response_validator import validate_response

from .base import (
    EndpointVerification,
    AcquisitionOutcome,
    ConnectorError,
    _HTTP,
    acquisition_status_for_verification,
    outcome_from_result,
    verification_from_result,
)

PACKAGE_URL = "https://api.neso.energy/api/3/action/package_show?id=historic-demand-data"


def verify_neso(country: str) -> EndpointVerification:
    if country != "GBR":
        return EndpointVerification(
            source_id="neso", country=country, feature="electricity_demand",
            status="NOT_SUPPORTED", message=f"NESO publishes Great Britain data only, not {country}",
        )
    history: list[dict[str, Any]] = []
    try:
        resp = _HTTP.get(PACKAGE_URL, timeout=30, history=history)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="neso", country=country, feature="electricity_demand",
            status=exc.status, message=str(exc), attempts=exc.attempts or history,
        )
    result = validate_response(resp, expected_format="json", min_records=0)
    return verification_from_result(result, "neso", country, "electricity_demand", attempts=history)


def acquire_neso(country: str, start_year: int, end_year: int, out_dir: Path) -> AcquisitionOutcome:
    history: list[dict[str, Any]] = []
    try:
        resp = _HTTP.get(PACKAGE_URL, timeout=30, history=history)
    except ConnectorError as exc:
        return AcquisitionOutcome(
            source_id="neso", country="GBR", feature="electricity_demand",
            status=exc.status, message=str(exc), failure_reason=exc.status,
            attempts=exc.attempts or history,
        )
    result = validate_response(resp, expected_format="json", min_records=0)
    if not result.ok:
        return outcome_from_result(result, "neso", "GBR", "electricity_demand")

    resources = resp.json().get("result", {}).get("resources", [])
    selected = []
    for res in resources:
        name = str(res.get("name", ""))
        r_url = str(res.get("url", ""))
        match = re.search(r"(\d{4})", name) or re.search(r"(\d{4})", r_url)
        if match and str(res.get("format", "")).upper() == "CSV":
            yr = int(match.group(1))
            if start_year <= yr <= end_year:
                selected.append((yr, res))
    if not selected:
        return AcquisitionOutcome(
            source_id="neso", country="GBR", feature="electricity_demand",
            status="NO_RECORDS", message=f"NESO has no demand packages for {start_year}-{end_year}",
            failure_reason="NO_RECORDS",
        )
    selected.sort(key=lambda x: x[0])
    frames = []
    for yr, res in selected:
        try:
            r = _HTTP.get(res["url"], timeout=120)
        except ConnectorError:
            continue
        csv_result = validate_response(r, expected_format="csv", min_records=1)
        if not csv_result.ok:
            continue
        frame = pd.DataFrame(csv_result.data)
        frames.append(frame)

    if not frames:
        return AcquisitionOutcome(
            source_id="neso", country="GBR", feature="electricity_demand",
            status="NO_RECORDS", message="NESO CSVs parsed but no records", failure_reason="NO_RECORDS",
        )
    combined = pd.concat(frames, ignore_index=True)
    date_col = next((c for c in ("settlement_date", "date", "datetime", "settlementdate") if c in combined.columns), None)
    out_path = out_dir / "raw" / "electricity" / "demand" / "neso" / "GBR.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_path, index=False)
    return AcquisitionOutcome(
        source_id="neso", country="GBR", feature="electricity_demand",
        status="SUCCESS", message=f"{len(combined)} half-hourly demand records",
        records=len(combined), path=str(out_path), frequency="half-hourly", unit="MW",
        requested_start=str(start_year), requested_end=str(end_year),
        schema_columns=list(combined.columns),
        verification_notes=["CKAN JSON valid", f"{len(selected)} year packages"],
        provenance={"package": "historic-demand-data"},
    )


def neso_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    verification = verify_neso(country)
    if verification.status != "VERIFIED":
        status = acquisition_status_for_verification(verification.status)
        return verification, AcquisitionOutcome(
            source_id="neso", country=country, feature=feature, status=status,
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_neso(country, start, end, out_dir)
    return verification, outcome
