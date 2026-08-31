"""HGT-QF three-tier country readiness (spec).

Three INDEPENDENT concepts, never collapsed into one flag:

    TARGET_READY      electricity-demand availability ONLY
                      (MONTHLY_SUFFICIENT / MONTHLY_PARTIAL / ANNUAL_ONLY / UNAVAILABLE)
    FEATURE_COVERAGE  which explanatory/contextual features exist per country
                      (core / extended / optional — independently counted)
    RESEARCH_READY    TARGET_READY AND configurable minimum core-feature coverage
                      (researcher sets thresholds; optional features never gate)

A country is NEVER disqualified for missing optional/extended features such as
electricity prices, EV stock, AC/heat-pump penetration, or sectoral demand.
"""
from __future__ import annotations

from typing import Any

from coverage_engine import (
    ANNUAL_ONLY,
    MONTHLY_PARTIAL,
    MONTHLY_SUFFICIENT,
    SUPPORTED,
    UNAVAILABLE,
    classify_demand,
    resolve_country,
)
from feature_registry import (
    TIER_CORE,
    TIER_EXTENDED,
    TIER_OPTIONAL,
    FeatureSpec,
    get_all_features,
)
from research_config import ResearchConfig, load_research_config

RESEARCH_READY = "RESEARCH_READY"
RESEARCH_NOT_READY = "RESEARCH_NOT_READY"

# Finer diversity regions (spec §16): Middle East, Southeast Asia and Latin
# America are split out from the six continents for balanced selection.
_GEO_REGION_OVERRIDES: dict[str, str] = {
    # Middle East
    "SAU": "Middle East", "ARE": "Middle East", "IRN": "Middle East",
    "IRQ": "Middle East", "ISR": "Middle East", "JOR": "Middle East",
    "KWT": "Middle East", "OMN": "Middle East", "QAT": "Middle East",
    "LBN": "Middle East", "BHR": "Middle East", "SYR": "Middle East",
    "YEM": "Middle East", "PSE": "Middle East",
    # Southeast Asia
    "IDN": "Southeast Asia", "MYS": "Southeast Asia", "PHL": "Southeast Asia",
    "THA": "Southeast Asia", "VNM": "Southeast Asia", "SGP": "Southeast Asia",
    "BRN": "Southeast Asia", "KHM": "Southeast Asia", "LAO": "Southeast Asia",
    "MMR": "Southeast Asia", "TLS": "Southeast Asia",
    # Latin America (Mexico + Central America + Caribbean)
    "MEX": "Latin America", "CRI": "Latin America", "PAN": "Latin America",
    "GTM": "Latin America", "HND": "Latin America", "SLV": "Latin America",
    "NIC": "Latin America", "CUB": "Latin America", "DOM": "Latin America",
    "HTI": "Latin America", "JAM": "Latin America", "TTO": "Latin America",
}


def diversity_region(iso3: str, fallback: str) -> str:
    """Return the diversity region for a country (finer than the continent)."""
    return _GEO_REGION_OVERRIDES.get(iso3.strip().upper(), fallback)


def _tier_coverage(plans: dict[str, Any], tier: str, features: list[FeatureSpec]) -> dict[str, Any]:
    total = 0
    available = 0
    auth_required = 0
    detail: dict[str, str] = {}
    for f in features:
        plan = plans.get(f.concept)
        if plan is None:
            continue
        total += 1
        if plan.best_status == SUPPORTED:
            available += 1
            detail[f.concept] = "AVAILABLE"
        elif plan.best_status == "AUTH_REQUIRED":
            auth_required += 1
            detail[f.concept] = "AUTH_REQUIRED"
        else:
            detail[f.concept] = plan.best_status
    return {
        "total": total,
        "available": available,
        "auth_required": auth_required,
        "missing": total - available,
        "ratio": (available / total) if total else 0.0,
        "label": f"{available}/{total}",
        "detail": detail,
    }


def evaluate_target_readiness(
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    config: ResearchConfig | None = None,
) -> dict[str, Any]:
    """TARGET_READY — electricity-demand availability ONLY (with evidence)."""
    cfg = config or load_research_config()
    demand = classify_demand(
        country_iso3, start_year, end_year, credentials,
        min_consecutive_months=cfg.min_consecutive_months,
    )
    demand["target_ready"] = demand["status"] in (MONTHLY_SUFFICIENT, MONTHLY_PARTIAL)
    return demand


def evaluate_feature_coverage(
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    """FEATURE_COVERAGE — independent per-tier counts for a country."""
    all_features = get_all_features()
    plans = {p.concept: p for p in resolve_country(country_iso3, start_year, end_year, credentials)}
    core = _tier_coverage(plans, TIER_CORE, [f for f in all_features if f.tier == TIER_CORE])
    extended = _tier_coverage(plans, TIER_EXTENDED, [f for f in all_features if f.tier == TIER_EXTENDED])
    optional = _tier_coverage(plans, TIER_OPTIONAL, [f for f in all_features if f.tier == TIER_OPTIONAL])
    return {
        "iso3": country_iso3,
        "core": core,
        "extended": extended,
        "optional": optional,
        "core_coverage": core["label"],
        "extended_coverage": extended["label"],
        "optional_coverage": optional["label"],
    }


def evaluate_research_readiness(
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    config: ResearchConfig | None = None,
) -> dict[str, Any]:
    """RESEARCH_READY — TARGET_READY AND minimum core feature coverage.

    Configurable via ResearchConfig (min history, min consecutive months,
    min core/extended coverage, optional-feature requirement). Optional and
    extended features are reported but never disqualify a country.
    """
    cfg = config or load_research_config()
    target = evaluate_target_readiness(country_iso3, start_year, end_year, credentials, cfg)
    coverage = evaluate_feature_coverage(country_iso3, start_year, end_year, credentials)

    status = target["status"]
    core = coverage["core"]
    extended = coverage["extended"]
    optional = coverage["optional"]

    reasons: list[str] = []
    ready = True

    if status not in (MONTHLY_SUFFICIENT, MONTHLY_PARTIAL):
        ready = False
        reasons.append(f"target demand is {status}")

    if status == MONTHLY_PARTIAL:
        # insufficient history: only research-ready if explicitly allowed
        ready = False
        reasons.append(
            f"monthly demand history insufficient "
            f"({target.get('longest_continuous_run', 0)} consecutive months "
            f"< {cfg.min_consecutive_months})"
        )

    if core["ratio"] < cfg.min_core_coverage:
        ready = False
        reasons.append(
            f"core coverage {core['label']} below required "
            f"{cfg.min_core_coverage:.0%}"
        )

    if extended["ratio"] < cfg.min_extended_coverage:
        ready = False
        reasons.append(
            f"extended coverage {extended['label']} below required "
            f"{cfg.min_extended_coverage:.0%}"
        )

    if cfg.require_optional_features and optional["missing"] > 0:
        ready = False
        reasons.append(f"optional features required but {optional['missing']} missing")

    return {
        "iso3": country_iso3,
        "target_status": status,
        "target_source": target.get("best_monthly_source") or target.get("annual_source", ""),
        "target_resolution": target.get("resolution", ""),
        "first_month": target.get("first_month", ""),
        "last_month": target.get("last_month", ""),
        "expected_months": target.get("expected_months", 0),
        "observed_months": target.get("observed_months", 0),
        "missing_months": target.get("missing_months", 0),
        "longest_continuous_run": target.get("longest_continuous_run", 0),
        "gap_count": target.get("gap_count", 0),
        "core_coverage": core["label"],
        "core_ratio": round(core["ratio"], 4),
        "extended_coverage": extended["label"],
        "optional_coverage": optional["label"],
        "research_ready": RESEARCH_READY if ready else RESEARCH_NOT_READY,
        "reason": "; ".join(reasons) if reasons else (
            "monthly demand + core feature coverage verified"
        ),
        "core_detail": core["detail"],
        "extended_detail": extended["detail"],
        "optional_detail": optional["detail"],
    }


def evaluate_readiness(
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    config: ResearchConfig | None = None,
) -> dict[str, Any]:
    """Combined three-tier readiness record for a country."""
    return evaluate_research_readiness(country_iso3, start_year, end_year, credentials, config)


def select_diverse_countries(
    readiness_rows: list[dict[str, Any]],
    max_per_region: int = 6,
    region_priority: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Select a geographically diverse RESEARCH_READY set (spec §16).

    Prioritises diversity across regions while respecting actual verified
    monthly demand availability (never includes a country merely for diversity).
    """
    from country_registry import get_country_record

    if region_priority is None:
        region_priority = ["Europe", "Africa", "Middle East", "North America",
                           "Latin America", "South America", "Asia",
                           "Southeast Asia", "Oceania"]

    eligible = [r for r in readiness_rows if r.get("research_ready") == RESEARCH_READY]

    # Rank: longest continuous demand run first, then core coverage.
    def rank_key(r: dict[str, Any]):
        return (-r.get("longest_continuous_run", 0), -r.get("core_ratio", 0))

    eligible.sort(key=rank_key)

    selected: list[dict[str, Any]] = []
    region_count: dict[str, int] = {}
    for r in eligible:
        rec = get_country_record(r["iso3"])
        region = diversity_region(r["iso3"], rec.region if rec else "Other")
        if region_count.get(region, 0) >= max_per_region:
            continue
        r["region"] = region
        selected.append(r)
        region_count[region] = region_count.get(region, 0) + 1
    return selected


__all__ = [
    "evaluate_target_readiness", "evaluate_feature_coverage",
    "evaluate_research_readiness", "evaluate_readiness",
    "select_diverse_countries", "diversity_region",
    "RESEARCH_READY", "RESEARCH_NOT_READY",
]
