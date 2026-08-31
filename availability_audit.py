"""HGT-QF Global Data Availability Audit.

Runs the Coverage Engine across the full country set and produces the
pre-download audit the plan requires:

    - text report (console)
    - metadata/coverage_matrix.csv
    - metadata/feature_coverage_summary.csv
    - metadata/source_selection_table.csv
    - metadata/availability_audit.json
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from country_registry import COUNTRY_REGISTRY, get_all_countries
from coverage_engine import (
    ACCESS_REQUIRES_AUTH,
    AVAILABLE,
    PARTIAL_AVAILABLE,
    build_coverage_matrix,
    build_feature_detail,
    build_source_selection_table,
    recommend_countries,
    summarize_demand_counts,
    summarize_feature_counts,
)
from feature_registry import get_all_features

_TERM_WIDTH = 78


def _hr(char: str = "─", title: str | None = None) -> str:
    line = char * _TERM_WIDTH
    if title:
        pad = _TERM_WIDTH - len(title) - 4
        left = pad // 2
        right = pad - left
        return char * left + f"  {title}  " + char * right
    return line


def _fmt_country_list(codes: list[str], width: int = _TERM_WIDTH - 8) -> str:
    lines: list[str] = []
    current = ""
    for code in codes:
        if len(current) + len(code) + 2 > width:
            lines.append(current.rstrip())
            current = ""
        current += code + ", "
    if current:
        lines.append(current.rstrip().rstrip(","))
    return "\n".join(lines)


def run_availability_audit(
    countries: list[str] | None = None,
    start_year: int = 2000,
    end_year: int = 2024,
    credentials: dict[str, str] | None = None,
    output_dir: Path | str | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    """Execute the full audit and write all outputs. Returns a result summary dict."""
    if countries is None:
        countries = [r.iso3 for r in get_all_countries()]
    countries = sorted({c.strip().upper() for c in countries})

    detail_df = build_feature_detail(countries, start_year, end_year, credentials)
    matrix_df = build_coverage_matrix(countries, start_year, end_year, credentials)
    source_table = build_source_selection_table(countries, start_year, end_year, credentials)
    feature_summary = summarize_feature_counts(detail_df)
    demand_summary = summarize_demand_counts(countries, start_year, end_year, credentials)
    recommended = recommend_countries(detail_df, top_n=top_n)

    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "countries_evaluated": len(countries),
        "year_range": {"start": start_year, "end": end_year},
        "feature_summary": feature_summary,
        "demand_summary": demand_summary,
        "recommended_countries": recommended.to_dict(orient="records"),
        "notes": [
            "Coverage is computed deterministically from the source registry; "
            "per-source country membership (e.g. Ember monthly geographies) is "
            "configurable and should be reconciled with provider catalogues.",
            "ACCESS_REQUIRES_AUTH rows mean data exists but a credential is missing.",
        ],
    }

    if output_dir is not None:
        out = Path(output_dir)
        meta = out / "metadata"
        meta.mkdir(parents=True, exist_ok=True)
        matrix_df.reset_index().to_csv(meta / "coverage_matrix.csv", index=False)
        detail_df.to_csv(meta / "feature_coverage_detail.csv", index=False)
        source_table.to_csv(meta / "source_selection_table.csv", index=False)
        pd.DataFrame.from_dict(
            {k: v for k, v in feature_summary.items()}, orient="index"
        ).reset_index().rename(columns={"index": "feature_id"}).to_csv(
            meta / "feature_coverage_summary.csv", index=False
        )
        recommended.to_csv(meta / "recommended_countries.csv", index=False)
        (meta / "availability_audit.json").write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8"
        )

    return audit


def render_audit_report(audit: dict[str, Any]) -> str:
    """Render the audit result as a human-readable text report."""
    lines: list[str] = []
    lines.append("")
    lines.append(_hr("═", "HGT-QF GLOBAL DATA AVAILABILITY AUDIT"))
    lines.append(_hr("═"))
    lines.append(f"  Countries evaluated: {audit['countries_evaluated']}")
    yr = audit["year_range"]
    lines.append(f"  Period: {yr['start']} – {yr['end']}")
    lines.append(_hr("═"))
    lines.append("")
    lines.append(f"{'FEATURE':<40} {'AVAIL':>6} {'FULL':>6} {'AUTH':>6}")
    lines.append("─" * _TERM_WIDTH)
    for fid, s in audit["feature_summary"].items():
        name = s["feature_name"]
        if len(name) > 38:
            name = name[:37] + "…"
        lines.append(
            f"{name:<40} {s['countries_available']:>6} {s['countries_full']:>6} {s['countries_auth_required']:>6}"
        )
    lines.append("─" * _TERM_WIDTH)
    demand = audit["demand_summary"]
    lines.append("")
    lines.append("  Electricity demand breakdown:")
    lines.append(f"    Monthly-capable: {demand['demand_monthly_capable']}")
    lines.append(f"    Annual-only:     {demand['demand_annual_only']}")
    lines.append(f"    Unavailable:     {demand['demand_unavailable']}")
    lines.append("")
    lines.append(_hr("═", "RECOMMENDED COUNTRIES FOR HGT-QF"))
    lines.append(_hr("═"))
    if audit["recommended_countries"]:
        lines.append(f"{'RANK':<5} {'ISO3':<6} {'COUNTRY':<24} {'FEATURES':>9} {'WEIGHT':>7}")
        lines.append("─" * _TERM_WIDTH)
        for i, row in enumerate(audit["recommended_countries"], start=1):
            lines.append(
                f"{i:<5} {row['iso3']:<6} {row['country_name'][:23]:<24} "
                f"{str(row['features_available']) + '/' + str(row['features_total']):>9} "
                f"{row['weighted_score']:>7.3f}"
            )
    lines.append("")
    lines.append("  Note: this audit is deterministic (no downloads). Bulk acquisition")
    lines.append("  should only start after this report is reviewed.")
    lines.append("")
    return "\n".join(lines)


__all__ = ["run_availability_audit", "render_audit_report"]
