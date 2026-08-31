"""Component 2 — Feature Registry for HGT-QF.

Canonical definition of the 25 conceptual research variables, each with:

    feature_id, concept, name, domain, required frequency, unit,
    ordered source candidates (priority), dataset type, availability class.

The *ordered source candidates* implement the plan's source-selection priorities:
for every feature the coverage engine walks the candidates in order and picks the
first one that is AVAILABLE for a given country x feature x frequency x period.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"
if not (CONFIG_DIR / "feature_registry.csv").exists():
    CONFIG_DIR = Path(__file__).parent
FEATURE_REGISTRY_CSV = CONFIG_DIR / "feature_registry.csv"


@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str
    concept: str
    feature_name: str
    domain: str
    required_frequency: str
    unit: str
    dataset_type: str
    source_candidates: tuple[str, ...]
    availability_class: str  # mandatory | optional | experimental
    is_target: bool
    is_derived: bool
    definition: str
    derived_from: str = ""


def _split_candidates(raw: str) -> tuple[str, ...]:
    return tuple(s.strip() for s in (raw or "").split("|") if s.strip())


def load_feature_registry() -> dict[str, FeatureSpec]:
    registry: dict[str, FeatureSpec] = {}
    if not FEATURE_REGISTRY_CSV.exists():
        return registry
    with FEATURE_REGISTRY_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fid = row["feature_id"].strip()
            registry[fid] = FeatureSpec(
                feature_id=fid,
                concept=row.get("concept", "").strip(),
                feature_name=row.get("feature_name", "").strip(),
                domain=row.get("domain", "").strip(),
                required_frequency=row.get("required_frequency", "").strip(),
                unit=row.get("unit", "").strip(),
                dataset_type=row.get("dataset_type", "tabular").strip(),
                source_candidates=_split_candidates(row.get("source_candidates", "")),
                availability_class=row.get("availability_class", "optional").strip(),
                is_target=row.get("is_target", "false").strip().lower() == "true",
                is_derived=row.get("is_derived", "false").strip().lower() == "true",
                definition=row.get("definition", "").strip(),
                derived_from=row.get("derived_from", "").strip(),
            )
    return registry


FEATURE_REGISTRY = load_feature_registry()


def get_feature(feature_id: str) -> FeatureSpec | None:
    return FEATURE_REGISTRY.get(feature_id.strip().upper())


def get_feature_by_concept(concept: str) -> FeatureSpec | None:
    for spec in FEATURE_REGISTRY.values():
        if spec.concept == concept.strip().lower():
            return spec
    return None


def get_all_features() -> list[FeatureSpec]:
    return sorted(FEATURE_REGISTRY.values(), key=lambda f: f.feature_id)


def get_mandatory_features() -> list[FeatureSpec]:
    return [f for f in FEATURE_REGISTRY.values() if f.availability_class == "mandatory"]


def get_optional_features() -> list[FeatureSpec]:
    return [f for f in FEATURE_REGISTRY.values() if f.availability_class == "optional"]


def get_experimental_features() -> list[FeatureSpec]:
    return [f for f in FEATURE_REGISTRY.values() if f.availability_class == "experimental"]


__all__ = [
    "FeatureSpec", "FEATURE_REGISTRY", "get_feature", "get_feature_by_concept",
    "get_all_features", "get_mandatory_features", "get_optional_features",
    "get_experimental_features",
]
