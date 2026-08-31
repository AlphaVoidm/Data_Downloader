"""Centralized, extensible Source Registry for HGT-QF (redesigned).

Loads config/source_registry.json. Each source definition contains everything the
spec requires: source_id, provider, features, supported countries/discovery,
frequencies, historical coverage, authentication, endpoint, dataset identifier,
country mapping mechanism, unit, priority, documentation URL, access method, and
rate-limit information.

New sources can be added by editing the JSON — no downloader code changes needed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"
SOURCE_REGISTRY_JSON = CONFIG_DIR / "source_registry.json"


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    source_name: str
    provider: str
    dataset_name: str
    organization: str
    features: tuple[str, ...]
    coverage_scope: str
    coverage_description: str
    coverage_discovery: str
    frequencies: tuple[str, ...]
    historical_start: int
    historical_end: int
    auth_required: bool
    auth_type: str
    auth_env: str
    auth_param: str
    endpoint: str
    dataset_id: str
    country_mapping: str
    unit: str
    priority: dict[str, int]
    documentation_url: str
    access_method: str
    rate_limit: str
    license: str
    academic_relevance: str
    public_access: str
    api_available: bool
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    acquisition_mode: str = "api_country_query"
    role: str = "primary"

    # Legacy-compatible accessors (used by pipeline.py / provenance.py)
    @property
    def source(self) -> str:
        return self.source_name

    @property
    def concept(self) -> str:
        return ",".join(self.features)

    @property
    def geographic_scope(self) -> str:
        return self.coverage_description

    @property
    def native_frequency(self) -> str:
        return ";".join(self.frequencies)

    @property
    def official_url(self) -> str:
        return self.endpoint.split("/api")[0] if "/api" in self.endpoint else self.endpoint


def _normalize_name(value: str) -> str:
    return value.strip().casefold().replace("_", " ").replace("-", " ")


def load_source_registry() -> dict[str, SourceDefinition]:
    with SOURCE_REGISTRY_JSON.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    registry: dict[str, SourceDefinition] = {}
    for source_id, item in cfg.items():
        if source_id.startswith("_"):
            continue
        auth = item.get("auth", {})
        coverage = item.get("coverage", {})
        historical = item.get("historical", {})
        registry[source_id] = SourceDefinition(
            source_id=source_id,
            source_name=item.get("source_name", source_id),
            provider=item.get("provider", ""),
            dataset_name=item.get("dataset_name", ""),
            organization=item.get("organization", item.get("provider", "")),
            features=tuple(item.get("features", [])),
            coverage_scope=coverage.get("scope", "unknown"),
            coverage_description=coverage.get("description", ""),
            coverage_discovery=coverage.get("discovery", ""),
            frequencies=tuple(item.get("frequencies", [])),
            historical_start=int(historical.get("start", 2000)),
            historical_end=int(historical.get("end", 2025)),
            auth_required=bool(auth.get("required", False)),
            auth_type=auth.get("type", "none"),
            auth_env=auth.get("env", ""),
            auth_param=auth.get("param", ""),
            endpoint=item.get("endpoint", ""),
            dataset_id=item.get("dataset_id", ""),
            country_mapping=item.get("country_mapping", ""),
            unit=item.get("unit", ""),
            priority={k: int(v) for k, v in (item.get("priority") or {}).items()},
            documentation_url=item.get("documentation_url", ""),
            access_method=item.get("access_method", ""),
            rate_limit=item.get("rate_limit", ""),
            license=item.get("license", ""),
            academic_relevance=item.get("academic_relevance", ""),
            public_access=item.get("public_access", ""),
            api_available=bool(item.get("api_available", False)),
            acquisition_mode=item.get("acquisition_mode", "api_country_query"),
            role=item.get("role", "primary"),
            notes=item.get("notes", ""),
            aliases=tuple(item.get("aliases", [])),
        )
    return registry


SOURCE_REGISTRY = load_source_registry()

# Legacy display-name aliases used by the v2 pipeline adapters.
_LEGACY_ALIASES: dict[str, str] = {
    "entsoe": "ENTSO-E Transparency",
    "eia": "EIA Open Data",
    "neso": "ESO / NESO",
    "aemo": "AEMO",
    "ember": "Ember",
    "world_bank": "World Bank",
    "nasa_power": "NASA POWER",
    "era5": "ERA5 / CDS",
    "nager": "Nager.Date",
    "eurostat": "Eurostat",
    "owid": "OWID",
    "irena": "IRENA",
    "iea": "IEA",
}

_ID_BY_NAME: dict[str, str] = {}
for _sid, _src in SOURCE_REGISTRY.items():
    _ID_BY_NAME[_normalize_name(_sid)] = _sid
    _ID_BY_NAME[_normalize_name(_src.source_name)] = _sid
    for _alias in _src.aliases:
        _ID_BY_NAME[_normalize_name(_alias)] = _sid
for _sid, _legacy in _LEGACY_ALIASES.items():
    _ID_BY_NAME.setdefault(_normalize_name(_legacy), _sid)


def _resolve_source_id(name: str) -> str | None:
    return _ID_BY_NAME.get(_normalize_name(name))


def get_source_metadata(source_name: str) -> SourceDefinition | None:
    """Look up a source by source_id, display name, or legacy alias."""
    source_id = _resolve_source_id(source_name)
    if source_id is None:
        return None
    return SOURCE_REGISTRY.get(source_id)


def get_source(source_id: str) -> SourceDefinition | None:
    return SOURCE_REGISTRY.get(source_id) or get_source_metadata(source_id)


def get_all_registered_sources() -> list[SourceDefinition]:
    return list(SOURCE_REGISTRY.values())


# --- Acquisition modes (the three-way source execution model) -----------------
MODE_API_COUNTRY_QUERY = "api_country_query"
MODE_BULK_JOB = "bulk_job"
MODE_RESTRICTED = "restricted"

ACQUISITION_MODES: dict[str, str] = {
    MODE_API_COUNTRY_QUERY: "Query country directly",
    MODE_BULK_JOB: "Submit targeted job/request; download only the extracted result",
    MODE_RESTRICTED: "Report honestly; do not attempt",
}


def get_source_capability_matrix() -> list[dict[str, str]]:
    """The Source Capability Matrix: how each source is supposed to be acquired.

    Columns: source, features, coverage, temporal resolution, spatial
    resolution, authentication, acquisition mode, historical coverage,
    fallback priority, rate limit, expected response, role.
    """
    rows: list[dict[str, str]] = []
    for s in sorted(SOURCE_REGISTRY.values(), key=lambda x: x.source_id):
        rows.append({
            "source_id": s.source_id,
            "source": s.source_name,
            "features": ";".join(s.features) or "(none — scenario only)",
            "country_coverage": s.coverage_scope,
            "temporal_resolution": ";".join(s.frequencies),
            "spatial_resolution": s.coverage_discovery,
            "authentication": s.auth_type,
            "acquisition_mode": s.acquisition_mode,
            "mode_label": ACQUISITION_MODES.get(s.acquisition_mode, s.acquisition_mode),
            "historical_coverage": f"{s.historical_start}–{s.historical_end}",
            "role": s.role,
            "rate_limit": s.rate_limit,
            "expected_response": s.access_method,
        })
    return rows


def get_sources_by_mode(mode: str) -> list[SourceDefinition]:
    return [s for s in SOURCE_REGISTRY.values() if s.acquisition_mode == mode]


def get_sources_for_feature(concept: str) -> list[SourceDefinition]:
    concept_norm = concept.strip().lower()
    return [s for s in SOURCE_REGISTRY.values() if concept_norm in s.features]


def credential_env_for(source_id: str) -> str:
    src = get_source(source_id)
    return src.auth_env if src else ""


__all__ = [
    "SourceDefinition", "SOURCE_REGISTRY", "get_source", "get_source_metadata",
    "get_all_registered_sources", "get_sources_for_feature", "credential_env_for",
    "get_source_capability_matrix", "get_sources_by_mode",
    "MODE_API_COUNTRY_QUERY", "MODE_BULK_JOB", "MODE_RESTRICTED",
    "ACQUISITION_MODES",
]
