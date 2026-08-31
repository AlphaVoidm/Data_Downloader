"""HGT-QF country readiness + geographic diversity (spec §15, §16).

Readiness is NOT a raw feature count. It combines:
    - demand status (MONTHLY_SUFFICIENT / MONTHLY_PARTIAL / ANNUAL_ONLY / UNAVAILABLE)
    - core exogenous coverage (climate / macro / energy-system)
    - optional feature coverage

Classification:
    CORE_READY       verified monthly demand + core groups satisfied
    CORE_PARTIAL     mostly present but one group weak or insufficient history
    CORE_NOT_READY   demand annual-only/unavailable or core gaps
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coverage_engine import (
    ANNUAL_ONLY,
    MONTHLY_PARTIAL,
    MONTHLY_SUFFICIENT,
    UNAVAILABLE,
    classify_demand,
    resolve_country,
    SUPPORTED,
)
from feature_registry import (
    FeatureSpec,
    get_all_features,
    get_optional_features,
)

CORE_READY = "CORE_READY"
CORE_PARTIAL = "CORE_PARTIAL"
CORE_NOT_READY = "CORE_NOT_READY"

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

# Domain groups used for readiness.
CLIMATE_CONCEPTS = {"temperature_2m", "solar_radiation", "wind_speed_10m", "precipitation"}
MACRO_CONCEPTS = {"gdp", "gdp_growth", "gdp_per_capita", "inflation_cpi"}
DEMO_CONCEPTS = {"total_population", "population_growth", "urban_population", "urbanisation_rate"}
ENERGY_CONCEPTS = {"total_electricity_generation", "renewable_generation_share", "generation_mix", "electricity_access"}
STRUCTURE_CONCEPTS = {"manufacturing_value_added"}
DERIVED_CLIMATE = {"cooling_degree_days", "heating_degree_days"}


def _group_coverage(plans: dict[str, Any], concepts: set[str]) -> tuple[int, int]:
    total = 0
    ok = 0
    for c in concepts:
        if c in plans:
            total += 1
            if plans[c].best_status == SUPPORTED:
                ok += 1
    return ok, total


def evaluate_readiness(
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    plans = {p.concept: p for p in resolve_country(country_iso3, start_year, end_year, credentials)}
    demand = classify_demand(country_iso3, start_year, end_year, credentials)

    climate_ok, climate_total = _group_coverage(plans, CLIMATE_CONCEPTS)
    macro_ok, macro_total = _group_coverage(plans, MACRO_CONCEPTS)
    demo_ok, demo_total = _group_coverage(plans, DEMO_CONCEPTS)
    energy_ok, energy_total = _group_coverage(plans, ENERGY_CONCEPTS)
    structure_ok, structure_total = _group_coverage(plans, STRUCTURE_CONCEPTS)
    cdd_ok, cdd_total = _group_coverage(plans, DERIVED_CLIMATE)

    optional_plans = {f.concept: plans[f.concept] for f in get_optional_features() if f.concept in plans}
    optional_total = len(optional_plans)
    optional_ok = sum(1 for p in optional_plans.values() if p.best_status == SUPPORTED)

    demand_status = demand["status"]
    core_group_scores = {
        "climate": (climate_ok, climate_total),
        "macro": (macro_ok, macro_total),
        "demographic": (demo_ok, demo_total),
        "energy_system": (energy_ok, energy_total),
        "economic_structure": (structure_ok, structure_total),
        "derived_climate": (cdd_ok, cdd_total),
    }

    # Readiness logic.
    if demand_status in (UNAVAILABLE, ANNUAL_ONLY):
        readiness = CORE_NOT_READY
        reason = f"Demand is {demand_status.lower().replace('_', ' ')}"
    elif demand_status == MONTHLY_PARTIAL:
        readiness = CORE_PARTIAL
        reason = f"Monthly demand history insufficient ({demand.get('months_available', 0)} months)"
    elif climate_ok < climate_total or macro_ok < macro_total:
        readiness = CORE_PARTIAL
        reason = f"Core exogenous gaps: climate {climate_ok}/{climate_total}, macro {macro_ok}/{macro_total}"
    elif energy_ok < energy_total:
        readiness = CORE_PARTIAL
        reason = f"Core energy-system gaps: {energy_ok}/{energy_total}"
    else:
        readiness = CORE_READY
        reason = "Monthly demand + core exogenous groups verified"

    return {
        "iso3": country_iso3,
        "demand_status": demand_status,
        "demand_source": demand.get("best_monthly_source") or demand.get("annual_source", ""),
        "demand_months": demand.get("months_available", 0),
        "climate_status": f"{climate_ok}/{climate_total}",
        "macro_status": f"{macro_ok}/{macro_total}",
        "demographic_status": f"{demo_ok}/{demo_total}",
        "energy_status": f"{energy_ok}/{energy_total}",
        "structure_status": f"{structure_ok}/{structure_total}",
        "derived_climate_status": f"{cdd_ok}/{cdd_total}",
        "optional_feature_coverage": f"{optional_ok}/{optional_total}",
        "core_readiness": readiness,
        "reason": reason,
        "core_group_scores": core_group_scores,
    }


def classify_demand_status(demand: dict[str, Any]) -> str:
    return demand["status"]


@dataclass
class ReadinessSummary:
    core_ready: int = 0
    core_partial: int = 0
    core_not_ready: int = 0

    def record(self, readiness: str) -> None:
        if readiness == CORE_READY:
            self.core_ready += 1
        elif readiness == CORE_PARTIAL:
            self.core_partial += 1
        else:
            self.core_not_ready += 1


def select_diverse_countries(
    readiness_rows: list[dict[str, Any]],
    max_per_region: int = 6,
    region_priority: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Select a geographically diverse CORE_READY/CORE_PARTIAL set (spec §16).

    Prioritises diversity across regions while respecting actual verified
    monthly demand availability (never includes a country merely for diversity).
    """
    from country_registry import get_country_record

    if region_priority is None:
        region_priority = ["Europe", "Africa", "Middle East", "North America",
                           "Latin America", "South America", "Asia",
                           "Southeast Asia", "Oceania"]

    eligible = [r for r in readiness_rows if r["core_readiness"] in (CORE_READY, CORE_PARTIAL)
                and r["demand_status"] in (MONTHLY_SUFFICIENT, MONTHLY_PARTIAL)]

    # Rank within region: CORE_READY first, then demand months desc.
    def rank_key(r: dict[str, Any]):
        return (0 if r["core_readiness"] == CORE_READY else 1, -r.get("demand_months", 0))

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
    "evaluate_readiness", "select_diverse_countries", "ReadinessSummary",
    "CORE_READY", "CORE_PARTIAL", "CORE_NOT_READY",
]
