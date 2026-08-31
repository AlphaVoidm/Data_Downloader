"""Feature configuration for HGT-QF (redesigned, three-tier).

Loads config/feature_config.json and exposes a tiered feature model:

    tier: target | core | extended | optional

    target    electricity_demand — the forecasting TARGET (fundamental requirement)
    core      core explanatory variables (counted toward RESEARCH_READY threshold)
    extended  valuable contextual variables (tracked, never required)
    optional  coverage-limited variables (prices, EV, AC/heat-pump, holidays)

The role field is preserved for compatibility:
    TARGET | CORE_EXOGENOUS | EXTENDED_EXOGENOUS | OPTIONAL_EXOGENOUS
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"
FEATURE_CONFIG_JSON = CONFIG_DIR / "feature_config.json"

ROLE_TARGET = "TARGET"
ROLE_CORE_EXOGENOUS = "CORE_EXOGENOUS"
ROLE_EXTENDED_EXOGENOUS = "EXTENDED_EXOGENOUS"
ROLE_OPTIONAL_EXOGENOUS = "OPTIONAL_EXOGENOUS"

TIER_TARGET = "target"
TIER_CORE = "core"
TIER_EXTENDED = "extended"
TIER_OPTIONAL = "optional"

_ROLE_TO_TIER = {
    ROLE_TARGET: TIER_TARGET,
    ROLE_CORE_EXOGENOUS: TIER_CORE,
    ROLE_EXTENDED_EXOGENOUS: TIER_EXTENDED,
    ROLE_OPTIONAL_EXOGENOUS: TIER_OPTIONAL,
}


@dataclass(frozen=True)
class FeatureSpec:
    concept: str
    name: str
    domain: str
    frequency: str
    unit: str
    role: str
    sources: tuple[str, ...] = field(default_factory=tuple)
    country_overrides: dict[str, tuple[str, ...]] = field(default_factory=dict)
    min_history_months: int = 0
    min_source_frequency: str = ""
    derived_from: str = ""
    note: str = ""
    demand_priority: bool = False

    @property
    def tier(self) -> str:
        return _ROLE_TO_TIER.get(self.role, TIER_OPTIONAL)

    @property
    def is_target(self) -> bool:
        return self.role == ROLE_TARGET

    @property
    def is_core(self) -> bool:
        return self.role in (ROLE_TARGET, ROLE_CORE_EXOGENOUS)

    @property
    def is_extended(self) -> bool:
        return self.role == ROLE_EXTENDED_EXOGENOUS

    @property
    def is_optional(self) -> bool:
        return self.role == ROLE_OPTIONAL_EXOGENOUS

    @property
    def is_derived(self) -> bool:
        return bool(self.derived_from)

    def ordered_sources(self, country_iso3: str) -> tuple[str, ...]:
        """Return the feature's source priority list for a given country,
        applying any country-specific overrides."""
        iso3 = country_iso3.strip().upper()
        if iso3 in self.country_overrides:
            overridden = list(self.country_overrides[iso3])
            for s in self.sources:
                if s not in overridden:
                    overridden.append(s)
            return tuple(overridden)
        return self.sources


def _load() -> dict[str, FeatureSpec]:
    with FEATURE_CONFIG_JSON.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    registry: dict[str, FeatureSpec] = {}

    def _add(role: str, item: dict) -> None:
        concept = item["concept"]
        overrides = {
            k.upper(): tuple(v)
            for k, v in (item.get("country_overrides") or {}).items()
        }
        registry[concept] = FeatureSpec(
            concept=concept,
            name=item["name"],
            domain=item["domain"],
            frequency=item.get("frequency", "annual"),
            unit=item.get("unit", ""),
            role=role,
            sources=tuple(item.get("sources", [])),
            country_overrides=overrides,
            min_history_months=int(item.get("min_history_months", 0)),
            min_source_frequency=item.get("min_source_frequency", ""),
            derived_from=item.get("derived_from", ""),
            note=item.get("note", ""),
            demand_priority=bool(item.get("demand_priority", False)),
        )

    for item in cfg.get("targets", []):
        _add(ROLE_TARGET, item)
    for item in cfg.get("core_exogenous", []):
        _add(ROLE_CORE_EXOGENOUS, item)
    for item in cfg.get("extended_exogenous", []):
        _add(ROLE_EXTENDED_EXOGENOUS, item)
    for item in cfg.get("optional_exogenous", []):
        _add(ROLE_OPTIONAL_EXOGENOUS, item)
    return registry


FEATURE_REGISTRY = _load()


def get_feature(concept: str) -> FeatureSpec | None:
    return FEATURE_REGISTRY.get(concept.strip().lower())


def get_target_feature() -> FeatureSpec:
    for spec in FEATURE_REGISTRY.values():
        if spec.is_target:
            return spec
    raise KeyError("No TARGET feature configured")


def get_core_features() -> list[FeatureSpec]:
    """Target + core explanatory features."""
    return sorted(
        [f for f in FEATURE_REGISTRY.values() if f.tier in (TIER_TARGET, TIER_CORE)],
        key=lambda f: f.concept,
    )


def get_core_exogenous() -> list[FeatureSpec]:
    """Core explanatory features only (excludes the target)."""
    return sorted(
        [f for f in FEATURE_REGISTRY.values() if f.role == ROLE_CORE_EXOGENOUS],
        key=lambda f: f.concept,
    )


def get_extended_features() -> list[FeatureSpec]:
    return sorted(
        [f for f in FEATURE_REGISTRY.values() if f.role == ROLE_EXTENDED_EXOGENOUS],
        key=lambda f: f.concept,
    )


def get_optional_features() -> list[FeatureSpec]:
    return sorted(
        [f for f in FEATURE_REGISTRY.values() if f.role == ROLE_OPTIONAL_EXOGENOUS],
        key=lambda f: f.concept,
    )


def get_features_by_tier(tier: str) -> list[FeatureSpec]:
    return sorted(
        [f for f in FEATURE_REGISTRY.values() if f.tier == tier],
        key=lambda f: f.concept,
    )


def get_all_features() -> list[FeatureSpec]:
    return sorted(FEATURE_REGISTRY.values(), key=lambda f: f.concept)


__all__ = [
    "FeatureSpec", "FEATURE_REGISTRY", "get_feature", "get_target_feature",
    "get_core_features", "get_core_exogenous", "get_extended_features",
    "get_optional_features", "get_features_by_tier", "get_all_features",
    "ROLE_TARGET", "ROLE_CORE_EXOGENOUS", "ROLE_EXTENDED_EXOGENOUS",
    "ROLE_OPTIONAL_EXOGENOUS", "TIER_TARGET", "TIER_CORE", "TIER_EXTENDED",
    "TIER_OPTIONAL",
]
