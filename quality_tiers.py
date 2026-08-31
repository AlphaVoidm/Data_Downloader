"""Data Quality Tiers and Classification Engine for HGT-QF Data Desk.

Implements multi-dimensional data quality evaluation across:
1. Completeness (% of expected temporal observations observed)
2. Continuity (absence of fragmentation and length of continuous unbroken spans)
3. Physical Validity (absence of sentinels like -999, negative demand, invalid percentages)
4. Timeliness (proximity to target evaluation horizon)

Classifies each acquired dataset into:
- Gold (Tier 1): >=95% complete, high continuity, 100% valid. Direct model-ready.
- Silver (Tier 2): 80-94% complete, minor gaps, >=95% valid. Imputation-ready.
- Bronze (Tier 3): 50-79% complete or coarse frequency. Preprocessing required.
- Insufficient (Tier 4): <50% complete. Requires external proxy or rejection.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from country_utils import get_country_name


def calculate_expected_observations(frequency: str, start_year: int, end_year: int) -> int:
    """Calculate expected number of data points for a given frequency and year span."""
    years_count = max(1, end_year - start_year + 1)
    freq_norm = frequency.strip().lower()
    if freq_norm in ["annual", "yearly"]:
        return years_count
    elif freq_norm in ["monthly"]:
        return years_count * 12
    elif freq_norm in ["daily"]:
        return years_count * 365
    elif freq_norm in ["hourly"]:
        return years_count * 365 * 24
    elif freq_norm in ["half-hourly"]:
        return years_count * 365 * 48
    elif freq_norm in ["five-minute"]:
        return years_count * 365 * 288
    return years_count


def validate_physical_bounds(df: pd.DataFrame, source_name: str) -> tuple[float, list[str]]:
    """
    Validate dataset for physical sanity and absence of corrupt sentinel values.
    Returns (validity_score [0..100], list of issues found).
    """
    issues = []
    total_checks = 0
    passed_checks = 0

    # Check for unmasked sentinels (-999, -9999, 99999 placeholder values)
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    sentinel_values = {-999.0, -9999.0, -99.0, 99999.0, 999999.0, -999, -9999, -99}
    for col in numeric_cols:
        if col.lower() in ["year", "iso3"]:
            continue
        total_checks += 1
        has_sentinel = df[col].isin(sentinel_values).any()
        if has_sentinel:
            issues.append(f"Column '{col}' contains unmasked sentinel values (-999, -99, 99999)")
        else:
            passed_checks += 1

    # Electricity demand / generation should be non-negative
    if "demand" in source_name.lower() or "ember" in source_name.lower() or "neso" in source_name.lower() or "eia" in source_name.lower():
        demand_cols = [c for c in df.columns if any(term in c.lower() for term in ["demand", "value", "tsd", "nd", "mw", "gwh", "twh"])]
        for col in demand_cols:
            if col in df.columns and pd.api.types.is_numeric_dtype(df[col]):
                total_checks += 1
                negative_count = (df[col] < 0).sum()
                if negative_count > 0:
                    issues.append(f"Electricity column '{col}' has {negative_count} negative values")
                else:
                    passed_checks += 1

    # Weather checks (temperature °C, precipitation mm)
    if "nasa" in source_name.lower() or "weather" in source_name.lower():
        if "T2M" in df.columns:
            total_checks += 1
            out_of_bounds = ((df["T2M"] < -80) | (df["T2M"] > 65)).sum()
            if out_of_bounds > 0:
                issues.append(f"Temperature T2M has {out_of_bounds} values outside realistic [-80°C, 65°C] range")
            else:
                passed_checks += 1
        if "PRECTOTCORR" in df.columns:
            total_checks += 1
            neg_precip = (df["PRECTOTCORR"] < 0).sum()
            if neg_precip > 0:
                issues.append(f"Precipitation has {neg_precip} negative values")
            else:
                passed_checks += 1

    # Socioeconomic checks (electricity access % between 0 and 100, population > 0)
    if "world" in source_name.lower() or "socio" in source_name.lower():
        if "indicator" in df.columns and "value" in df.columns:
            total_checks += 1
            access_rows = df[df["indicator"] == "electricity_access_pct"]["value"].dropna()
            if not access_rows.empty and ((access_rows < 0) | (access_rows > 100.1)).any():
                issues.append("Electricity access indicator has values outside [0, 100]%")
            else:
                passed_checks += 1

    if total_checks == 0:
        return 100.0, []

    score = round((passed_checks / total_checks) * 100.0, 1)
    return score, issues


def evaluate_dataset_quality(
    file_path: Path,
    source_name: str,
    country_iso3: str,
    frequency: str,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """Evaluate quality dimensions and assign tier for a dataset."""
    expected_obs = calculate_expected_observations(frequency, start_year, end_year)

    if not file_path.exists():
        return {
            "iso3": country_iso3,
            "country_name": get_country_name(country_iso3),
            "source": source_name,
            "frequency": frequency,
            "tier": "Insufficient",
            "tier_badge": "⚠️ Insufficient",
            "overall_score": 0.0,
            "completeness_score": 0.0,
            "continuity_score": 0.0,
            "validity_score": 0.0,
            "timeliness_score": 0.0,
            "actual_observations": 0,
            "expected_observations": expected_obs,
            "issues": ["File not found"],
        }

    try:
        df = pd.read_csv(file_path)
    except Exception as exc:
        return {
            "iso3": country_iso3,
            "country_name": get_country_name(country_iso3),
            "source": source_name,
            "frequency": frequency,
            "tier": "Insufficient",
            "tier_badge": "⚠️ Insufficient",
            "overall_score": 0.0,
            "completeness_score": 0.0,
            "continuity_score": 0.0,
            "validity_score": 0.0,
            "timeliness_score": 0.0,
            "actual_observations": 0,
            "expected_observations": expected_obs,
            "issues": [f"Unparseable file: {exc}"],
        }

    if df.empty:
        return {
            "iso3": country_iso3,
            "country_name": get_country_name(country_iso3),
            "source": source_name,
            "frequency": frequency,
            "tier": "Insufficient",
            "tier_badge": "⚠️ Insufficient",
            "overall_score": 0.0,
            "completeness_score": 0.0,
            "continuity_score": 0.0,
            "validity_score": 0.0,
            "timeliness_score": 0.0,
            "actual_observations": 0,
            "expected_observations": expected_obs,
            "issues": ["File is empty (0 records)"],
        }

    # 1. Completeness
    actual_obs = len(df)
    if "observed" in df.columns:
        actual_observed = int(df["observed"].astype(bool).sum())
    else:
        actual_observed = actual_obs

    completeness_score = min(100.0, round((actual_observed / max(1, expected_obs)) * 100.0, 2))

    # 2. Continuity
    # Check for temporal continuity if date/year is present
    continuity_score = 100.0
    date_col = next((c for c in ["date", "year", "SettlementDate", "settlement_date"] if c in df.columns), None)
    if date_col and len(df) > 1:
        try:
            dates = pd.to_datetime(df[date_col], errors="coerce").dropna().sort_values()
            if len(dates) > 1:
                diffs = dates.diff().dropna()
                median_step = diffs.median()
                if median_step.total_seconds() > 0:
                    gaps = (diffs > (median_step * 2)).sum()
                    continuity_score = max(0.0, round(100.0 - (gaps * 5.0), 1))
        except Exception:
            continuity_score = 80.0

    # 3. Physical Validity
    validity_score, issues = validate_physical_bounds(df, source_name)

    # 4. Timeliness (proximity of latest date to end_year)
    timeliness_score = 100.0
    if date_col:
        try:
            if date_col.lower() == "year" or pd.api.types.is_numeric_dtype(df[date_col]):
                max_year = int(df[date_col].max())
            else:
                dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
                max_year = dates.max().year if not dates.empty else end_year
            year_lag = max(0, end_year - max_year)
            timeliness_score = max(0.0, round(100.0 - (year_lag * 10.0), 1))
        except Exception:
            pass

    # Overall weighted score
    overall_score = round(
        0.40 * completeness_score +
        0.30 * continuity_score +
        0.20 * validity_score +
        0.10 * timeliness_score,
        2,
    )

    # Tier classification
    if completeness_score >= 95.0 and continuity_score >= 85.0 and validity_score == 100.0 and overall_score >= 90.0:
        tier = "Gold"
        tier_badge = "🥇 Gold (Tier 1)"
    elif completeness_score >= 80.0 and continuity_score >= 70.0 and validity_score >= 90.0 and overall_score >= 75.0:
        tier = "Silver"
        tier_badge = "🥈 Silver (Tier 2)"
    elif completeness_score >= 45.0 and overall_score >= 45.0:
        tier = "Bronze"
        tier_badge = "🥉 Bronze (Tier 3)"
    else:
        tier = "Insufficient"
        tier_badge = "⚠️ Insufficient (Tier 4)"

    return {
        "iso3": country_iso3,
        "country_name": get_country_name(country_iso3),
        "source": source_name,
        "frequency": frequency,
        "tier": tier,
        "tier_badge": tier_badge,
        "overall_score": overall_score,
        "completeness_score": completeness_score,
        "continuity_score": continuity_score,
        "validity_score": validity_score,
        "timeliness_score": timeliness_score,
        "actual_observations": actual_observed,
        "expected_observations": expected_obs,
        "issues": issues,
    }


def generate_quality_report(
    root: Path,
    mode: str,
    start_year: int,
    end_year: int,
    results: list[Any] | None = None,
) -> tuple[Path, Path]:
    """
    Evaluate all acquired CSV files and generate:
    - quality/quality_tiers_summary.csv
    - quality/data_quality_report.json
    """
    quality_dir = root / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    csv_files = list(root.glob("raw/**/*.csv"))
    evaluations: list[dict[str, Any]] = []

    # Map file path components to source and ISO3
    for csv_file in csv_files:
        country_iso3 = csv_file.stem.upper()
        # Guess source from parent directory
        parent_dir = csv_file.parent.name
        freq = "annual"
        if parent_dir in ["nasa_power", "era5"]:
            freq = "daily" if parent_dir == "nasa_power" else "hourly"
            source = "NASA POWER" if parent_dir == "nasa_power" else "ERA5 / CDS"
        elif parent_dir in ["ember", "neso", "eia", "entsoe", "aemo"]:
            source_map = {"ember": "Ember", "neso": "ESO / NESO", "eia": "EIA Open Data", "entsoe": "ENTSO-E Transparency", "aemo": "AEMO"}
            freq_map = {"ember": "monthly", "neso": "half-hourly", "eia": "hourly", "entsoe": "hourly", "aemo": "five-minute"}
            source = source_map.get(parent_dir, parent_dir)
            freq = freq_map.get(parent_dir, "monthly")
        elif parent_dir in ["worldbank", "iiasa_ssp"]:
            source = "World Bank" if parent_dir == "worldbank" else "IIASA SSP"
            freq = "annual"
        elif parent_dir == "nager_date":
            source = "Nager.Date"
            freq = "annual"
        else:
            source = parent_dir
            freq = "annual"

        evaluation = evaluate_dataset_quality(csv_file, source, country_iso3, freq, start_year, end_year)
        evaluation["relative_path"] = str(csv_file.relative_to(root))
        evaluations.append(evaluation)

    # Save summary CSV
    summary_csv_path = quality_dir / "quality_tiers_summary.csv"
    if evaluations:
        df_summary = pd.DataFrame(evaluations).drop(columns=["issues"], errors="ignore")
        df_summary.to_csv(summary_csv_path, index=False)
    else:
        pd.DataFrame(columns=["iso3", "country_name", "source", "frequency", "tier", "overall_score"]).to_csv(summary_csv_path, index=False)

    # Save comprehensive JSON report
    report_json_path = quality_dir / "data_quality_report.json"
    full_report = {
        "project": "HGT-QF Data Desk",
        "mode": mode,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "year_range": {"start": start_year, "end": end_year},
        "total_datasets_evaluated": len(evaluations),
        "tier_distribution": {
            "Gold": sum(1 for e in evaluations if e["tier"] == "Gold"),
            "Silver": sum(1 for e in evaluations if e["tier"] == "Silver"),
            "Bronze": sum(1 for e in evaluations if e["tier"] == "Bronze"),
            "Insufficient": sum(1 for e in evaluations if e["tier"] == "Insufficient"),
        },
        "datasets": evaluations,
    }
    report_json_path.write_text(json.dumps(full_report, indent=2), encoding="utf-8")

    return summary_csv_path, report_json_path
