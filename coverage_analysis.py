"""Coverage analysis and quality reporting for electricity demand data.

Generates matrices and reports showing data availability by country, month, and source.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from country_utils import get_country_name


def calculate_demand_coverage(
    data: pd.DataFrame | None,
    country_iso3: str,
    start_month: str,
    end_month: str,
) -> dict[str, Any]:
    """
    Calculate coverage metrics for a country's electricity demand data.

    Args:
        data: DataFrame with 'date' column in YYYY-MM format, or None
        country_iso3: ISO-3 country code
        start_month: YYYY-MM format
        end_month: YYYY-MM format

    Returns:
        dict with coverage metrics
    """
    start_dt = pd.Timestamp(start_month)
    end_dt = pd.Timestamp(end_month)
    expected_months = (end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month) + 1

    country_name = get_country_name(country_iso3)

    if data is None or data.empty:
        return {
            "iso3": country_iso3,
            "country_name": country_name,
            "variable": "electricity_demand",
            "expected_observations": expected_months,
            "actual_observations": 0,
            "coverage_percent": 0.0,
            "first_observation": None,
            "last_observation": None,
            "missing_observations": expected_months,
            "number_of_gaps": 1,
            "longest_gap_months": expected_months,
            "longest_continuous_months": 0,
            "status": "unavailable",
            "status_badge": "⚪ Unavailable (0%)",
            "requested_start": start_month,
            "requested_end": end_month,
        }

    # Ensure date column is datetime
    date_col = next((c for c in ["date", "SettlementDate", "settlement_date"] if c in data.columns), None)
    if date_col:
        data_copy = data.copy()
        data_copy["_parsed_date"] = pd.to_datetime(data_copy[date_col], errors="coerce")
        data_copy = data_copy.dropna(subset=["_parsed_date"])
    else:
        return {
            "iso3": country_iso3,
            "country_name": country_name,
            "variable": "electricity_demand",
            "expected_observations": expected_months,
            "actual_observations": 0,
            "coverage_percent": 0.0,
            "first_observation": None,
            "last_observation": None,
            "missing_observations": expected_months,
            "number_of_gaps": 1,
            "longest_gap_months": expected_months,
            "longest_continuous_months": 0,
            "status": "unavailable",
            "status_badge": "⚪ Unavailable (0%)",
            "requested_start": start_month,
            "requested_end": end_month,
        }

    if data_copy.empty:
        return {
            "iso3": country_iso3,
            "country_name": country_name,
            "variable": "electricity_demand",
            "expected_observations": expected_months,
            "actual_observations": 0,
            "coverage_percent": 0.0,
            "first_observation": None,
            "last_observation": None,
            "missing_observations": expected_months,
            "number_of_gaps": 1,
            "longest_gap_months": expected_months,
            "longest_continuous_months": 0,
            "status": "unavailable",
            "status_badge": "⚪ Unavailable (0%)",
            "requested_start": start_month,
            "requested_end": end_month,
        }

    observed_months = data_copy["_parsed_date"].dt.to_period("M").unique()
    actual_count = len(observed_months)
    missing_count = max(0, expected_months - actual_count)
    coverage = (actual_count / expected_months * 100) if expected_months > 0 else 0

    first_obs = data_copy["_parsed_date"].min().strftime("%Y-%m")
    last_obs = data_copy["_parsed_date"].max().strftime("%Y-%m")

    # Detect gaps
    observed_months_sorted = sorted(observed_months)
    gaps = []
    for i in range(1, len(observed_months_sorted)):
        prev_end = observed_months_sorted[i - 1]
        curr_start = observed_months_sorted[i]
        gap_size = (curr_start.year - prev_end.year) * 12 + (curr_start.month - prev_end.month) - 1
        if gap_size > 0:
            gaps.append(gap_size)

    num_gaps = len(gaps)
    longest_gap = max(gaps) if gaps else 0

    # Calculate longest continuous span
    longest_continuous = 0
    if observed_months_sorted:
        current_span = 1
        for i in range(1, len(observed_months_sorted)):
            prev_end = observed_months_sorted[i - 1]
            curr_start = observed_months_sorted[i]
            gap = (curr_start.year - prev_end.year) * 12 + (curr_start.month - prev_end.month) - 1
            if gap == 0:
                current_span += 1
            else:
                longest_continuous = max(longest_continuous, current_span)
                current_span = 1
        longest_continuous = max(longest_continuous, current_span)

    # Determine status
    if coverage >= 95:
        status = "complete"
        status_badge = "🟢 Complete (≥95%)"
    elif coverage >= 80:
        status = "good"
        status_badge = "🟡 Good (80-94%)"
    elif coverage >= 50:
        status = "partial"
        status_badge = "🟠 Partial (50-79%)"
    elif coverage > 0:
        status = "insufficient"
        status_badge = "🔴 Insufficient (<50%)"
    else:
        status = "unavailable"
        status_badge = "⚪ Unavailable (0%)"

    return {
        "iso3": country_iso3,
        "country_name": country_name,
        "variable": "electricity_demand",
        "expected_observations": expected_months,
        "actual_observations": actual_count,
        "coverage_percent": round(coverage, 2),
        "first_observation": first_obs,
        "last_observation": last_obs,
        "missing_observations": missing_count,
        "number_of_gaps": num_gaps,
        "longest_gap_months": longest_gap,
        "longest_continuous_months": longest_continuous,
        "status": status,
        "status_badge": status_badge,
        "requested_start": start_month,
        "requested_end": end_month,
    }


def generate_coverage_matrix(coverage_list: list[dict[str, Any]], output_path: Path) -> None:
    """Write coverage metrics to CSV."""
    if not coverage_list:
        return

    df = pd.DataFrame(coverage_list)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
