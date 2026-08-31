"""Component 5 — Acquisition Engine for HGT-QF.

Downloads only from sources that the Coverage Engine has confirmed are available
for a given country x feature x period. Anything else is skipped with a recorded
reason and *no network request*.

Dispatch:
    tabular sources     -> existing verified adapters (Ember, ENTSO-E, EIA, NESO,
                           World Bank, NASA POWER, Nager.Date)
    geospatial sources  -> Component 6 scientific extractor (ERA5 / CMIP6)
    sources with no     -> recorded as ADAPTER_PENDING / RESEARCH_TIER (never
    adapter yet           silently dropped)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from coverage_engine import (
    ACCESS_REQUIRES_AUTH,
    AVAILABLE,
    PARTIAL_AVAILABLE,
    resolve_feature,
)
from directory_structure import get_raw_path
from feature_registry import FEATURE_REGISTRY, get_all_features
from pipeline import ADAPTERS
from scientific_extractor import (
    MODE_COUNTRY_AGGREGATE,
    extract_era5_monthly_country,
)

# Feature concept -> ERA5 variable subset used by the scientific extractor.
CLIMATE_VARIABLE_MAP: dict[str, list[str]] = {
    "temperature": ["temperature"],
    "solar_radiation": ["solar_radiation"],
    "wind_speed": ["wind_speed"],
    "precipitation": ["precipitation"],
}

# Sources registered but without an acquisition adapter yet.
PENDING_SOURCES = {
    "AEMO", "IIASA SSP", "GPWv4", "IEA", "IRENA", "Eurostat", "OWID",
}


@dataclass
class AcquisitionResult:
    country: str
    country_name: str
    feature_id: str
    concept: str
    feature_name: str
    source: str
    status: str
    message: str
    records: int = 0
    path: str = ""
    frequency: str = ""
    dataset_type: str = "tabular"
    start_year: int = 0
    end_year: int = 0
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_url: str = ""
    doi: str = ""
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _normalize_status(status: str) -> str:
    status = (status or "").strip().upper()
    if status in ("SUCCESS",):
        return "SUCCESS"
    if status in ("PARTIAL_SUCCESS", "PARTIAL_AVAILABLE"):
        return "PARTIAL_SUCCESS"
    if status in ("ACCESS_REQUIRES_AUTH", "ACCESS_RESTRICTED"):
        return "ACCESS_REQUIRES_AUTH"
    if status in ("NO_DATA_AVAILABLE", "VARIABLE_NOT_AVAILABLE"):
        return "NO_DATA_AVAILABLE"
    if status in ("MAPPING_MISSING", "SOURCE_NOT_COVERED", "NOT_COVERED"):
        return "NOT_COVERED"
    if status in ("PERIOD_NOT_AVAILABLE",):
        return "PERIOD_NOT_AVAILABLE"
    if status in ("API_ERROR", "DOWNLOAD_ERROR", "PARSE_ERROR", "INVALID_RESPONSE"):
        return "DOWNLOAD_ERROR"
    if status in ("DEPENDENCY_MISSING", "ADAPTER_PENDING", "RESEARCH_TIER"):
        return status
    return status or "UNKNOWN"


def _run_tabular_adapter(
    source: str, country: str, output_dir: Path, start: int, end: int, credentials: dict[str, str] | None
) -> tuple[int, str, str, str]:
    adapter = ADAPTERS.get(source)
    if adapter is None:
        return 0, "ADAPTER_PENDING", f"No acquisition adapter registered for {source}", ""
    frequency = {
        "Ember": "monthly", "ENTSO-E Transparency": "hourly",
        "EIA Open Data": "hourly", "ESO / NESO": "half-hourly",
        "World Bank": "annual", "NASA POWER": "daily", "Nager.Date": "annual",
    }.get(source, "")
    output = get_raw_path(output_dir, source, country, frequency or "unknown")
    records, status, message = adapter(country, output, start, end, credentials)
    return records, status, message, str(output) if records > 0 else ""


def _run_scientific_adapter(
    source: str, concept: str, country: str, output_dir: Path, start: int, end: int,
    credentials: dict[str, str] | None, mode: str,
) -> tuple[int, str, str, str]:
    if source == "ERA5 / CDS":
        variables = CLIMATE_VARIABLE_MAP.get(concept)
        if not variables:
            return 0, "VARIABLE_NOT_AVAILABLE", f"No ERA5 variable mapped for '{concept}'", ""
        result = extract_era5_monthly_country(
            country_iso3=country, variables=variables, start_year=start, end_year=end,
            mode=mode, output_dir=output_dir, credentials=credentials,
        )
        return result.records, result.status, result.message, result.output_path
    if source == "CMIP6 / CDS":
        return 0, "RESEARCH_TIER", "CMIP6 scenario extraction requires SSP selection (future module)", ""
    return 0, "ADAPTER_PENDING", f"No scientific extractor for {source}", ""


def acquire_feature(
    feature_id: str,
    country: str,
    start: int,
    end: int,
    output_dir: Path | str,
    credentials: dict[str, str] | None = None,
    mode: str = MODE_COUNTRY_AGGREGATE,
) -> AcquisitionResult:
    """Acquire one feature for one country, using the coverage engine's selection."""
    feature = FEATURE_REGISTRY.get(feature_id)
    if feature is None:
        raise KeyError(f"Unknown feature id: {feature_id}")

    from country_utils import get_country_name

    cname = get_country_name(country)
    plan = resolve_feature(feature_id, country, start, end, credentials)

    base = AcquisitionResult(
        country=country,
        country_name=cname,
        feature_id=feature_id,
        concept=feature.concept,
        feature_name=feature.feature_name,
        source=plan.best_source,
        status="",
        message="",
        frequency=plan.best_frequency,
        start_year=start,
        end_year=end,
    )

    # Skip anything the coverage engine already knows is unavailable — no HTTP.
    if plan.best_status not in (AVAILABLE, PARTIAL_AVAILABLE):
        base.status = _normalize_status(plan.best_status)
        base.skip_reason = plan.best_status
        base.message = _skip_message(plan)
        return base

    source = plan.best_source
    base.dataset_type = "geospatial" if source in ("ERA5 / CDS", "CMIP6 / CDS") else "tabular"

    if source in ADAPTERS:
        records, status, message, path = _run_tabular_adapter(
            source, country, Path(output_dir), start, end, credentials
        )
    elif source in ("ERA5 / CDS", "CMIP6 / CDS"):
        records, status, message, path = _run_scientific_adapter(
            source, feature.concept, country, Path(output_dir), start, end, credentials, mode
        )
    elif source in PENDING_SOURCES:
        records, status, message, path = 0, "ADAPTER_PENDING", (
            f"{source} is covered by the registry but has no acquisition adapter yet"
        ), ""
    else:
        records, status, message, path = 0, "ADAPTER_PENDING", (
            f"No acquisition path registered for {source}"
        ), ""

    base.status = _normalize_status(status)
    base.message = message
    base.records = records
    base.path = path
    return base


def _skip_message(plan: Any) -> str:
    if plan.best_status == "NOT_COVERED":
        return "Skipped: no registered source covers this country/feature (no HTTP request made)"
    if plan.best_status == "VARIABLE_NOT_AVAILABLE":
        return "Skipped: candidate sources do not publish this variable for this country"
    if plan.best_status == "PERIOD_NOT_AVAILABLE":
        return "Skipped: requested period is outside all candidate source ranges"
    if plan.best_status == "ACCESS_REQUIRES_AUTH":
        return "Skipped: data exists but a credential is required and not provided"
    return f"Skipped: {plan.best_status}"


def run_acquisition(
    countries: list[str],
    start: int,
    end: int,
    output_dir: Path | str,
    credentials: dict[str, str] | None = None,
    feature_ids: list[str] | None = None,
    mode: str = MODE_COUNTRY_AGGREGATE,
    progress: Callable[[str], None] | None = None,
) -> list[AcquisitionResult]:
    """Run the full acquisition across countries x features (coverage-gated)."""
    from country_utils import get_country_name

    ids = feature_ids or [f.feature_id for f in get_all_features()]
    results: list[AcquisitionResult] = []
    for iso3 in countries:
        cname = get_country_name(iso3)
        for fid in ids:
            if progress:
                progress(f"{cname} ({iso3}) | {fid}")
            results.append(acquire_feature(fid, iso3, start, end, output_dir, credentials, mode))
    return results


__all__ = [
    "AcquisitionResult", "acquire_feature", "run_acquisition",
    "CLIMATE_VARIABLE_MAP", "PENDING_SOURCES",
]
