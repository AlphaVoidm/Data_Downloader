"""Component 3 — Source Coverage Registry for HGT-QF Data Desk.

Maintains verified provider metadata plus the *coverage* attributes required by
the Coverage Engine (Component 4):

    dataset_type          tabular | geospatial
    coverage_scope        global | regional:<x> | national:<iso3>
    coverage_countries    approximate number of geographies covered
    coverage_frequency    native frequency(s)
    auth_required         none | api_key | api_token | cds_credentials
    credential_env        env var holding the credential
    variables             comma-separated concepts this source provides

This is what lets the system answer "which sources actually exist for country X,
feature Y, at frequency Z, over period P" *before* any network request is made.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


def _resolve_config_dir() -> Path:
    """Locate the config directory (config/ preferred, repo root fallback)."""
    root = Path(__file__).parent
    for candidate in (root / "config", root, Path("/home/claude/config")):
        if (candidate / "source_registry.csv").exists():
            return candidate
    return root / "config"


CONFIG_DIR = _resolve_config_dir()
SOURCE_REGISTRY_CSV = CONFIG_DIR / "source_registry.csv"


@dataclass(frozen=True)
class SourceMetadata:
    source: str
    organization: str
    dataset_name: str
    concept: str
    geographic_scope: str
    native_frequency: str
    unit: str
    public_access: str
    api_available: bool
    license: str
    official_url: str
    documentation_url: str
    historical_start: int
    historical_end: int
    academic_relevance: str
    notes: str
    # --- Coverage-engine extensions ---
    dataset_type: str = "tabular"
    coverage_scope: str = "global"
    coverage_countries_approx: str = ""
    coverage_frequency: str = ""
    auth_required: str = "none"
    credential_env: str = ""
    variables: str = ""


def load_source_registry() -> dict[str, SourceMetadata]:
    """Load all registered data source specifications from config/source_registry.csv."""
    registry: dict[str, SourceMetadata] = {}
    if not SOURCE_REGISTRY_CSV.exists():
        return registry

    with SOURCE_REGISTRY_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            s_name = row["source"].strip()
            registry[s_name] = SourceMetadata(
                source=s_name,
                organization=row.get("organization", "").strip(),
                dataset_name=row.get("dataset_name", "").strip(),
                concept=row.get("concept", "").strip(),
                geographic_scope=row.get("geographic_scope", "").strip(),
                native_frequency=row.get("native_frequency", "").strip(),
                unit=row.get("unit", "").strip(),
                public_access=row.get("public_access", "").strip(),
                api_available=row.get("api_available", "false").strip().lower() == "true",
                license=row.get("license", "").strip(),
                official_url=row.get("official_url", "").strip(),
                documentation_url=row.get("documentation_url", "").strip(),
                historical_start=int(row.get("historical_start", 2000) or 2000),
                historical_end=int(row.get("historical_end", 2025) or 2025),
                academic_relevance=row.get("academic_relevance", "").strip(),
                notes=row.get("notes", "").strip(),
                dataset_type=row.get("dataset_type", "tabular").strip(),
                coverage_scope=row.get("coverage_scope", "global").strip(),
                coverage_countries_approx=row.get("coverage_countries_approx", "").strip(),
                coverage_frequency=row.get("coverage_frequency", "").strip(),
                auth_required=row.get("auth_required", "none").strip(),
                credential_env=row.get("credential_env", "").strip(),
                variables=row.get("variables", "").strip(),
            )
    return registry


SOURCE_REGISTRY = load_source_registry()


def get_source_metadata(source_name: str) -> SourceMetadata | None:
    """Retrieve verified metadata for a given data source name."""
    return SOURCE_REGISTRY.get(source_name)


def get_all_registered_sources() -> list[SourceMetadata]:
    """Return all registered source specifications."""
    return list(SOURCE_REGISTRY.values())


def get_sources_for_variable(concept: str) -> list[SourceMetadata]:
    """Return sources that declare they provide a given concept/variable."""
    concept_norm = concept.strip().lower()
    matches = []
    for meta in SOURCE_REGISTRY.values():
        variables = {v.strip().lower() for v in meta.variables.split(",")} if meta.variables else set()
        if concept_norm in variables or meta.concept.strip().lower() == concept_norm:
            matches.append(meta)
    return matches


__all__ = [
    "SourceMetadata", "SOURCE_REGISTRY", "get_source_metadata",
    "get_all_registered_sources", "get_sources_for_variable",
]
