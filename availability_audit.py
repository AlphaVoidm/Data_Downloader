"""HGT-QF availability audit (discovery-only; no downloads, no network).

Produces REPORT A (source coverage) and REPORT C (HGT-QF readiness), plus a
text summary. This is what must pass before bulk acquisition is enabled.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from country_registry import COUNTRY_REGISTRY, get_all_countries, get_country_record
from coverage_engine import (
    ANNUAL_ONLY,
    MONTHLY_PARTIAL,
    MONTHLY_SUFFICIENT,
    SUPPORTED,
    UNAVAILABLE,
    classify_demand,
    resolve_feature,
)
from feature_registry import get_all_features
from readiness import (
    CORE_NOT_READY,
    CORE_PARTIAL,
    CORE_READY,
    evaluate_readiness,
    select_diverse_countries,
)

_TERM_WIDTH = 78


def _hr(char: str = "─", title: str | None = None) -> str:
    line = char * _TERM_WIDTH
    if title:
        pad = _TERM_WIDTH - len(title) - 4
        left = pad // 2
        return char * left + f"  {title}  " + char * (pad - left)
    return line


def build_report_a(
    countries: list[str],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> pd.DataFrame:
    """REPORT A — source coverage (country × feature × candidate sources)."""
    records = []
    for iso3 in countries:
        for feature in get_all_features():
            plan = resolve_feature(feature.concept, iso3, start_year, end_year, credentials)
            src = None
            from source_registry import get_source
            if plan.best_source_id:
                src = get_source(plan.best_source_id)
            records.append({
                "country": get_country_record(iso3).country_name if get_country_record(iso3) else iso3,
                "iso3": iso3,
                "feature": plan.concept,
                "feature_name": plan.name,
                "role": plan.role,
                "candidate_sources": ";".join(plan.candidates_in_order),
                "best_source": plan.best_source_name,
                "frequency": plan.best_frequency,
                "historical_start": (src.historical_start if src else ""),
                "historical_end": (src.historical_end if src else ""),
                "status": plan.best_status,
                "access_type": (src.auth_type if src else ""),
                "endpoint": (src.endpoint if src else ""),
            })
    return pd.DataFrame(records)


def build_report_c(
    countries: list[str],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> pd.DataFrame:
    """REPORT C — HGT-QF country readiness."""
    records = []
    for iso3 in countries:
        r = evaluate_readiness(iso3, start_year, end_year, credentials)
        rec = get_country_record(iso3)
        records.append({
            "country": rec.country_name if rec else iso3,
            "iso3": iso3,
            "region": rec.region if rec else "",
            "demand_status": r["demand_status"],
            "demand_source": r["demand_source"],
            "demand_months": r["demand_months"],
            "climate_status": r["climate_status"],
            "macro_status": r["macro_status"],
            "demographic_status": r["demographic_status"],
            "energy_status": r["energy_status"],
            "structure_status": r["structure_status"],
            "derived_climate_status": r["derived_climate_status"],
            "optional_feature_coverage": r["optional_feature_coverage"],
            "core_readiness": r["core_readiness"],
            "reason": r["reason"],
        })
    return pd.DataFrame(records)


def run_availability_audit(
    countries: list[str] | None = None,
    start_year: int = 2000,
    end_year: int = 2024,
    credentials: dict[str, str] | None = None,
    output_dir: Path | str | None = None,
    max_per_region: int = 6,
) -> dict[str, Any]:
    if countries is None:
        countries = [r.iso3 for r in get_all_countries()]
    countries = sorted({c.strip().upper() for c in countries})

    report_a = build_report_a(countries, start_year, end_year, credentials)
    report_c = build_report_c(countries, start_year, end_year, credentials)

    demand_counts: dict[str, int] = {}
    for iso3 in countries:
        d = classify_demand(iso3, start_year, end_year, credentials)
        demand_counts[d["status"]] = demand_counts.get(d["status"], 0) + 1

    readiness_counts = report_c["core_readiness"].value_counts().to_dict()
    diverse = select_diverse_countries(
        report_c.to_dict(orient="records"), max_per_region=max_per_region
    )

    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "countries_evaluated": len(countries),
        "year_range": {"start": start_year, "end": end_year},
        "demand_summary": {
            MONTHLY_SUFFICIENT: demand_counts.get(MONTHLY_SUFFICIENT, 0),
            MONTHLY_PARTIAL: demand_counts.get(MONTHLY_PARTIAL, 0),
            ANNUAL_ONLY: demand_counts.get(ANNUAL_ONLY, 0),
            UNAVAILABLE: demand_counts.get(UNAVAILABLE, 0),
        },
        "readiness_summary": {
            CORE_READY: readiness_counts.get(CORE_READY, 0),
            CORE_PARTIAL: readiness_counts.get(CORE_PARTIAL, 0),
            CORE_NOT_READY: readiness_counts.get(CORE_NOT_READY, 0),
        },
        "diverse_selection": diverse,
        "notes": [
            "Discovery-only audit; no downloads, no network.",
            "Endpoint verification happens during acquisition (this sandbox has no outbound network).",
            "Per-source country membership (Ember monthly, OWID EV) is configurable and must be reconciled with provider catalogues.",
        ],
    }

    if output_dir is not None:
        out = Path(output_dir)
        meta = out / "metadata"
        meta.mkdir(parents=True, exist_ok=True)
        report_a.to_csv(meta / "report_A_source_coverage.csv", index=False)
        report_c.to_csv(meta / "report_C_readiness.csv", index=False)
        (meta / "availability_audit.json").write_text(
            json.dumps(audit, indent=2, default=str), encoding="utf-8"
        )
    return audit


def render_audit_report(audit: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(_hr("═", "HGT-QF GLOBAL DATA AVAILABILITY AUDIT"))
    lines.append(_hr("═"))
    lines.append(f"  Countries evaluated: {audit['countries_evaluated']}")
    yr = audit["year_range"]
    lines.append(f"  Period: {yr['start']} – {yr['end']}")
    lines.append(_hr("═"))
    lines.append("")
    d = audit["demand_summary"]
    lines.append("  Electricity demand (TARGET):")
    lines.append(f"    MONTHLY_SUFFICIENT : {d.get(MONTHLY_SUFFICIENT, 0)}")
    lines.append(f"    MONTHLY_PARTIAL    : {d.get(MONTHLY_PARTIAL, 0)}")
    lines.append(f"    ANNUAL_ONLY        : {d.get(ANNUAL_ONLY, 0)}")
    lines.append(f"    UNAVAILABLE        : {d.get(UNAVAILABLE, 0)}")
    lines.append("")
    r = audit["readiness_summary"]
    lines.append("  HGT-QF core readiness:")
    lines.append(f"    CORE_READY         : {r.get(CORE_READY, 0)}")
    lines.append(f"    CORE_PARTIAL       : {r.get(CORE_PARTIAL, 0)}")
    lines.append(f"    CORE_NOT_READY     : {r.get(CORE_NOT_READY, 0)}")
    lines.append("")
    lines.append(_hr("═", "GEOGRAPHICALLY DIVERSE SELECTION (CORE_READY/CORE_PARTIAL)"))
    lines.append(_hr("═"))
    if audit["diverse_selection"]:
        lines.append(f"{'ISO3':<6} {'COUNTRY':<22} {'REGION':<14} {'DEMAND':<20} {'READY':<15} {'OPT':>4}")
        lines.append("─" * _TERM_WIDTH)
        for row in audit["diverse_selection"]:
            lines.append(
                f"{row['iso3']:<6} {row['country'][:21]:<22} {row['region'][:13]:<14} "
                f"{row['demand_status']:<20} {row['core_readiness']:<15} {row['optional_feature_coverage']:>4}"
            )
    lines.append("")
    lines.append("  Enable acquisition only for countries marked CORE_READY / MONTHLY_SUFFICIENT.")
    lines.append("")
    return "\n".join(lines)


__all__ = ["build_report_a", "build_report_c", "run_availability_audit", "render_audit_report"]
