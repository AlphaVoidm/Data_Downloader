"""Selection Manager — GUI-driven country/feature/source selection for HGT-QF.

Sits between the GUI and the acquisition pipeline:
    GUI selections  →  Selection Manager  →  Acquisition Planner  →  Source Adapters

Key responsibilities:
    1. Maintain user selections (countries, features, sources, period)
    2. Support Automatic vs Manual source mode
    3. Generate the acquisition plan (what will be downloaded)
    4. Validate selections before download (auth, coverage, period overlap)
    5. Prevent invalid combinations (e.g. wrong country for a source)
    6. Render the download plan preview
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from coverage_engine import (
    SUPPORTED, AUTH_REQUIRED, NOT_SUPPORTED, MAPPING_REQUIRED,
    TEMPORARILY_UNAVAILABLE, UNKNOWN, DISCOVERY_BADGES,
    resolve_feature, classify_demand,
)
from feature_registry import (
    FEATURE_REGISTRY, FeatureSpec, get_all_features,
    get_target_feature, get_core_features, get_extended_features,
    get_optional_features, get_features_by_tier,
    TIER_TARGET, TIER_CORE, TIER_EXTENDED, TIER_OPTIONAL,
    resolve_feature_concept,
)
from source_registry import (
    SOURCE_REGISTRY, get_source, get_all_registered_sources,
    get_sources_for_feature,
)
from country_registry import get_all_countries
from country_utils import get_country_name, normalize_country


# ---------------------------------------------------------------------------
# Source selection modes
# ---------------------------------------------------------------------------

MODE_AUTOMATIC = "automatic"
MODE_MANUAL = "manual"


@dataclass
class SourceSelection:
    """A user-selected (feature, source) pair with resolved metadata."""
    feature_concept: str
    feature_name: str
    feature_tier: str
    source_id: str
    source_name: str
    auth_required: bool
    auth_satisfied: bool
    coverage_status: str
    frequency: str
    period_overlap_months: int
    reason: str = ""
    is_fallback: bool = False


@dataclass
class CountryPlan:
    """The full acquisition plan for one country."""
    iso3: str
    country_name: str
    selections: list[SourceSelection] = field(default_factory=list)
    demand_status: str = ""
    demand_source: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_selections(self) -> int:
        return len(self.selections)

    @property
    def auth_issues(self) -> list[SourceSelection]:
        return [s for s in self.selections if s.auth_required and not s.auth_satisfied]

    @property
    def supported_selections(self) -> list[SourceSelection]:
        return [s for s in self.selections if s.coverage_status == SUPPORTED]

    @property
    def unsupported_selections(self) -> list[SourceSelection]:
        return [s for s in self.selections if s.coverage_status in (NOT_SUPPORTED, TEMPORARILY_UNAVAILABLE)]


@dataclass
class DownloadPlan:
    """Complete acquisition plan across all countries."""
    countries: list[CountryPlan] = field(default_factory=list)
    total_requests: int = 0
    source_mode: str = MODE_AUTOMATIC
    start_year: int = 2000
    end_year: int = 2024

    @property
    def country_count(self) -> int:
        return len(self.countries)

    @property
    def feature_count(self) -> int:
        features = set()
        for cp in self.countries:
            for s in cp.selections:
                features.add(s.feature_concept)
        return len(features)

    @property
    def source_count(self) -> int:
        sources = set()
        for cp in self.countries:
            for s in cp.selections:
                sources.add(s.source_id)
        return len(sources)

    @property
    def supported_count(self) -> int:
        return sum(
            sum(1 for s in cp.selections if s.coverage_status == SUPPORTED)
            for cp in self.countries
        )

    @property
    def auth_issues_count(self) -> int:
        return sum(
            sum(1 for s in cp.selections if s.auth_required and not s.auth_satisfied)
            for cp in self.countries
        )


# ---------------------------------------------------------------------------
# Feature grouping for the GUI
# ---------------------------------------------------------------------------

FEATURE_GROUPS = {
    "TARGET": {
        "label": "Target Variable",
        "tier": TIER_TARGET,
        "description": "The forecasting target (mandatory)",
    },
    "CORE": {
        "label": "Core Explanatory",
        "tier": TIER_CORE,
        "description": "Core research variables (counted toward RESEARCH_READY)",
    },
    "EXTENDED": {
        "label": "Extended Explanatory",
        "tier": TIER_EXTENDED,
        "description": "Valuable contextual variables (tracked, never required)",
    },
    "OPTIONAL": {
        "label": "Optional",
        "tier": TIER_OPTIONAL,
        "description": "Coverage-limited variables (never disqualify a country)",
    },
}


def get_feature_groups() -> dict[str, dict[str, Any]]:
    """Return the feature grouping structure with features populated."""
    groups: dict[str, dict[str, Any]] = {}
    for key, meta in FEATURE_GROUPS.items():
        tier = meta["tier"]
        features = get_features_by_tier(tier)
        groups[key] = {
            **meta,
            "features": [
                {
                    "concept": f.concept,
                    "name": f.name,
                    "domain": f.domain,
                    "frequency": f.frequency,
                    "unit": f.unit,
                    "sources": list(f.sources),
                    "is_target": f.is_target,
                }
                for f in features
            ],
        }
    return groups


# ---------------------------------------------------------------------------
# Source grouping by domain for the GUI
# ---------------------------------------------------------------------------

SOURCE_DOMAINS: dict[str, list[str]] = {
    "Electricity Demand": ["entsoe", "eia", "neso", "aemo", "ember"],
    "Climate": ["nasa_power", "era5"],
    "Socioeconomic": ["world_bank"],
    "Energy System": ["irena", "iea", "eurostat"],
    "Calendar": ["nager"],
    "Scenario / Projection": ["iiasa", "owid"],
    "Gridded Population": ["gpwv4"],
}


def get_source_groups() -> dict[str, list[dict[str, Any]]]:
    """Return sources grouped by domain for the GUI."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for domain, source_ids in SOURCE_DOMAINS.items():
        items = []
        for sid in source_ids:
            src = get_source(sid)
            if src:
                items.append({
                    "source_id": sid,
                    "source_name": src.source_name,
                    "features": list(src.features),
                    "auth_required": src.auth_required,
                    "auth_env": src.auth_env,
                    "frequency": ";".join(src.frequencies),
                    "coverage": src.coverage_description,
                    "historical": f"{src.historical_start}–{src.historical_end}",
                    "access": src.public_access,
                })
        if items:
            groups[domain] = items

    # Catch any sources not in the predefined groups
    assigned = set()
    for ids in SOURCE_DOMAINS.values():
        assigned.update(ids)
    remaining = [s for s in get_all_registered_sources() if s.source_id not in assigned]
    if remaining:
        items = []
        for src in remaining:
            items.append({
                "source_id": src.source_id,
                "source_name": src.source_name,
                "features": list(src.features),
                "auth_required": src.auth_required,
                "auth_env": src.auth_env,
                "frequency": ";".join(src.frequencies),
                "coverage": src.coverage_description,
                "historical": f"{src.historical_start}–{src.historical_end}",
                "access": src.public_access,
            })
        groups["Other"] = items
    return groups


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------

def _resolve_source_for_feature(
    concept: str,
    iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    manual_source_id: str | None = None,
) -> SourceSelection:
    """Resolve which source to use for a feature, either automatically or manually."""
    feature = FEATURE_REGISTRY.get(concept)
    if feature is None:
        return SourceSelection(
            feature_concept=concept, feature_name=concept, feature_tier="unknown",
            source_id="", source_name="(unknown feature)",
            auth_required=False, auth_satisfied=True,
            coverage_status=UNKNOWN, frequency="",
            period_overlap_months=0, reason=f"Feature '{concept}' not in registry",
        )

    if manual_source_id:
        # Manual mode: evaluate only the specified source
        decision_plan = resolve_feature(concept, iso3, start_year, end_year, credentials)
        match = next((d for d in decision_plan.decisions if d.source_id == manual_source_id), None)
        if match:
            return SourceSelection(
                feature_concept=concept, feature_name=feature.name,
                feature_tier=feature.tier,
                source_id=match.source_id, source_name=match.source_name,
                auth_required=match.auth_required, auth_satisfied=match.auth_satisfied,
                coverage_status=match.status, frequency=match.frequency,
                period_overlap_months=match.period_overlap_months, reason=match.reason,
            )
        # Source not in the feature's candidate list
        src = get_source(manual_source_id)
        src_name = src.source_name if src else manual_source_id
        return SourceSelection(
            feature_concept=concept, feature_name=feature.name,
            feature_tier=feature.tier,
            source_id=manual_source_id, source_name=src_name,
            auth_required=False, auth_satisfied=True,
            coverage_status=NOT_SUPPORTED, frequency="",
            period_overlap_months=0,
            reason=f"{src_name} does not publish '{concept}'",
        )

    # Automatic mode: use the coverage engine's resolution
    plan = resolve_feature(concept, iso3, start_year, end_year, credentials)
    if plan.best_source_id:
        best_decision = next(
            (d for d in plan.decisions if d.source_id == plan.best_source_id),
            plan.decisions[0] if plan.decisions else None,
        )
        if best_decision:
            return SourceSelection(
                feature_concept=concept, feature_name=feature.name,
                feature_tier=feature.tier,
                source_id=best_decision.source_id, source_name=best_decision.source_name,
                auth_required=best_decision.auth_required,
                auth_satisfied=best_decision.auth_satisfied,
                coverage_status=best_decision.status,
                frequency=best_decision.frequency,
                period_overlap_months=best_decision.period_overlap_months,
                reason=best_decision.reason,
            )

    # No source found at all
    return SourceSelection(
        feature_concept=concept, feature_name=feature.name,
        feature_tier=feature.tier,
        source_id="", source_name="(no source)",
        auth_required=False, auth_satisfied=True,
        coverage_status=NOT_SUPPORTED, frequency="",
        period_overlap_months=0, reason="No source covers this country/feature",
    )


def build_download_plan(
    countries: list[str],
    features: list[str],
    start_year: int,
    end_year: int,
    source_mode: str = MODE_AUTOMATIC,
    source_overrides: dict[str, str] | None = None,
    credentials: dict[str, str] | None = None,
) -> DownloadPlan:
    """Build the complete download plan.

    Args:
        countries: ISO-3 codes
        features: feature concepts
        start_year: start year
        end_year: end year
        source_mode: "automatic" or "manual"
        source_overrides: {concept: source_id} manual source selections
        credentials: credential dict
    """
    source_overrides = source_overrides or {}
    plan = DownloadPlan(
        source_mode=source_mode,
        start_year=start_year,
        end_year=end_year,
    )

    for iso3 in countries:
        cp = CountryPlan(
            iso3=iso3,
            country_name=get_country_name(iso3),
        )

        # Check demand coverage
        try:
            demand_info = classify_demand(iso3, start_year, end_year, credentials)
            cp.demand_status = demand_info.get("status", UNKNOWN)
            cp.demand_source = demand_info.get("best_monthly_source", "")
        except Exception:
            pass

        for concept in features:
            manual_src = None
            if source_mode == MODE_MANUAL and concept in source_overrides:
                manual_src = source_overrides[concept]

            sel = _resolve_source_for_feature(
                concept, iso3, start_year, end_year, credentials, manual_src
            )
            cp.selections.append(sel)

            if sel.auth_required and not sel.auth_satisfied:
                cp.warnings.append(
                    f"{sel.feature_name}: {sel.source_name} requires auth ({sel.reason})"
                )
            if sel.coverage_status in (NOT_SUPPORTED, TEMPORARILY_UNAVAILABLE):
                cp.warnings.append(
                    f"{sel.feature_name}: {sel.source_name} — {sel.reason}"
                )

        plan.countries.append(cp)

    plan.total_requests = sum(
        1 for cp in plan.countries
        for s in cp.selections
        if s.coverage_status == SUPPORTED
    )
    return plan


def render_plan_preview(plan: DownloadPlan) -> str:
    """Render the download plan as a text preview (for the GUI or CLI)."""
    lines: list[str] = []
    lines.append("")
    lines.append("════" + " DOWNLOAD PLAN " + "════" * 20)
    lines.append(f"  Mode:      {plan.source_mode}")
    lines.append(f"  Period:    {plan.start_year}–{plan.end_year}")
    lines.append(f"  Countries: {plan.country_count}")
    lines.append(f"  Features:  {plan.feature_count}")
    lines.append(f"  Sources:   {plan.source_count}")
    lines.append(f"  Requests:  {plan.total_requests} (supported)")
    lines.append("")

    for cp in plan.countries:
        lines.append(f"  {cp.iso3} ({cp.country_name})")
        if cp.demand_status:
            lines.append(f"    Demand classification: {cp.demand_status}"
                         + (f"  [{cp.demand_source}]" if cp.demand_source else ""))

        for sel in cp.selections:
            status_icon = {
                SUPPORTED: "✓",
                AUTH_REQUIRED: "🔑",
                NOT_SUPPORTED: "✗",
                MAPPING_REQUIRED: "🔵",
                TEMPORARILY_UNAVAILABLE: "⚠",
                UNKNOWN: "?",
            }.get(sel.coverage_status, "?")

            auth_str = ""
            if sel.auth_required:
                auth_str = " 🔑✓" if sel.auth_satisfied else " 🔑✗"

            src_label = sel.source_name if sel.source_name else "(none)"
            lines.append(
                f"    ├─ {sel.feature_name[:35]:<36} → {src_label:<20} "
                f"{status_icon}{auth_str}  {sel.frequency:<10} "
                f"({sel.period_overlap_months}mo overlap)"
            )

        if cp.warnings:
            for w in cp.warnings[:5]:
                lines.append(f"    ⚠ {w}")
        lines.append("")

    lines.append("════" * 25)
    return "\n".join(lines)


def get_available_countries() -> list[dict[str, str]]:
    """Return all registered countries for the GUI selector."""
    countries = []
    for r in get_all_countries():
        countries.append({
            "iso3": r.iso3,
            "name": r.country_name,
            "region": r.region,
        })
    return countries


# ---------------------------------------------------------------------------
# Validation of user selections before download
# ---------------------------------------------------------------------------

def validate_selection(
    countries: list[str],
    features: list[str],
    start_year: int,
    end_year: int,
    source_mode: str = MODE_AUTOMATIC,
    source_overrides: dict[str, str] | None = None,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate the user's selection before download.

    Returns a dict with:
        valid: bool
        errors: list[str]
        warnings: list[str]
        plan: DownloadPlan
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Countries
    if not countries:
        errors.append("No countries selected")
    for iso3 in countries:
        if not normalize_country(iso3):
            errors.append(f"Invalid country: {iso3}")

    # 2. Features
    if not features:
        errors.append("No features selected")
    for concept in features:
        if concept not in FEATURE_REGISTRY:
            try:
                resolve_feature_concept(concept)
            except Exception:
                errors.append(f"Unknown feature: {concept}")

    # 3. Period
    if end_year < start_year:
        errors.append(f"End year ({end_year}) must be ≥ start year ({start_year})")
    if start_year < 1950:
        warnings.append(f"Start year {start_year} may exceed most sources' historical coverage")

    # 4. Build the plan and check
    plan = build_download_plan(
        countries, features, start_year, end_year,
        source_mode, source_overrides, credentials,
    )

    # 5. Check for auth issues
    if plan.auth_issues_count > 0:
        warnings.append(
            f"{plan.auth_issues_count} selection(s) require credentials that are not provided"
        )

    # 6. Check for no supported sources
    if plan.total_requests == 0:
        errors.append("No supported source/country/feature combinations found")

    # 7. Check manual mode overrides
    if source_mode == MODE_MANUAL and source_overrides:
        for concept, sid in source_overrides.items():
            src = get_source(sid)
            if src is None:
                errors.append(f"Manual source '{sid}' for '{concept}' is not registered")
            elif concept not in src.features:
                warnings.append(
                    f"Manual source '{src.source_name}' does not list '{concept}' "
                    f"in its features (may still work)"
                )

    # 8. Target feature should be included
    target = get_target_feature()
    if target.concept not in features:
        warnings.append(
            f"Target feature '{target.name}' not selected — "
            "demand forecasting requires electricity demand data"
        )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "plan": plan,
        "summary": {
            "countries": plan.country_count,
            "features": plan.feature_count,
            "sources": plan.source_count,
            "requests": plan.total_requests,
            "auth_issues": plan.auth_issues_count,
        },
    }


__all__ = [
    "SourceSelection", "CountryPlan", "DownloadPlan",
    "MODE_AUTOMATIC", "MODE_MANUAL",
    "FEATURE_GROUPS", "SOURCE_DOMAINS",
    "get_feature_groups", "get_source_groups",
    "build_download_plan", "render_plan_preview",
    "validate_selection", "get_available_countries",
]
