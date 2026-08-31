"""Acquisition engine for HGT-QF.

Adapters are deliberately isolated: one unavailable or credentialed source produces a
report entry and cannot abort the rest of a run.
"""
from __future__ import annotations

import json
import io
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pycountry
import requests


@dataclass(frozen=True)
class SourceSpec:
    name: str
    mode: str
    frequency: str
    indicator: str
    access: str
    url: str
    credential: str = ""


@dataclass
class SourceResult:
    country: str
    source: str
    mode: str
    status: str
    message: str
    records: int = 0
    raw_path: str = ""
    retrieved_at: str = ""
    url: str = ""


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("EIA Open Data", "short-term", "hourly", "demand", "api", "https://api.eia.gov/v2/electricity/rto/region-data/data/", "EIA_API_KEY"),
    SourceSpec("ENTSO-E Transparency", "short-term", "hourly", "demand", "api", "https://transparency.entsoe.eu/api", "ENTSOE_API_TOKEN"),
    SourceSpec("ESO / NESO", "short-term", "half-hourly", "demand", "open", "https://api.neso.energy/api/3/action/package_search"),
    SourceSpec("AEMO", "short-term", "five-minute", "demand", "open", "https://aemo.com.au/en/energy-systems/electricity/national-electricity-market-nem/data-nem/market-management-system-mms-data"),
    SourceSpec("Ember", "long-term", "annual", "demand and generation", "open", "https://ember-energy.org/data/"),
    SourceSpec("World Bank", "long-term", "annual", "socioeconomic", "open", "https://api.worldbank.org/v2/country/{iso3}/indicator/NY.GDP.MKTP.CD"),
    SourceSpec("NASA POWER", "long-term", "daily", "weather", "open", "https://power.larc.nasa.gov/api/temporal/daily/point"),
    SourceSpec("ERA5 / CDS", "long-term", "hourly", "weather", "credentialed", "https://cds.climate.copernicus.eu/api", "CDS_API_KEY"),
    SourceSpec("CMIP6 / CDS", "long-term", "monthly", "climate scenarios", "credentialed", "https://cds.climate.copernicus.eu/api", "CDS_API_KEY"),
    SourceSpec("IIASA SSP", "long-term", "annual", "population and GDP scenarios", "open", "https://tntcat.iiasa.ac.at/SspDb/"),
    SourceSpec("GPWv4", "long-term", "five-year", "population raster", "open", "https://sedac.ciesin.columbia.edu/data/collection/gpw-v4"),
    SourceSpec("Nager.Date", "long-term", "annual", "public holidays", "open", "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"),
)


def normalize_country(value: str) -> str | None:
    value = value.strip()
    try:
        if len(value) == 3:
            country = pycountry.countries.get(alpha_3=value.upper())
        else:
            country = pycountry.countries.get(name=value) or pycountry.countries.search_fuzzy(value)[0]
    except LookupError:
        country = None
    return country.alpha_3 if country else None


def load_country_log(path: str | Path, sheet_name: str = "Searched Countries") -> list[str]:
    """Load unique ISO-3 countries from the research workbook."""
    frame = pd.read_excel(path, sheet_name=sheet_name, header=None, usecols=[0])
    continent_headings = {"africa", "asia", "europe", "north america", "south america", "oceania"}
    countries: list[str] = []
    for value in frame.iloc[:, 0].dropna().tolist():
        if str(value).strip().casefold() in continent_headings:
            continue
        country = normalize_country(str(value))
        if country and country not in countries:
            countries.append(country)
    return countries


def selected_sources(mode: str) -> list[SourceSpec]:
    return [source for source in SOURCES if source.mode == mode]


def _get_json(url: str, params: dict[str, Any] | None = None, retries: int = 3) -> Any:
    last_error = ""
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt < retries - 1:
                time.sleep(2**attempt)
    raise RuntimeError(last_error or "request failed")


def _world_bank(country: str, output: Path, start: int, end: int) -> int:
    indicators = {"NY.GDP.MKTP.CD": "gdp_usd", "SP.POP.TOTL": "population", "EG.ELC.ACCS.ZS": "electricity_access_pct"}
    rows: list[dict[str, Any]] = []
    for indicator, column in indicators.items():
        url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
        payload = _get_json(url, {"format": "json", "per_page": 1000, "date": f"{start}:{end}"})
        for item in (payload[1] if isinstance(payload, list) and len(payload) > 1 else []):
            rows.append({"iso3": country, "year": int(item["date"]), "indicator": column, "value": item["value"], "status": "observed", "unit": "varies"})
    if not rows:
        return 0
    pd.DataFrame(rows).to_csv(output, index=False)
    return len(rows)


def _nager_date(country: str, output: Path, start: int, end: int) -> int:
    country_record = pycountry.countries.get(alpha_3=country)
    if not country_record:
        raise ValueError(f"unknown ISO-3 country: {country}")
    rows: list[dict[str, Any]] = []
    for year in range(start, end + 1):
        payload = _get_json(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_record.alpha_2}")
        rows.extend({"iso3": country, "date": item["date"], "name": item["localName"], "status": "observed"} for item in payload)
    pd.DataFrame(rows).to_csv(output, index=False)
    return len(rows)


COUNTRY_COORDINATES: dict[str, tuple[float, float]] = {
    "AGO": (-11.2027, 17.8739), "BEN": (9.3077, 2.3158), "CMR": (7.3697, 12.3547),
    "EGY": (26.8206, 30.8025), "GBR": (55.3781, -3.4360), "LBY": (26.3351, 17.2283),
    "MUS": (-20.3484, 57.5522), "MYS": (4.2105, 101.9758), "SOM": (5.1521, 46.1996),
}


def _nasa_power(country: str, output: Path, start: int, end: int) -> int:
    coordinates = COUNTRY_COORDINATES.get(country)
    if not coordinates:
        raise NotImplementedError("country coordinates are not present in the source log mapping")
    start_date = f"{start}0101"
    end_date = f"{min(end, date.today().year)}1231"
    payload = _get_json(
        "https://power.larc.nasa.gov/api/temporal/daily/point",
        {
            "parameters": "T2M,PRECTOTCORR,WS10M",
            "community": "RE",
            "longitude": coordinates[1],
            "latitude": coordinates[0],
            "start": start_date,
            "end": end_date,
            "format": "JSON",
        },
    )
    parameter = payload.get("properties", {}).get("parameter", {})
    dates = sorted(set().union(*(values.keys() for values in parameter.values())))
    rows = [{"iso3": country, "date": item, **{name: values.get(item) for name, values in parameter.items()}} for item in dates]
    if not rows:
        return 0
    pd.DataFrame(rows).to_csv(output, index=False)
    return len(rows)


def _neso(country: str, output: Path, start: int, end: int) -> int:
    if country != "GBR":
        raise NotImplementedError("NESO publishes Great Britain data only")
    catalog = _get_json(
        "https://api.neso.energy/api/3/action/package_show",
        {"id": "historic-demand-data"},
    )
    resources = catalog.get("result", {}).get("resources", [])
    selected = []
    for resource in resources:
        name = str(resource.get("name", ""))
        url = str(resource.get("url", ""))
        match = re.search(r"(\d{4})", name) or re.search(r"demanddata(?:update)?_(\d{4})", url, re.IGNORECASE)
        if match and str(resource.get("format", "")).upper() == "CSV" and start <= int(match.group(1)) <= end:
            selected.append(resource)
    if not selected:
        raise RuntimeError(f"NESO has no historic demand CSVs for {start}-{end}")

    records = 0
    frames = []
    for resource in selected:
        response = requests.get(resource["url"], timeout=120)
        response.raise_for_status()
        frame = pd.read_csv(io.BytesIO(response.content))
        frame["iso3"] = country
        frames.append(frame)
        records += len(frame)
    pd.concat(frames, ignore_index=True).to_csv(output, index=False)
    return records


def _generic_placeholder(source: SourceSpec, country: str, output: Path, credentials: dict[str, str] | None = None) -> int:
    aliases = {"ENTSOE_API_TOKEN": "ENTSOE_API_KEY"}
    credential = (credentials or {}).get(source.credential) or os.getenv(source.credential) or os.getenv(aliases.get(source.credential, ""))
    if source.access == "credentialed" and not credential:
        raise PermissionError(f"missing credential: {source.credential}")
    country_coverage = {
        "EIA Open Data": {"USA"},
        "ENTSO-E Transparency": {"AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA", "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD", "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE", "GBR"},
        "AEMO": {"AUS"},
    }
    covered = country_coverage.get(source.name)
    if covered is not None and country not in covered:
        raise NotImplementedError(f"{source.name} does not publish demand data for {country}")
    raise NotImplementedError(f"{source.name} requires a source-specific area or dataset mapping for {country}")


ADAPTERS: dict[str, Callable[..., int]] = {
    "World Bank": _world_bank,
    "Nager.Date": _nager_date,
    "NASA POWER": _nasa_power,
    "ESO / NESO": _neso,
}


def run_pipeline(countries: list[str], mode: str, raw_dir: str, start: int, end: int, progress: Callable[[str], None] | None = None, credentials: dict[str, str] | None = None) -> list[SourceResult]:
    root = Path(raw_dir).expanduser()
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "harmonized").mkdir(parents=True, exist_ok=True)
    (root / "model_ready").mkdir(parents=True, exist_ok=True)
    results: list[SourceResult] = []
    for country in countries:
        source_files: list[tuple[SourceSpec, Path, str]] = []
        for source in selected_sources(mode):
            if progress:
                progress(f"{country} | {source.name}")
            retrieved_at = datetime.now(timezone.utc).isoformat()
            output = root / "raw" / f".{country}_{source.name.lower().replace(' ', '_').replace('/', '-')}.csv"
            try:
                if source.name in ADAPTERS:
                    records = ADAPTERS[source.name](country, output, start, end)
                    status = "success" if records else "unavailable"
                    message = f"{records:,} records" if records else "source returned no records"
                else:
                    records = _generic_placeholder(source, country, output, credentials)
                    status, message = "success", f"{records:,} records"
                if status == "success":
                    source_files.append((source, output, retrieved_at))
                results.append(SourceResult(country, source.name, mode, status, message, records, str(output), retrieved_at, source.url))
            except PermissionError as exc:
                results.append(SourceResult(country, source.name, mode, "credential-required", str(exc), retrieved_at=retrieved_at, url=source.url))
            except NotImplementedError as exc:
                results.append(SourceResult(country, source.name, mode, "not-covered", str(exc), retrieved_at=retrieved_at, url=source.url))
            except Exception as exc:  # adapters must never stop the run
                results.append(SourceResult(country, source.name, mode, "failed", str(exc), retrieved_at=retrieved_at, url=source.url))
        merged_frames = []
        for source, source_file, retrieved_at in source_files:
            frame = pd.read_csv(source_file)
            frame.insert(0, "source", source.name)
            frame.insert(1, "frequency", source.frequency)
            frame.insert(2, "retrieved_at", retrieved_at)
            merged_frames.append(frame)
            source_file.unlink(missing_ok=True)
        if merged_frames:
            country_record = pycountry.countries.get(alpha_3=country)
            country_name = country_record.name if country_record else country
            country_output = root / "raw" / f"{country_name}.csv"
            pd.concat(merged_frames, ignore_index=True, sort=False).to_csv(country_output, index=False)
            for result in results:
                if result.country == country and result.status == "success":
                    result.raw_path = str(country_output)
    report = root / f"availability_{mode}.json"
    report.write_text(json.dumps([asdict(item) for item in results], indent=2), encoding="utf-8")
    manifest = {
        "project": "HGT-QF",
        "mode": mode,
        "countries_iso3": countries,
        "years": {"start": start, "end": end},
        "sources": [asdict(source) for source in selected_sources(mode)],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "layers": {"raw": str(root / "raw"), "harmonized": str(root / "harmonized"), "model_ready": str(root / "model_ready")},
    }
    (root / f"manifest_{mode}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return results
