"""U.S. Energy Information Administration (EIA) Open Data API v2 Adapter.

Downloads official hourly electricity system demand by regional balancing authority / RTO.
Preserves raw hourly observations, native respondent codes, and provenance.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from provenance import generate_file_sidecar
from source_mapping import get_primary_area_code


def get_eia_hourly_demand(
    country_iso3: str,
    start_year: int,
    end_year: int,
    api_key: str | None = None,
    respondent_code: str = "US48",
) -> dict[str, Any]:
    """
    Fetch hourly electricity demand from U.S. EIA Open Data API v2.

    Route: electricity/rto/region-data
    Parameters:
        frequency: hourly
        data[0]: value
        facets[type][]: D (Demand)
        facets[respondent][]: US48 (or other balancing authority)
    """
    if country_iso3 != "USA":
        return {
            "success": False,
            "data": None,
            "records": 0,
            "message": f"U.S. EIA Open Data publishes United States data only, not {country_iso3}",
            "status_type": "SOURCE_NOT_COVERED",
        }

    key = api_key or os.getenv("EIA_API_KEY")
    if not key:
        return {
            "success": False,
            "data": None,
            "records": 0,
            "message": "U.S. EIA Open Data requires EIA_API_KEY",
            "status_type": "ACCESS_RESTRICTED",
        }

    url = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
    start_str = f"{start_year}-01-01T00"
    end_str = f"{end_year}-12-31T23"

    params = {
        "api_key": key,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[type][]": "D",
        "facets[respondent][]": respondent_code,
        "start": start_str,
        "end": end_str,
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "offset": 0,
        "length": 5000,
    }

    try:
        all_rows = []
        offset = 0
        limit = 5000

        while True:
            params["offset"] = offset
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code == 429:
                return {
                    "success": False,
                    "data": None,
                    "records": 0,
                    "message": "EIA API rate limit exceeded (HTTP 429)",
                    "status_type": "API_ERROR",
                }
            if resp.status_code in [401, 403]:
                return {
                    "success": False,
                    "data": None,
                    "records": 0,
                    "message": "Invalid or unauthorized EIA_API_KEY",
                    "status_type": "ACCESS_RESTRICTED",
                }
            if resp.status_code != 200:
                return {
                    "success": False,
                    "data": None,
                    "records": 0,
                    "message": f"EIA API returned HTTP {resp.status_code}: {resp.text[:150]}",
                    "status_type": "API_ERROR",
                }

            payload = resp.json()
            response_data = payload.get("response", {})
            items = response_data.get("data", [])
            total_available = response_data.get("total", 0)

            if not items:
                break

            for item in items:
                all_rows.append({
                    "iso3": "USA",
                    "respondent": item.get("respondent", respondent_code),
                    "respondent_name": item.get("respondent-name", "US Lower 48 Total"),
                    "period_utc": item.get("period"),
                    "type": item.get("type", "D"),
                    "type_name": item.get("type-name", "Demand"),
                    "value_mwh": item.get("value"),
                    "unit": item.get("value-units", "megawatthours"),
                    "concept": "electricity_demand",
                    "source_variable": "Hourly System Demand",
                    "source": "EIA Open Data",
                    "frequency": "hourly",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                })

            offset += len(items)
            # Break if fetched all or reached safe single-request batch
            if offset >= total_available or len(items) < limit or offset >= 20000:
                break

        if not all_rows:
            return {
                "success": True,
                "data": pd.DataFrame(),
                "records": 0,
                "message": f"EIA reported no demand records for respondent {respondent_code} ({start_year}-{end_year})",
                "status_type": "NO_DATA_AVAILABLE",
            }

        df = pd.DataFrame(all_rows)
        return {
            "success": True,
            "data": df,
            "records": len(df),
            "message": f"{len(df):,} hourly demand observations retrieved from U.S. EIA ({respondent_code})",
            "status_type": "SUCCESS",
        }

    except Exception as exc:
        return {
            "success": False,
            "data": None,
            "records": 0,
            "message": f"EIA download error: {str(exc)[:150]}",
            "status_type": "DOWNLOAD_ERROR",
        }


def save_eia_data(data: pd.DataFrame, output_path: Path, country_iso3: str = "USA") -> None:
    """Save raw EIA demand data preserving exact native schema."""
    if data is None or data.empty:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(output_path, index=False)

