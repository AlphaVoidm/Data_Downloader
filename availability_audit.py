"""HGT-QF availability audit (discovery-only; no downloads, no network).

Produces:
    REPORT A — GLOBAL AVAILABILITY / provenance registry
               (country × feature × candidate sources × per-source status)
    REPORT C — three-tier readiness (TARGET_READY / FEATURE_COVERAGE /
               RESEARCH_READY) with configurable thresholds
plus a text summary. This is the global availability map — it must be correct
before acquisition is enabled.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from country_registry import get_all_countries, get_country_record
from coverage_engine import (
    ANNUAL_ONLY,
    MONTHLY_PARTIAL,
    MONTHLY_SUFFICIENT,
    UNAVAILABLE,
    classify_demand,
    resolve_feature,
)
from feature_registry import get_all_features
from readiness import (
    RESEARCH_NOT_READY,
    RESEARCH_READY,
    evaluate_readiness,
    select_diverse_countries,
)
from research_config import ResearchConfig, load_research_config
from source_registry import get_source
from status_vocabulary import source_status

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
    """REPORT A — provenance registry: country × feature × candidate sources.

    One row per (country, feature). For every candidate source the per-source
    SOURCE_* status is recorded, and the best source's metadata (license,
    auth, retrieval method, verification URL) is attached as provenance.
    """
    records = []
    for iso3 in countries:
        rec = get_country_record(iso3)
        for feature in get_all_features():
            plan = resolve_feature(feature.concept, iso3, start_year, end_year, credentials)
            src = get_source(plan.best_source_id) if plan.best_source_id else None

            candidate_statuses = ";".join(
                f"{d.source_id}:{source_status(d.status)}" for d in plan.decisions
            )
            records.append({
                "country": rec.country_name if rec else iso3,
                "iso3": iso3,
                "feature": plan.concept,
                "feature_name": plan.name,
                "tier": feature.tier,
                "candidate_sources": ";".join(plan.candidates_in_order),
                "candidate_source_statuses": candidate_statuses,
                "best_source": plan.best_source_name,
                "availability_status": source_status(plan.best_status),
                "frequency": plan.best_frequency,
                "first_date": (f"{start_year}-01" if plan.best_status != "UNKNOWN" else ""),
                "last_date": (f"{end_year}-12" if plan.best_status != "UNKNOWN" else ""),
                "historical_start": (src.historical_start if src else ""),
                "historical_end": (src.historical_end if src else ""),
                "unit": (src.unit if src else feature.unit),
                "license": (src.license if src else ""),
                "authentication_required": (bool(src.auth_required) if src else False),
                "retrieval_method": (src.access_method if src else ""),
                "verification_url": (src.endpoint if src else ""),
                "documentation_url": (src.documentation_url if src else ""),
                "last_verified": "discovery (deterministic)",
            })
    return pd.DataFrame(records)


def build_report_c(
    countries: list[str],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    config: ResearchConfig | None = None,
) -> pd.DataFrame:
    """REPORT C — three-tier country readiness."""
    cfg = config or load_research_config()
    records = []
    for iso3 in countries:
        r = evaluate_readiness(iso3, start_year, end_year, credentials, cfg)
        rec = get_country_record(iso3)
        records.append({
            "country": rec.country_name if rec else iso3,
            "iso3": iso3,
            "region": rec.region if rec else "",
            "target_status": r["target_status"],
            "target_source": r["target_source"],
            "target_resolution": r["target_resolution"],
            "first_month": r["first_month"],
            "last_month": r["last_month"],
            "expected_months": r["expected_months"],
            "observed_months": r["observed_months"],
            "missing_months": r["missing_months"],
            "longest_continuous_run": r["longest_continuous_run"],
            "gap_count": r["gap_count"],
            "core_coverage": r["core_coverage"],
            "core_ratio": r["core_ratio"],
            "extended_coverage": r["extended_coverage"],
            "optional_coverage": r["optional_coverage"],
            "research_ready": r["research_ready"],
            "reason": r["reason"],
        })
    return pd.DataFrame(records)


def build_feature_summary(
    countries: list[str],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Per-feature availability counts over the CURRENT audit scope.

    The denominator is len(countries) — never the global registry size.
    """
    features = get_all_features()
    total = len(countries)
    counts: dict[str, dict[str, Any]] = {
        f.concept: {"feature": f.concept, "feature_name": f.name, "tier": f.tier,
                    "available": 0, "auth_required": 0, "unavailable": 0, "total": total}
        for f in features
    }
    for iso3 in countries:
        for f in features:
            plan = resolve_feature(f.concept, iso3, start_year, end_year, credentials)
            row = counts[f.concept]
            if plan.best_status == "SUPPORTED":
                row["available"] += 1
            elif plan.best_status == "AUTH_REQUIRED":
                row["auth_required"] += 1
            else:
                row["unavailable"] += 1
    return [counts[f.concept] for f in features]


def run_availability_audit(
    countries: list[str] | None = None,
    start_year: int = 2000,
    end_year: int = 2024,
    credentials: dict[str, str] | None = None,
    output_dir: Path | str | None = None,
    max_per_region: int = 6,
    config: ResearchConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_research_config()
    if countries is None:
        countries = [r.iso3 for r in get_all_countries()]
    countries = sorted({c.strip().upper() for c in countries})

    report_a = build_report_a(countries, start_year, end_year, credentials)
    report_c = build_report_c(countries, start_year, end_year, credentials, cfg)
    feature_summary = build_feature_summary(countries, start_year, end_year, credentials)

    demand_counts: dict[str, int] = {}
    for iso3 in countries:
        d = classify_demand(iso3, start_year, end_year, credentials,
                            min_consecutive_months=cfg.min_consecutive_months)
        demand_counts[d["status"]] = demand_counts.get(d["status"], 0) + 1

    research_counts = report_c["research_ready"].value_counts().to_dict()
    diverse = select_diverse_countries(report_c.to_dict(orient="records"), max_per_region=max_per_region)

    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "countries_evaluated": len(countries),
        "year_range": {"start": start_year, "end": end_year},
        "research_config": cfg.to_dict(),
        "target_summary": {
            MONTHLY_SUFFICIENT: demand_counts.get(MONTHLY_SUFFICIENT, 0),
            MONTHLY_PARTIAL: demand_counts.get(MONTHLY_PARTIAL, 0),
            ANNUAL_ONLY: demand_counts.get(ANNUAL_ONLY, 0),
            UNAVAILABLE: demand_counts.get(UNAVAILABLE, 0),
        },
        "research_summary": {
            RESEARCH_READY: research_counts.get(RESEARCH_READY, 0),
            RESEARCH_NOT_READY: research_counts.get(RESEARCH_NOT_READY, 0),
        },
        "feature_summary": feature_summary,
        "diverse_selection": diverse,
        "notes": [
            "Discovery-only audit; no downloads, no network.",
            "Optional/extended features (prices, EV, AC/heat-pump, sectoral, holidays) never disqualify a country.",
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
        pd.DataFrame(feature_summary).to_csv(meta / "report_feature_summary.csv", index=False)
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
    cfg = audit.get("research_config", {})
    lines.append(f"  Research config: min demand history {cfg.get('min_history_months')} mo, "
                 f"min core coverage {cfg.get('min_core_coverage'):.0%}, "
                 f"optional {'required' if cfg.get('require_optional_features') else 'not required'}")
    lines.append(_hr("═"))
    lines.append("")

    t = audit["target_summary"]
    lines.append("  1. TARGET_READY — electricity demand only:")
    lines.append(f"    MONTHLY_SUFFICIENT : {t.get(MONTHLY_SUFFICIENT, 0)}")
    lines.append(f"    MONTHLY_PARTIAL    : {t.get(MONTHLY_PARTIAL, 0)}")
    lines.append(f"    ANNUAL_ONLY        : {t.get(ANNUAL_ONLY, 0)}")
    lines.append(f"    UNAVAILABLE        : {t.get(UNAVAILABLE, 0)}")
    lines.append("")

    lines.append("  2. FEATURE_COVERAGE — explanatory/contextual features (per feature):")
    denom = audit["countries_evaluated"]
    lines.append(f"    {'FEATURE':<28} {'TIER':<9} {'AVAILABLE':>14} {'AUTH':>6} {'UNAVAIL':>8}")
    lines.append("    " + "─" * 66)
    for f in audit["feature_summary"]:
        lines.append(
            f"    {f['feature'][:27]:<28} {f['tier']:<9} "
            f"{f['available']:>6}/{denom} {f['auth_required']:>6} {f['unavailable']:>8}"
        )
    lines.append("")

    r = audit["research_summary"]
    lines.append("  3. RESEARCH_READY — target + configurable core coverage:")
    lines.append(f"    RESEARCH_READY     : {r.get(RESEARCH_READY, 0)}")
    lines.append(f"    RESEARCH_NOT_READY : {r.get(RESEARCH_NOT_READY, 0)}")
    lines.append("")

    lines.append(_hr("═", "GEOGRAPHICALLY DIVERSE SELECTION (RESEARCH_READY)"))
    lines.append(_hr("═"))
    if audit["diverse_selection"]:
        lines.append(f"{'ISO3':<6} {'COUNTRY':<22} {'REGION':<14} {'DEMAND':<20} {'CORE':>5} {'OPT':>5} {'RESEARCH':<18}")
        lines.append("─" * _TERM_WIDTH)
        for row in audit["diverse_selection"]:
            lines.append(
                f"{row['iso3']:<6} {row['country'][:21]:<22} {row['region'][:13]:<14} "
                f"{row['target_status']:<20} {row['core_coverage']:>5} {row['optional_coverage']:>5} "
                f"{row['research_ready']:<18}"
            )
    lines.append("")
    lines.append("  Acquisition eligibility: TARGET_READY (monthly demand). Optional/extended")
    lines.append("  gaps never block useful data — readiness is reported separately.")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "build_report_a", "build_report_c", "build_feature_summary",
    "run_availability_audit", "render_audit_report",
]
