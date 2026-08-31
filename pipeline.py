"""Acquisition and Source-Verification Engine for HGT-QF (Final Spec).

Architecture:
- Preserves raw source-native responses in domain/category/source/ISO3.csv structure
- Pre-flight capability validation consulting source coverage & area mapping registries
- Strict status taxonomy (SUCCESS, PARTIAL_SUCCESS, MAPPING_MISSING, SOURCE_NOT_COVERED,
  NO_DATA_AVAILABLE, ACCESS_RESTRICTED, API_ERROR, DOWNLOAD_ERROR, INVALID_RESPONSE)
- Raw data remains 100% unmodified; sentinel values are audited in metadata, not overwritten
- Complete multi-dimensional inventory generation across 25 conceptual research variables
"""
from __future__ import annotations

import io
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests

from conflict_detection import detect_source_conflicts
from country_utils import (
    get_country_coordinates,
    get_country_name,
    normalize_country,
)
from coverage_analysis import (
    calculate_demand_coverage,
    generate_coverage_matrix,
)
from directory_structure import (
    ensure_harmonized_dir,
    ensure_model_ready_dir,
    ensure_quality_dir,
    get_raw_path,
)
from eia_adapter import get_eia_hourly_demand, save_eia_data
from ember_adapter import get_ember_monthly_demand, save_ember_data
from entsoe_adapter import get_entsoe_total_load, save_entsoe_data
from inventory_engine import generate_all_inventory_reports
from provenance import (
    generate_dataset_manifest,
    generate_file_sidecar,
)
from quality_tiers import generate_quality_report
from source_mapping import (
    get_primary_area_code,
    validate_source_capability,
)
from source_registry import get_source_metadata


# ============================================================================
# Dataclasses & Status Badges
# ============================================================================

@dataclass(frozen=True)
class SourceSpec:
    name: str
    mode: str
    frequency: str
    indicator: str
    access: str
    url: str
    credential: str = ""
    description: str = ""


@dataclass
class SourceResult:
    country: str
    country_name: str
    source: str
    mode: str
    status: str  # SUCCESS, PARTIAL_SUCCESS, MAPPING_MISSING, SOURCE_NOT_COVERED, NO_DATA_AVAILABLE, ACCESS_RESTRICTED, API_ERROR, DOWNLOAD_ERROR, INVALID_RESPONSE
    status_badge: str
    message: str
    records: int = 0
    raw_path: str = ""
    retrieved_at: str = ""
    url: str = ""


STATUS_BADGES: dict[str, str] = {
    "SUCCESS": "🟢 SUCCESS",
    "PARTIAL_SUCCESS": "🟡 PARTIAL_SUCCESS",
    "MAPPING_MISSING": "🔵 MAPPING_MISSING",
    "SOURCE_NOT_COVERED": "⚪ SOURCE_NOT_COVERED",
    "NO_DATA_AVAILABLE": "⚪ NO_DATA_AVAILABLE",
    "ACCESS_RESTRICTED": "🔑 ACCESS_RESTRICTED",
    "API_ERROR": "🟠 API_ERROR",
    "DOWNLOAD_ERROR": "❌ DOWNLOAD_ERROR",
    "INVALID_RESPONSE": "🔴 INVALID_RESPONSE",
    "COVERAGE_UNVERIFIED": "❓ COVERAGE_UNVERIFIED",
}

SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        name="Ember",
        mode="long-term",
        frequency="monthly",
        indicator="electricity_demand",
        access="open",
        url="https://ember-energy.org/data/",
        credential="EMBER_API_KEY",
        description="Global monthly and annual electricity demand, generation, and energy mix (215+ countries).",
    ),
    # Ember also runs in short-term mode as the universal fallback for countries
    # that have no high-frequency (hourly/sub-hourly) demand source (e.g. Africa, Asia).
    SourceSpec(
        name="Ember",
        mode="short-term",
        frequency="monthly",
        indicator="electricity_demand",
        access="open",
        url="https://ember-energy.org/data/",
        credential="EMBER_API_KEY",
        description="Global monthly electricity demand fallback for countries without hourly sources (215+ countries).",
    ),
    SourceSpec(
        name="ENTSO-E Transparency",
        mode="short-term",
        frequency="hourly",
        indicator="electricity_demand",
        access="api",
        url="https://web-api.tp.entsoe.eu/api",
        credential="ENTSOE_API_TOKEN",
        description="European actual total load [6.1.A] across 35+ bidding zones via EIC codes.",
    ),
    SourceSpec(
        name="EIA Open Data",
        mode="short-term",
        frequency="hourly",
        indicator="electricity_demand",
        access="api",
        url="https://api.eia.gov/v2/electricity/rto/region-data/data/",
        credential="EIA_API_KEY",
        description="US federal hourly regional balancing authority electricity demand.",
    ),
    SourceSpec(
        name="ESO / NESO",
        mode="short-term",
        frequency="half-hourly",
        indicator="electricity_demand",
        access="open",
        url="https://api.neso.energy/api/3/action/package_search",
        description="National Energy System Operator (UK) historic half-hourly electricity demand.",
    ),
    SourceSpec(
        name="AEMO",
        mode="short-term",
        frequency="five-minute",
        indicator="electricity_demand",
        access="open",
        url="https://aemo.com.au/en/energy-systems/electricity/national-electricity-market-nem",
        description="Australian Energy Market Operator 5-minute National Electricity Market load.",
    ),
    SourceSpec(
        name="World Bank",
        mode="long-term",
        frequency="annual",
        indicator="socioeconomic_demographic_energy",
        access="open",
        url="https://api.worldbank.org/v2/country/{iso3}/indicator/",
        description="World Development Indicators: Macroeconomic, Demographic, and Energy System panel data.",
    ),
    SourceSpec(
        name="NASA POWER",
        mode="long-term",
        frequency="daily",
        indicator="climate_meteorology",
        access="open",
        url="https://power.larc.nasa.gov/api/temporal/daily/point",
        description="Daily meteorological parameters: Temperature (T2M), Solar (ALLSKY_SFC_SW_DWN), Wind (WS10M), Precip (PRECTOTCORR).",
    ),
    SourceSpec(
        name="Nager.Date",
        mode="long-term",
        frequency="annual",
        indicator="public_holidays",
        access="open",
        url="https://date.nager.at/api/v3/PublicHolidays/{year}/{country}",
        description="Worldwide official public holiday calendars and regional observances.",
    ),
    SourceSpec(
        name="ERA5 / CDS",
        mode="long-term",
        frequency="hourly",
        indicator="climate_reanalysis",
        access="credentialed",
        url="https://cds.climate.copernicus.eu/api",
        credential="CDS_API_KEY",
        description="ECMWF Copernicus ERA5 reanalysis (Research Tier / Bulk extraction).",
    ),
    SourceSpec(
        name="CMIP6 / CDS",
        mode="long-term",
        frequency="monthly",
        indicator="climate_scenarios",
        access="credentialed",
        url="https://cds.climate.copernicus.eu/api",
        credential="CDS_API_KEY",
        description="Coupled Model Intercomparison Project Phase 6 climate projections (Research Tier).",
    ),
    SourceSpec(
        name="IIASA SSP",
        mode="long-term",
        frequency="five-year",
        indicator="population_gdp_scenarios",
        access="open",
        url="https://tntcat.iiasa.ac.at/SspDb/",
        description="Shared Socioeconomic Pathways demographic and economic long-term projections.",
    ),
    SourceSpec(
        name="GPWv4",
        mode="long-term",
        frequency="five-year",
        indicator="population_raster",
        access="open",
        url="https://sedac.ciesin.columbia.edu/data/collection/gpw-v4",
        description="Gridded Population of the World v4 spatial raster datasets.",
    ),
)


# Global HTTP Session for connection reuse
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({"User-Agent": "HGT-QF-DataDesk/2.2.0 (academic-research; electricity-forecasting)"})


def load_country_log(path: str | Path, sheet_name: str = "Searched Countries") -> list[str]:
    """Load unique ISO-3 countries from the research workbook."""
    frame = pd.read_excel(path, sheet_name=sheet_name, header=None, usecols=[0])
    continent_headings = {"africa", "asia", "europe", "north america", "south america", "oceania"}
    countries: list[str] = []
    for value in frame.iloc[:, 0].dropna().tolist():
        val_str = str(value).strip()
        if val_str.casefold() in continent_headings or "below are" in val_str.casefold() or "country" == val_str.casefold():
            continue
        country = normalize_country(val_str)
        if country and country not in countries:
            countries.append(country)
    return countries


def selected_sources(mode: str) -> list[SourceSpec]:
    """Get all sources configured for a given execution mode."""
    return [source for source in SOURCES if source.mode == mode]


def _get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 3,
    timeout: int = 30,
) -> tuple[Any, float]:
    """Fetch JSON with connection pooling, latency timing, and exponential backoff."""
    last_error = ""
    for attempt in range(retries):
        try:
            req_start = time.perf_counter()
            response = HTTP_SESSION.get(url, params=params, headers=headers, timeout=timeout)
            req_latency = time.perf_counter() - req_start

            if response.status_code == 429:
                raise RuntimeError(f"HTTP 429 Rate Limited from {url}")
            if response.status_code in [401, 403]:
                raise PermissionError(f"HTTP {response.status_code} Access Denied from {url}")

            response.raise_for_status()
            data = response.json()
            return data, req_latency
        except (PermissionError, RuntimeError):
            raise
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries - 1:
                time.sleep(2**attempt)

    raise RuntimeError(last_error or f"Request failed after {retries} attempts: {url}")


# ============================================================================
# Source Adapters (Preserving 100% Raw Native Values)
# ============================================================================

def _ember_adapter_wrapper(
    country: str,
    output: Path,
    start: int,
    end: int,
    credentials: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Execute Ember monthly electricity data adapter."""
    api_key = (credentials or {}).get("EMBER_API_KEY") or os.getenv("EMBER_API_KEY")
    result = get_ember_monthly_demand(country, start, end, api_key=api_key)

    if not result["success"]:
        return 0, result.get("status_type", "ACCESS_RESTRICTED"), result.get("message", "")

    data = result.get("data")
    if data is None or data.empty:
        return 0, "NO_DATA_AVAILABLE", "Ember returned 0 records for requested period"

    save_ember_data(data, output, country)
    generate_file_sidecar(
        output, "Ember", country, "monthly",
        "https://api.ember-energy.org/v1/electricity-generation/monthly",
        {"country": country, "start": start, "end": end},
    )
    return len(data), "SUCCESS", f"{len(data):,} monthly demand/generation records retrieved"


def _entsoe_adapter_wrapper(
    country: str,
    output: Path,
    start: int,
    end: int,
    credentials: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Execute ENTSO-E hourly load adapter with retry on transient 503/502/504 errors."""
    token = (credentials or {}).get("ENTSOE_API_TOKEN") or os.getenv("ENTSOE_API_TOKEN") or os.getenv("ENTSOE_API_KEY")

    # Retry up to 3 times for transient ENTSO-E server errors (503/502/504/rate limit)
    MAX_RETRIES = 3
    result = None
    for attempt in range(MAX_RETRIES):
        result = get_entsoe_total_load(country, start, end, api_token=token)
        # Only retry on transient server-side failures
        if result.get("status_type") in ("API_ERROR", "DOWNLOAD_ERROR") and attempt < MAX_RETRIES - 1:
            http_s = result.get("http_status", 0)
            if http_s in (429, 500, 502, 503, 504) or http_s == 0:
                wait = 2 ** (attempt + 1)
                time.sleep(wait)
                continue
        break  # Don't retry for ACCESS_RESTRICTED, MAPPING_MISSING, SUCCESS, etc.

    if not result["success"]:
        return 0, result.get("status_type", "ACCESS_RESTRICTED"), result.get("message", "")

    data = result.get("data")
    if data is None or data.empty:
        return 0, "NO_DATA_AVAILABLE", result.get("message", "0 records returned from ENTSO-E")

    save_entsoe_data(data, output, country)
    generate_file_sidecar(
        output, "ENTSO-E Transparency", country, "hourly",
        "https://transparency.entsoe.eu/api",
        {"documentType": "A65", "processType": "A16", "start": start, "end": end},
    )
    return len(data), "SUCCESS", result.get("message", f"{len(data):,} records retrieved")


def _eia_adapter_wrapper(
    country: str,
    output: Path,
    start: int,
    end: int,
    credentials: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Execute U.S. EIA hourly demand adapter."""
    key = (credentials or {}).get("EIA_API_KEY") or os.getenv("EIA_API_KEY")
    result = get_eia_hourly_demand(country, start, end, api_key=key, respondent_code="US48")

    if not result["success"]:
        return 0, result.get("status_type", "ACCESS_RESTRICTED"), result.get("message", "")

    data = result.get("data")
    if data is None or data.empty:
        return 0, "NO_DATA_AVAILABLE", result.get("message", "0 records returned from EIA")

    save_eia_data(data, output, country)
    generate_file_sidecar(
        output, "EIA Open Data", country, "hourly",
        "https://api.eia.gov/v2/electricity/rto/region-data/data/",
        {"frequency": "hourly", "type": "D", "respondent": "US48", "start": start, "end": end},
    )
    return len(data), "SUCCESS", result.get("message", f"{len(data):,} records retrieved")


def _world_bank_adapter(
    country: str,
    output: Path,
    start: int,
    end: int,
    credentials: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """
    Download World Bank WDI indicators across Macroeconomics, Demographics, and Energy.
    Preserves raw observations and native column names.
    """
    indicators = {
        # Economic
        "NY.GDP.MKTP.CD": ("gdp_usd", "current US$", "economic"),
        "NY.GDP.MKTP.KD.ZG": ("gdp_growth_pct", "%", "economic"),
        "NY.GDP.PCAP.CD": ("gdp_per_capita_usd", "current US$", "economic"),
        "FP.CPI.TOTL.ZG": ("cpi_inflation_pct", "%", "economic"),
        # Demographic
        "SP.POP.TOTL": ("population", "count", "demographic"),
        "SP.POP.GROW": ("population_growth_pct", "%", "demographic"),
        "SP.URB.TOTL": ("urban_population", "count", "demographic"),
        "SP.URB.TOTL.IN.ZS": ("urbanisation_rate_pct", "%", "demographic"),
        # Energy & Structure
        "EG.ELC.ACCS.ZS": ("electricity_access_pct", "% of population", "energy_system"),
        "EG.ELC.PROD.KH": ("electricity_production_kwh", "kWh", "energy_system"),
        "NV.IND.MANF.ZS": ("manufacturing_share_pct", "% of GDP", "built_environment"),
    }

    rows: list[dict[str, Any]] = []
    successful_inds = []
    failed_inds = []
    retrieved_at = datetime.now(timezone.utc).isoformat()

    for ind_code, (ind_name, ind_unit, ind_domain) in indicators.items():
        url = f"https://api.worldbank.org/v2/country/{country}/indicator/{ind_code}"
        params = {"format": "json", "per_page": 1000, "date": f"{start}:{end}"}

        try:
            payload, _ = _get_json(url, params)
            if not isinstance(payload, list) or len(payload) < 2:
                failed_inds.append(ind_name)
                continue

            header_info = payload[0] if isinstance(payload[0], dict) else {}
            data_items = payload[1] or []
            total_pages = header_info.get("pages", 1)
            current_page = header_info.get("page", 1)

            for item in data_items:
                raw_val = item.get("value")
                rows.append({
                    "iso3": country,
                    "year": int(item["date"]),
                    "indicator": ind_name,
                    "indicator_code": ind_code,
                    "domain": ind_domain,
                    "value": raw_val,
                    "observed": raw_val is not None,
                    "unit": ind_unit,
                    "source": "World Bank",
                    "frequency": "annual",
                    "retrieved_at": retrieved_at,
                })

            while current_page < total_pages:
                current_page += 1
                params["page"] = current_page
                p_payload, _ = _get_json(url, params)
                if isinstance(p_payload, list) and len(p_payload) > 1 and p_payload[1]:
                    for item in p_payload[1]:
                        raw_val = item.get("value")
                        rows.append({
                            "iso3": country,
                            "year": int(item["date"]),
                            "indicator": ind_name,
                            "indicator_code": ind_code,
                            "domain": ind_domain,
                            "value": raw_val,
                            "observed": raw_val is not None,
                            "unit": ind_unit,
                            "source": "World Bank",
                            "frequency": "annual",
                            "retrieved_at": retrieved_at,
                        })

            successful_inds.append(ind_name)
        except Exception:
            failed_inds.append(ind_name)

    if not rows:
        return 0, "NO_DATA_AVAILABLE", "World Bank returned 0 records for requested indicators"

    df = pd.DataFrame(rows).sort_values(by=["indicator", "year"])
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)

    generate_file_sidecar(
        output, "World Bank", country, "annual",
        f"https://api.worldbank.org/v2/country/{country}/indicator/",
        {"indicators": list(indicators.keys()), "start": start, "end": end},
    )

    if len(successful_inds) == len(indicators):
        return len(df), "SUCCESS", f"{len(df):,} records across all {len(indicators)} indicators"
    elif successful_inds:
        return len(df), "PARTIAL_SUCCESS", f"{len(df):,} records ({len(successful_inds)}/{len(indicators)} indicators available)"
    return 0, "NO_DATA_AVAILABLE", "No World Bank indicators returned observations"


def _nasa_power_adapter(
    country: str,
    output: Path,
    start: int,
    end: int,
    credentials: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """
    Download NASA POWER daily meteorological parameters.
    Variables: Temperature (T2M), Solar (ALLSKY_SFC_SW_DWN), Wind (WS10M), Precipitation (PRECTOTCORR).
    Preserves 100% raw values (sentinels -999.0 are NOT overwritten with NaN in raw files).
    """
    coords = get_country_coordinates(country)
    if not coords:
        return 0, "MAPPING_MISSING", f"Centroid coordinates not registered for {country}"

    lat, lon = coords
    clamped_start = max(1981, start)
    clamped_end = min(end, date.today().year)
    start_date = f"{clamped_start}0101"
    end_date = f"{clamped_end}1231"

    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    params = {
        "parameters": "T2M,ALLSKY_SFC_SW_DWN,WS10M,PRECTOTCORR",
        "community": "RE",
        "longitude": lon,
        "latitude": lat,
        "start": start_date,
        "end": end_date,
        "format": "JSON",
    }

    try:
        payload, latency = _get_json(url, params, timeout=60)
        parameter = payload.get("properties", {}).get("parameter", {})
        if not parameter:
            return 0, "NO_DATA_AVAILABLE", "NASA POWER returned no parameter block"

        dates = sorted(set().union(*(values.keys() for values in parameter.values())))
        if not dates:
            return 0, "NO_DATA_AVAILABLE", "NASA POWER returned no date observations"

        retrieved_at = datetime.now(timezone.utc).isoformat()
        rows = []

        for d_str in dates:
            formatted_date = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}" if len(d_str) == 8 else d_str
            t2m_raw = parameter.get("T2M", {}).get(d_str)
            solar_raw = parameter.get("ALLSKY_SFC_SW_DWN", {}).get(d_str)
            ws10m_raw = parameter.get("WS10M", {}).get(d_str)
            precip_raw = parameter.get("PRECTOTCORR", {}).get(d_str)

            rows.append({
                "iso3": country,
                "date": formatted_date,
                "T2M": t2m_raw,
                "ALLSKY_SFC_SW_DWN": solar_raw,
                "WS10M": ws10m_raw,
                "PRECTOTCORR": precip_raw,
                "concept": "climate_meteorology",
                "source": "NASA POWER",
                "frequency": "daily",
                "latitude": lat,
                "longitude": lon,
                "retrieved_at": retrieved_at,
            })

        df = pd.DataFrame(rows)
        output.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output, index=False)

        generate_file_sidecar(
            output, "NASA POWER", country, "daily",
            url, params, latency_sec=latency,
        )

        return len(df), "SUCCESS", f"{len(df):,} daily weather records (T2M, Solar, Wind, Precip)"

    except Exception as exc:
        return 0, "DOWNLOAD_ERROR", f"NASA POWER request failed: {str(exc)[:150]}"


def _neso_adapter(
    country: str,
    output: Path,
    start: int,
    end: int,
    credentials: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Download National Energy System Operator (UK/GBR) half-hourly demand data."""
    if country != "GBR":
        return 0, "SOURCE_NOT_COVERED", f"ESO / NESO publishes Great Britain data only, not {country}"

    catalog_url = "https://api.neso.energy/api/3/action/package_show"
    try:
        catalog, _ = _get_json(catalog_url, {"id": "historic-demand-data"})
        resources = catalog.get("result", {}).get("resources", [])

        selected = []
        for res in resources:
            name = str(res.get("name", ""))
            r_url = str(res.get("url", ""))
            match = re.search(r"(\d{4})", name) or re.search(r"demanddata(?:update)?_(\d{4})", r_url, re.IGNORECASE)
            if match and str(res.get("format", "")).upper() == "CSV":
                yr = int(match.group(1))
                if start <= yr <= end:
                    selected.append((yr, res))

        if not selected:
            return 0, "NO_DATA_AVAILABLE", f"NESO has no demand packages available for {start}-{end}"

        selected.sort(key=lambda x: x[0])
        frames = []
        retrieved_at = datetime.now(timezone.utc).isoformat()

        for yr, res in selected:
            resp = HTTP_SESSION.get(res["url"], timeout=120)
            resp.raise_for_status()
            frame = pd.read_csv(io.BytesIO(resp.content))
            frame.columns = [c.strip().lower().replace(" ", "_") for c in frame.columns]
            frame["iso3"] = "GBR"
            frame["source"] = "ESO / NESO"
            frame["frequency"] = "half-hourly"
            frame["concept"] = "electricity_demand"
            frame["source_variable"] = "National Demand (ND) / Transmission System Demand (TSD)"
            frame["retrieved_at"] = retrieved_at
            frames.append(frame)

        if not frames:
            return 0, "NO_DATA_AVAILABLE", "NESO returned 0 records"

        combined = pd.concat(frames, ignore_index=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(output, index=False)

        generate_file_sidecar(
            output, "ESO / NESO", country, "half-hourly",
            catalog_url,
            {"package_id": "historic-demand-data", "start": start, "end": end},
        )
        return len(combined), "SUCCESS", f"{len(combined):,} half-hourly demand records retrieved"

    except Exception as exc:
        return 0, "DOWNLOAD_ERROR", f"NESO download failed: {str(exc)[:150]}"


def _nager_date_adapter(
    country: str,
    output: Path,
    start: int,
    end: int,
    credentials: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Download worldwide public holidays from Nager.Date API."""
    import pycountry

    country_record = pycountry.countries.get(alpha_3=country)
    alpha2 = country_record.alpha_2 if country_record else None
    if not alpha2:
        if country == "XKX":
            alpha2 = "XK"
        else:
            return 0, "MAPPING_MISSING", f"ISO-2 country mapping missing for {country}"

    rows: list[dict[str, Any]] = []
    retrieved_at = datetime.now(timezone.utc).isoformat()
    missing_years = []

    for yr in range(start, end + 1):
        url = f"https://date.nager.at/api/v3/PublicHolidays/{yr}/{alpha2}"
        try:
            payload, _ = _get_json(url)
            if isinstance(payload, list):
                for item in payload:
                    rows.append({
                        "iso3": country,
                        "date": item.get("date"),
                        "name": item.get("localName") or item.get("name"),
                        "global": item.get("global", True),
                        "types": ",".join(item.get("types", [])),
                        "concept": "public_holidays",
                        "source": "Nager.Date",
                        "frequency": "annual",
                        "retrieved_at": retrieved_at,
                    })
        except Exception:
            missing_years.append(yr)

    if not rows:
        return 0, "NO_DATA_AVAILABLE", f"Nager.Date has no holiday records for {country} ({start}-{end})"

    df = pd.DataFrame(rows).sort_values(by="date")
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)

    generate_file_sidecar(
        output, "Nager.Date", country, "annual",
        f"https://date.nager.at/api/v3/PublicHolidays/{{year}}/{alpha2}",
        {"country_iso2": alpha2, "start": start, "end": end},
    )

    if missing_years:
        return len(df), "PARTIAL_SUCCESS", f"{len(df):,} holidays ({len(missing_years)} years unavailable)"
    return len(df), "SUCCESS", f"{len(df):,} holidays retrieved"


ADAPTERS: dict[str, Callable[..., tuple[int, str, str]]] = {
    "Ember": _ember_adapter_wrapper,
    "ENTSO-E Transparency": _entsoe_adapter_wrapper,
    "EIA Open Data": _eia_adapter_wrapper,
    "World Bank": _world_bank_adapter,
    "Nager.Date": _nager_date_adapter,
    "NASA POWER": _nasa_power_adapter,
    "ESO / NESO": _neso_adapter,
}


# ============================================================================
# Main Acquisition Engine
# ============================================================================

def run_pipeline(
    countries: list[str],
    mode: str,
    raw_dir: str,
    start: int,
    end: int,
    progress: Callable[[str], None] | None = None,
    credentials: dict[str, str] | None = None,
) -> list[SourceResult]:
    """
    Main verified acquisition orchestrator.
    Consults pre-flight capability and source mappings before executing queries.
    """
    root = Path(raw_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    ensure_quality_dir(root)
    ensure_harmonized_dir(root)
    ensure_model_ready_dir(root)

    results: list[SourceResult] = []
    demand_coverage_data = []

    for country in countries:
        c_name = get_country_name(country)

        # Track which demand sources succeeded for this country so we can
        # apply the Ember fallback correctly in short-term mode.
        country_demand_succeeded = False
        # Deduplicate sources by name so Ember (which appears in both modes)
        # only runs once per country — but only after we know if a higher-frequency
        # source succeeded.
        seen_source_names: set[str] = set()

        for source in selected_sources(mode):
            # Ember deduplication: in short-term mode Ember appears twice in SOURCES
            # (once as long-term entry, once as short-term fallback). Only run it once.
            source_key = source.name
            if source_key in seen_source_names:
                continue
            seen_source_names.add(source_key)

            if progress:
                progress(f"{c_name} ({country}) | {source.name}")

            retrieved_at = datetime.now(timezone.utc).isoformat()
            output = get_raw_path(root, source.name, country, source.frequency)

            # 1. Pre-Flight Capability & Area Mapping Check
            cap_status, cap_reason = validate_source_capability(country, source.name)
            if cap_status != "OK":
                status_key = cap_status
                if cap_status == "RESEARCH_TIER":
                    status_key = "SOURCE_NOT_COVERED"
                # For Ember specifically: always attempt regardless of pre-flight
                # (Ember validate_source_capability always returns OK, so this is just a guard)
                results.append(
                    SourceResult(
                        country=country,
                        country_name=c_name,
                        source=source.name,
                        mode=mode,
                        status=status_key,
                        status_badge=STATUS_BADGES.get(status_key, status_key),
                        message=cap_reason,
                        records=0,
                        raw_path="",
                        retrieved_at=retrieved_at,
                        url=source.url,
                    )
                )
                continue

            # 2. Execute Adapter
            try:
                if source.name in ADAPTERS:
                    records, status, message = ADAPTERS[source.name](
                        country, output, start, end, credentials
                    )
                else:
                    status = "MAPPING_MISSING"
                    message = f"Adapter for {source.name} is in research specification phase"
                    records = 0

                # Track demand coverage if electricity demand was acquired
                if source.indicator == "electricity_demand" and status in ["SUCCESS", "PARTIAL_SUCCESS"]:
                    country_demand_succeeded = True
                    try:
                        if output.exists():
                            data = pd.read_csv(output)
                            start_month = f"{start}-01"
                            end_month = f"{end}-12"
                            coverage = calculate_demand_coverage(data, country, start_month, end_month)
                            demand_coverage_data.append(coverage)
                    except Exception:
                        pass

                results.append(
                    SourceResult(
                        country=country,
                        country_name=c_name,
                        source=source.name,
                        mode=mode,
                        status=status,
                        status_badge=STATUS_BADGES.get(status, status),
                        message=message,
                        records=records,
                        raw_path=str(output) if records > 0 else "",
                        retrieved_at=retrieved_at,
                        url=source.url,
                    )
                )

            except PermissionError as exc:
                results.append(
                    SourceResult(
                        country=country,
                        country_name=c_name,
                        source=source.name,
                        mode=mode,
                        status="ACCESS_RESTRICTED",
                        status_badge=STATUS_BADGES["ACCESS_RESTRICTED"],
                        message=str(exc),
                        retrieved_at=retrieved_at,
                        url=source.url,
                    )
                )
            except Exception as exc:
                results.append(
                    SourceResult(
                        country=country,
                        country_name=c_name,
                        source=source.name,
                        mode=mode,
                        status="DOWNLOAD_ERROR",
                        status_badge=STATUS_BADGES["DOWNLOAD_ERROR"],
                        message=str(exc),
                        retrieved_at=retrieved_at,
                        url=source.url,
                    )
                )

    # 1. Availability Report
    report = root / f"availability_{mode}.json"
    report.write_text(json.dumps([asdict(item) for item in results], indent=2), encoding="utf-8")

    # 2. Demand Coverage Matrix
    if demand_coverage_data:
        quality_dir = ensure_quality_dir(root)
        coverage_matrix_path = quality_dir / "demand_coverage_matrix.csv"
        generate_coverage_matrix(demand_coverage_data, coverage_matrix_path)

    # 3. Multi-Dimensional Inventory Reports (Country, Feature, Historical Depth, Source Registry)
    generate_all_inventory_reports(root, countries, results)

    # 4. Standard Dataset Manifest with SHA-256 Hashes
    generate_dataset_manifest(root, mode, results)

    # 5. Quality Tier Evaluation (Preserving Raw Values)
    generate_quality_report(root, mode, start, end, results)

    # 6. Cross-Source Conflict Detection
    detect_source_conflicts(root)

    # 7. Run Manifest
    manifest = {
        "project": "HGT-QF Data Desk",
        "version": "2.2.0",
        "mode": mode,
        "countries_iso3": countries,
        "years": {"start": start, "end": end},
        "sources": [asdict(source) for source in selected_sources(mode)],
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_architecture": "data/raw/<domain>/<category>/<source>/<ISO3>.csv",
        "layers": {
            "raw": str(root / "raw"),
            "quality": str(root / "quality"),
            "harmonized": str(root / "harmonized"),
            "model_ready": str(root / "model_ready"),
        },
    }
    (root / f"manifest_{mode}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return results
