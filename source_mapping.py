"""Source-Specific Geographic Area Mapping Registry for HGT-QF.

Resolves canonical ISO-3 country codes into provider-specific area identifiers
(ENTSO-E EIC bidding zones, EIA balancing authorities, AEMO regions, etc.)
and validates capability to avoid conflating missing mappings with unsupported sources.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from country_utils import get_country_coordinates

CONFIG_DIR = Path(__file__).parent / "config"
# Also check project root config/ if local config/ is missing (supports both dev and deployment)
if not (CONFIG_DIR / "source_area_mapping.csv").exists():
    _alt = Path("/home/claude/config")
    if (_alt / "source_area_mapping.csv").exists():
        CONFIG_DIR = _alt
AREA_MAPPING_CSV = CONFIG_DIR / "source_area_mapping.csv"


@dataclass(frozen=True)
class AreaMapping:
    iso3: str
    country_name: str
    source: str
    source_area_code: str
    source_area_name: str
    mapping_type: str
    mapping_source: str
    documentation_url: str
    verified: bool
    verification_date: str
    notes: str


# Regional domain scopes for providers
EUROPEAN_ISO3: set[str] = {
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA",
    "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD",
    "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE", "GBR", "NOR", "CHE",
    "ALB", "BIH", "MNE", "MKD", "SRB", "XKX", "UKR", "MDA", "GEO"
}


def load_source_area_mappings() -> list[AreaMapping]:
    """Load verified source area mappings from config/source_area_mapping.csv."""
    mappings: list[AreaMapping] = []
    if not AREA_MAPPING_CSV.exists():
        return mappings

    with AREA_MAPPING_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mappings.append(AreaMapping(
                iso3=row["iso3"].strip().upper(),
                country_name=row.get("country_name", "").strip(),
                source=row.get("source", "").strip(),
                source_area_code=row.get("source_area_code", "").strip(),
                source_area_name=row.get("source_area_name", "").strip(),
                mapping_type=row.get("mapping_type", "").strip(),
                mapping_source=row.get("mapping_source", "").strip(),
                documentation_url=row.get("documentation_url", "").strip(),
                verified=row.get("verified", "false").strip().lower() == "true",
                verification_date=row.get("verification_date", "").strip(),
                notes=row.get("notes", "").strip(),
            ))
    return mappings


AREA_MAPPINGS = load_source_area_mappings()


def get_mappings_for_country_source(iso3: str, source_name: str) -> list[AreaMapping]:
    """Get all verified area mappings for a given country ISO-3 and source."""
    iso3_clean = iso3.strip().upper()
    return [
        m for m in AREA_MAPPINGS
        if m.iso3 == iso3_clean and m.source.casefold() == source_name.casefold() and m.verified
    ]


def get_primary_area_code(iso3: str, source_name: str) -> str | None:
    """Return the primary verified area identifier for a country and source."""
    mappings = get_mappings_for_country_source(iso3, source_name)
    if mappings:
        return mappings[0].source_area_code
    return None


def is_country_mapped_for_source(iso3: str, source_name: str) -> bool:
    """Check if verified mapping exists for country and source."""
    return len(get_mappings_for_country_source(iso3, source_name)) > 0


def validate_source_capability(iso3: str, source_name: str) -> tuple[str, str]:
    """
    Validate whether a source can legitimately provide data for a given ISO-3.

    Returns:
        (status, message)
        Statuses:
        - 'OK': Ready for query.
        - 'MAPPING_MISSING': Source covers region, but verified area code is not yet registered.
        - 'SOURCE_NOT_COVERED': Source genuinely does not cover this geographic territory.
        - 'RESEARCH_TIER': Source is a research-tier dataset requiring manual bulk batching.
    """
    iso3_clean = iso3.strip().upper()
    s_norm = source_name.strip().lower()

    # 1. Global Open Sources (Inherent ISO-3 Coverage)
    if "world bank" in s_norm or "worldbank" in s_norm:
        return "OK", "Global coverage (200+ countries)"
    if "nager" in s_norm or "holiday" in s_norm:
        return "OK", "Worldwide holiday coverage"
    if "nasa" in s_norm or "power" in s_norm:
        coords = get_country_coordinates(iso3_clean)
        if coords:
            return "OK", f"Centroid coordinates available ({coords[0]:.2f}, {coords[1]:.2f})"
        return "MAPPING_MISSING", f"Geographic centroid coordinates not yet mapped for {iso3_clean}"
    if "ember" in s_norm:
        return "OK", "Global coverage (215+ countries)"

    # 2. National / Single-Country Operators
    if "neso" in s_norm or "eso" in s_norm:
        if iso3_clean == "GBR":
            return "OK", "Great Britain National System Operator"
        return "SOURCE_NOT_COVERED", f"ESO / NESO publishes Great Britain (GBR) data only, not {iso3_clean}"

    if "aemo" in s_norm:
        if iso3_clean == "AUS":
            return "OK", "Australian Energy Market Operator"
        return "SOURCE_NOT_COVERED", f"AEMO publishes Australian (AUS) NEM data only, not {iso3_clean}"

    if "eia" in s_norm:
        if iso3_clean == "USA":
            return "OK", "US Energy Information Administration"
        return "SOURCE_NOT_COVERED", f"U.S. EIA Open Data publishes United States data only, not {iso3_clean}"

    # 3. Regional European Systems (ENTSO-E)
    if "entso" in s_norm:
        if is_country_mapped_for_source(iso3_clean, "ENTSO-E Transparency"):
            code = get_primary_area_code(iso3_clean, "ENTSO-E Transparency")
            return "OK", f"Verified EIC Area Code: {code}"
        elif iso3_clean in EUROPEAN_ISO3:
            return "MAPPING_MISSING", f"Country is in ENTSO-E European perimeter, but specific EIC area code is not yet registered for {iso3_clean}"
        else:
            return "SOURCE_NOT_COVERED", f"ENTSO-E Transparency covers European electricity market areas only, not {iso3_clean}"

    # 4. Research Tier Sources
    if any(term in s_norm for term in ["era5", "cmip6", "iiasa", "gpwv4"]):
        return "RESEARCH_TIER", f"{source_name} is a Research-Tier bulk raster/scenario dataset requiring batch extraction"

    return "SOURCE_NOT_COVERED", f"No verified coverage mapping for {source_name} on {iso3_clean}"

