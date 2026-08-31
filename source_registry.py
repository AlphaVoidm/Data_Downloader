"""Source Coverage Registry for HGT-QF Data Desk.

Maintains verified provider metadata, accessibility rules, native frequencies,
licensing, and country eligibility validation.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent / "config"
if not (CONFIG_DIR / "source_registry.csv").exists():
    _alt = Path("/home/claude/config")
    if (_alt / "source_registry.csv").exists():
        CONFIG_DIR = _alt
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
            )
    return registry


SOURCE_REGISTRY = load_source_registry()


def get_source_metadata(source_name: str) -> SourceMetadata | None:
    """Retrieve verified metadata for a given data source name."""
    return SOURCE_REGISTRY.get(source_name)


def get_all_registered_sources() -> list[SourceMetadata]:
    """Return all registered source specifications."""
    return list(SOURCE_REGISTRY.values())

