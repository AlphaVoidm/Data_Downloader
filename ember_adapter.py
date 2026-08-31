"""Ember Monthly and Annual Electricity Data Adapter (legacy shim).

Ember publishes open electricity demand, generation, and energy mix data for
215+ countries. This adapter delegates to the modern connector
(``connectors/ember.py``) so the legacy pipeline entry point and the modern
acquisition engine share one correct implementation:

    * entity resolved via /v1/options/{dataset}/{resolution}/{filter_name}
    * queried with ``entity_code`` (ISO-3) + ``YYYY-MM`` monthly dates
    * demand falls back to Ember's open bulk long-format CSV
    * demand is NEVER manufactured from generation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def get_ember_monthly_demand(
    country_iso3: str,
    start_year: int,
    end_year: int,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Fetch monthly electricity demand from Ember.

    Delegates to the modern connector (connectors/ember.py). Returns the legacy
    dict shape: {success, data, records, message, status_type}.
    """
    from connectors.ember import acquire_ember

    outcome = acquire_ember(
        country_iso3, "electricity_demand", start_year, end_year, api_key,
        Path("hgt_qf_data"),
    )
    status_type = {
        "SUCCESS": "SUCCESS",
        "AUTH_FAILED": "ACCESS_RESTRICTED",
        "NO_DATA": "NO_DATA_AVAILABLE",
        "NO_RECORDS": "NO_DATA_AVAILABLE",
        "RATE_LIMITED": "API_ERROR",
        "NETWORK_ERROR": "API_ERROR",
        "TIMEOUT": "API_ERROR",
        "SCHEMA_MISMATCH": "INVALID_RESPONSE",
    }.get(outcome.status, "API_ERROR")
    data = None
    if outcome.status == "SUCCESS" and outcome.path:
        try:
            data = pd.read_csv(outcome.path)
        except Exception:  # noqa: BLE001
            data = pd.DataFrame()
    return {
        "success": outcome.status == "SUCCESS" and data is not None and not data.empty,
        "data": data,
        "records": outcome.records,
        "message": outcome.message,
        "status_type": status_type,
    }


def save_ember_data(data: pd.DataFrame, output_path: Path, country_iso3: str) -> None:
    """Save raw Ember monthly demand data preserving exact native schema."""
    if data is None or data.empty:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(output_path, index=False)
