"""Directory structure management for raw, harmonized, and model-ready data layers.

Preserves source-native data by domain/source/frequency instead of merging at country level.
"""
from __future__ import annotations

from pathlib import Path


# Domain hierarchy for organizing raw data
DOMAIN_STRUCTURE = {
    "electricity": {
        "demand": {
            "sources": {
                "Ember": {"frequency": "monthly", "file_pattern": "{iso3}.csv"},
                "ESO / NESO": {"frequency": "half-hourly", "file_pattern": "{iso3}.csv"},
                "EIA Open Data": {"frequency": "hourly", "file_pattern": "{iso3}.csv"},
                "ENTSO-E Transparency": {"frequency": "hourly", "file_pattern": "{iso3}.csv"},
                "AEMO": {"frequency": "five-minute", "file_pattern": "{iso3}.csv"},
            }
        },
    },
    "weather": {
        "observations": {
            "sources": {
                "NASA POWER": {"frequency": "daily", "file_pattern": "{iso3}.csv"},
                "ERA5": {"frequency": "hourly", "file_pattern": "{iso3}.csv"},
            }
        },
    },
    "socioeconomic": {
        "indicators": {
            "sources": {
                "World Bank": {"frequency": "annual", "file_pattern": "{iso3}.csv"},
                "IIASA SSP": {"frequency": "annual", "file_pattern": "{iso3}.csv"},
            }
        },
    },
    "calendar": {
        "holidays": {
            "sources": {
                "Nager.Date": {"frequency": "annual", "file_pattern": "{iso3}.csv"},
            }
        },
    },
}


def get_raw_path(root: Path, source_name: str, country_iso3: str, frequency: str | None = None) -> Path:
    """
    Get the standardized path for a source's raw data file.

    Example:
        root/raw/electricity/demand/ember/EGY.csv
        root/raw/weather/observations/nasa_power/EGY.csv
        root/raw/socioeconomic/indicators/worldbank/EGY.csv
    """
    # Map source to domain/category/frequency
    source_map = {
        "Ember": ("electricity", "demand", "ember"),
        "ESO / NESO": ("electricity", "demand", "neso"),
        "EIA Open Data": ("electricity", "demand", "eia"),
        "ENTSO-E Transparency": ("electricity", "demand", "entsoe"),
        "AEMO": ("electricity", "demand", "aemo"),
        "NASA POWER": ("weather", "observations", "nasa_power"),
        "ERA5 / CDS": ("weather", "observations", "era5"),
        "World Bank": ("socioeconomic", "indicators", "worldbank"),
        "IIASA SSP": ("socioeconomic", "scenarios", "iiasa_ssp"),
        "Nager.Date": ("calendar", "holidays", "nager_date"),
    }

    if source_name not in source_map:
        # Fallback for unknown sources
        return root / "raw" / "other" / source_name.lower().replace(" ", "_") / f"{country_iso3}.csv"

    domain, category, source_dir = source_map[source_name]
    return root / "raw" / domain / category / source_dir / f"{country_iso3}.csv"


def ensure_quality_dir(root: Path) -> Path:
    """Ensure quality directory exists."""
    quality_dir = root / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    return quality_dir


def ensure_harmonized_dir(root: Path) -> Path:
    """Ensure harmonized directory exists."""
    harmonized_dir = root / "harmonized"
    harmonized_dir.mkdir(parents=True, exist_ok=True)
    return harmonized_dir


def ensure_model_ready_dir(root: Path) -> Path:
    """Ensure model-ready directory exists."""
    model_ready_dir = root / "model_ready"
    model_ready_dir.mkdir(parents=True, exist_ok=True)
    return model_ready_dir


def get_raw_dir(root: Path) -> Path:
    """Ensure and return raw directory."""
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir
