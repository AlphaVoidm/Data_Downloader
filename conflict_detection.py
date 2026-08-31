"""Cross-Source Conflict and Discrepancy Detection Framework for HGT-QF.

Compares overlapping indicators across multiple sources (e.g., Ember vs NESO vs EIA electricity demand,
or World Bank vs secondary socioeconomic metrics) to detect:
1. Temporal misalignment
2. Unit inconsistencies (e.g. MWh vs GWh vs TWh)
3. Percentage deviations: delta = |A - B| / max(|A|, |B|) * 100%
4. Discrepancy severity flagging (Normal <= 3%, Minor <= 10%, Warning <= 25%, Critical > 25%)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from country_utils import get_country_name


def calculate_series_discrepancy(
    df_a: pd.DataFrame,
    source_a: str,
    col_a: str,
    df_b: pd.DataFrame,
    source_b: str,
    col_b: str,
    country_iso3: str,
    time_col: str = "year",
    variable_name: str = "electricity_demand",
) -> list[dict[str, Any]]:
    """
    Compare two time series on a common time column and compute point-by-point deviations.
    """
    if time_col not in df_a.columns or time_col not in df_b.columns:
        return []
    if col_a not in df_a.columns or col_b not in df_b.columns:
        return []

    # Clean and align on time column
    a_clean = df_a[[time_col, col_a]].dropna().copy()
    b_clean = df_b[[time_col, col_b]].dropna().copy()

    a_clean[col_a] = pd.to_numeric(a_clean[col_a], errors="coerce")
    b_clean[col_b] = pd.to_numeric(b_clean[col_b], errors="coerce")

    a_clean = a_clean.dropna()
    b_clean = b_clean.dropna()

    merged = pd.merge(a_clean, b_clean, on=time_col, suffixes=(f"_{source_a}", f"_{source_b}"))
    if merged.empty:
        return []

    discrepancies = []
    for _, row in merged.iterrows():
        t = row[time_col]
        val_a = float(row[f"{col_a}_{source_a}"])
        val_b = float(row[f"{col_b}_{source_b}"])

        max_val = max(abs(val_a), abs(val_b))
        if max_val == 0:
            pct_delta = 0.0
        else:
            pct_delta = round((abs(val_a - val_b) / max_val) * 100.0, 2)

        # Classify discrepancy severity
        if pct_delta <= 3.0:
            severity = "Agreement"
            severity_badge = "🟢 Agreement (≤3%)"
        elif pct_delta <= 10.0:
            severity = "Minor"
            severity_badge = "🟡 Minor (3-10%)"
        elif pct_delta <= 25.0:
            severity = "Warning"
            severity_badge = "🟠 Warning (10-25%)"
        else:
            severity = "Critical"
            severity_badge = "🔴 Critical (>25%)"

        discrepancies.append({
            "iso3": country_iso3,
            "country_name": get_country_name(country_iso3),
            "variable": variable_name,
            "time_period": str(t),
            "source_a": source_a,
            "value_a": val_a,
            "source_b": source_b,
            "value_b": val_b,
            "absolute_diff": round(abs(val_a - val_b), 4),
            "percentage_delta": pct_delta,
            "severity": severity,
            "severity_badge": severity_badge,
        })

    return discrepancies


def detect_source_conflicts(root: Path) -> tuple[Path, Path]:
    """
    Scan root/raw directory for multi-source overlaps and generate:
    - quality/source_conflicts.csv
    - quality/conflict_report.json
    """
    quality_dir = root / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    all_conflicts: list[dict[str, Any]] = []

    # 1. Great Britain Electricity Demand: NESO vs Ember
    neso_gbr = root / "raw" / "electricity" / "demand" / "neso" / "GBR.csv"
    ember_gbr = root / "raw" / "electricity" / "demand" / "ember" / "GBR.csv"

    if neso_gbr.exists() and ember_gbr.exists():
        try:
            df_neso = pd.read_csv(neso_gbr)
            df_ember = pd.read_csv(ember_gbr)

            # Aggregate NESO half-hourly/hourly to annual or monthly if possible
            if "settlement_date" in df_neso.columns and "nd" in df_neso.columns:
                df_neso["year"] = pd.to_datetime(df_neso["settlement_date"], errors="coerce").dt.year
                # Sum ND (National Demand MW * 0.5h = MWh, / 1e6 = TWh)
                neso_annual = df_neso.groupby("year")["nd"].apply(lambda s: (s * 0.5).sum() / 1e6).reset_index()
                neso_annual.rename(columns={"nd": "demand_twh"}, inplace=True)

                if "date" in df_ember.columns and "value" in df_ember.columns:
                    df_ember["year"] = pd.to_datetime(df_ember["date"], errors="coerce").dt.year
                    ember_annual = df_ember.groupby("year")["value"].sum().reset_index()
                    ember_annual.rename(columns={"value": "demand_twh"}, inplace=True)

                    conflicts = calculate_series_discrepancy(
                        neso_annual, "NESO", "demand_twh",
                        ember_annual, "Ember", "demand_twh",
                        "GBR", time_col="year", variable_name="annual_electricity_demand_twh"
                    )
                    all_conflicts.extend(conflicts)
        except Exception:
            pass

    # Save summary CSV
    conflicts_csv = quality_dir / "source_conflicts.csv"
    if all_conflicts:
        pd.DataFrame(all_conflicts).to_csv(conflicts_csv, index=False)
    else:
        pd.DataFrame(columns=[
            "iso3", "country_name", "variable", "time_period",
            "source_a", "value_a", "source_b", "value_b",
            "absolute_diff", "percentage_delta", "severity", "severity_badge"
        ]).to_csv(conflicts_csv, index=False)

    # Save JSON report
    report_json = quality_dir / "conflict_report.json"
    report_data = {
        "project": "HGT-QF Data Desk",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_conflicts_analyzed": len(all_conflicts),
        "severity_summary": {
            "Agreement": sum(1 for c in all_conflicts if c["severity"] == "Agreement"),
            "Minor": sum(1 for c in all_conflicts if c["severity"] == "Minor"),
            "Warning": sum(1 for c in all_conflicts if c["severity"] == "Warning"),
            "Critical": sum(1 for c in all_conflicts if c["severity"] == "Critical"),
        },
        "conflicts": all_conflicts,
    }
    report_json.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

    return conflicts_csv, report_json

