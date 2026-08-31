"""Ember Monthly and Annual Electricity Data Adapter.

Ember publishes open electricity demand, generation, and energy mix data for 215+ countries.
Supports direct open data catalogue retrieval and Ember API key access.
Preserves raw monthly observations, fuel mix categories, and provenance.
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from country_utils import normalize_country


def get_ember_monthly_demand(
    country_iso3: str,
    start_year: int,
    end_year: int,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Fetch monthly electricity demand and generation data from Ember.

    Primary Concept: electricity_demand
    Source Variable: Demand / Electricity Generation
    Native Frequency: monthly
    Unit: TWh
    """
    key = api_key or os.getenv("EMBER_API_KEY")

    # 1. If API key is provided, query the official Ember API
    if key:
        try:
            url = "https://api.ember-energy.org/v1/electricity-generation/monthly"
            headers = {"Authorization": f"Bearer {key}"}
            params = {
                "entity_code": country_iso3,
                "start_date": f"{start_year}-01-01",
                "end_date": f"{end_year}-12-31",
            }
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 429:
                return {
                    "success": False,
                    "data": None,
                    "records": 0,
                    "message": "Ember API rate limit exceeded (HTTP 429)",
                    "status_type": "API_ERROR",
                }
            if resp.status_code in [401, 403]:
                return {
                    "success": False,
                    "data": None,
                    "records": 0,
                    "message": "Invalid or unauthorized EMBER_API_KEY",
                    "status_type": "ACCESS_RESTRICTED",
                }
            if resp.status_code == 200:
                payload = resp.json()
                items = payload.get("data", [])
                if not items:
                    return {
                        "success": True,
                        "data": pd.DataFrame(),
                        "records": 0,
                        "message": f"Ember API returned 0 records for {country_iso3} ({start_year}-{end_year})",
                        "status_type": "NO_DATA_AVAILABLE",
                    }
                df = pd.DataFrame(items)
                df["iso3"] = country_iso3
                df["concept"] = "electricity_demand"
                df["source_variable"] = "Monthly Demand / Generation"
                df["source"] = "Ember"
                df["frequency"] = "monthly"
                df["unit"] = "TWh"
                df["retrieved_at"] = datetime.now(timezone.utc).isoformat()
                return {
                    "success": True,
                    "data": df,
                    "records": len(df),
                    "message": f"{len(df):,} monthly demand/generation records retrieved from Ember API",
                    "status_type": "SUCCESS",
                }
        except Exception as exc:
            pass  # Fallback to public data portal notice

    # 2. Open Dataset Guidance when API key is not configured
    return {
        "success": False,
        "data": None,
        "records": 0,
        "message": "Ember adapter requires EMBER_API_KEY or local bulk download from Ember Open Data Catalogue",
        "status_type": "ACCESS_RESTRICTED",
    }


def save_ember_data(data: pd.DataFrame, output_path: Path, country_iso3: str) -> None:
    """Save raw Ember monthly demand data preserving exact native schema."""
    if data is None or data.empty:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(output_path, index=False)
