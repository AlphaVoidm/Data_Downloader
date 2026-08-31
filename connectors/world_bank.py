"""World Bank World Development Indicators connector."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from response_validator import validate_response

from .base import (
    EndpointVerification,
    AcquisitionOutcome,
    ConnectorError,
    _HTTP,
    outcome_from_result,
    verification_from_result,
)

BASE = "https://api.worldbank.org/v2/country/{iso3}/indicator/{code}"

FEATURE_INDICATORS: dict[str, tuple[str, str]] = {
    "gdp": ("NY.GDP.MKTP.CD", "current USD"),
    "gdp_growth": ("NY.GDP.MKTP.KD.ZG", "%"),
    "gdp_per_capita": ("NY.GDP.PCAP.CD", "current USD"),
    "inflation_cpi": ("FP.CPI.TOTL.ZG", "%"),
    "total_population": ("SP.POP.TOTL", "count"),
    "population_growth": ("SP.POP.GROW", "%"),
    "urban_population": ("SP.URB.TOTL", "count"),
    "urbanisation_rate": ("SP.URB.TOTL.IN.ZS", "%"),
    "electricity_access": ("EG.ELC.ACCS.ZS", "%"),
    "manufacturing_value_added": ("NV.IND.MANF.ZS", "%"),
    "total_electricity_generation": ("EG.ELC.PROD.KH", "kWh"),
}


def verify_world_bank(country: str, feature: str) -> EndpointVerification:
    code, _ = FEATURE_INDICATORS[feature]
    url = BASE.format(iso3=country, code=code)
    try:
        resp = _HTTP.get(url, params={"format": "json", "per_page": 1, "date": "2020"}, timeout=30)
    except ConnectorError as exc:
        return EndpointVerification(
            source_id="world_bank", country=country, feature=feature, status=str(exc), message=str(exc),
        )
    result = validate_response(resp, expected_format="json", min_records=0)
    return verification_from_result(result, "world_bank", country, feature)


def acquire_world_bank(country: str, feature: str, start_year: int, end_year: int, out_dir: Path) -> AcquisitionOutcome:
    code, unit = FEATURE_INDICATORS[feature]
    url = BASE.format(iso3=country, code=code)
    rows: list[dict[str, Any]] = []
    page = 1
    total_pages = 1
    try:
        while page <= total_pages:
            resp = _HTTP.get(
                url, params={"format": "json", "per_page": 1000, "date": f"{start_year}:{end_year}", "page": page},
                timeout=30,
            )
            result = validate_response(resp, expected_format="json", min_records=0)
            if not result.ok:
                if rows:
                    break
                return outcome_from_result(result, "world_bank", country, feature)
            payload = resp.json()
            if not isinstance(payload, list) or len(payload) < 2:
                break
            meta, data = payload[0], payload[1] or []
            total_pages = meta.get("pages", 1)
            for item in data:
                val = item.get("value")
                if val is None:
                    continue
                rows.append({
                    "iso3": country, "year": int(item["date"]),
                    "indicator": feature, "indicator_code": code,
                    "value": val, "unit": unit, "observed": True,
                })
            if page >= total_pages:
                break
            page += 1
    except ConnectorError as exc:
        if not rows:
            return AcquisitionOutcome(
                source_id="world_bank", country=country, feature=feature,
                status=str(exc), message=str(exc), failure_reason=str(exc),
            )

    if not rows:
        return AcquisitionOutcome(
            source_id="world_bank", country=country, feature=feature, status="NO_RECORDS",
            message=f"World Bank returned no observations for {code}", failure_reason="NO_RECORDS",
        )

    df = pd.DataFrame(rows).sort_values("year")
    out_path = out_dir / "raw" / "socioeconomic" / "indicators" / "worldbank" / f"{country}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Append if multiple indicators already exist for this country.
    if out_path.exists():
        existing = pd.read_csv(out_path)
        existing = existing[existing["indicator"] != feature]
        df = pd.concat([existing, df], ignore_index=True)
    df.to_csv(out_path, index=False)

    return AcquisitionOutcome(
        source_id="world_bank", country=country, feature=feature,
        status="SUCCESS", message=f"{len(rows)} annual observations for {feature}",
        records=len(rows), path=str(out_path), frequency="annual", unit=unit,
        requested_start=str(start_year), requested_end=str(end_year),
        received_start=str(df['year'].min()), received_end=str(df['year'].max()),
        schema_columns=list(df.columns),
        verification_notes=["JSON valid", f"indicator {code}"],
        provenance={"indicator_code": code},
    )


def world_bank_connector(country: str, feature: str, start: int, end: int, credentials: dict[str, str] | None, out_dir: Path, **_: Any):
    verification = verify_world_bank(country, feature)
    if verification.status != "VERIFIED":
        return verification, AcquisitionOutcome(
            source_id="world_bank", country=country, feature=feature,
            status=verification.status if verification.status in ("RATE_LIMITED", "NETWORK_ERROR", "TIMEOUT") else "NOT_VERIFIED",
            message=verification.message, failure_reason=verification.status,
        )
    outcome = acquire_world_bank(country, feature, start, end, out_dir)
    return verification, outcome
