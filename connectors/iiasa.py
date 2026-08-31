"""IIASA SSP (Shared Socioeconomic Pathways) bulk scenario connector.

The SSP database is a *country-level scenario* dataset — a country is never
"not covered"; the question is which scenario × variable × year to extract.
The correct acquisition is therefore:

    download/cache the bulk IAMC CSV ONCE
        -> find the country's region code (ISO-3)
        -> select scenario + variable
        -> extract that country's rows

IAMC CSV shape (the format the SSP DB publishes):
    Model, Scenario, Region, Variable, Unit, <2000>, <2001>, ... <2100>
with one row per (model, scenario, region, variable). Country rows use ISO-3
region codes; aggregate rows use codes like "World", "R5OECD90+EU", etc.

Bulk file (documented by the message_ix / IIASA project):
    https://data.ece.iiasa.ac.at/ssp/1706548837040-ssp_basic_drivers_release_3.0_full.csv.gz
with the legacy SspDb country CSV as a fallback. The URL list is configurable
via the ``IIASA_SSP_URLS`` env var (comma-separated).
"""
from __future__ import annotations

import gzip
import io
import os
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from country_registry import get_country_record

from .base import AcquisitionOutcome, EndpointVerification, _HTTP

# Candidate bulk files, tried in order (cached after the first success).
_SSP_URLS = (
    "https://data.ece.iiasa.ac.at/ssp/1706548837040-ssp_basic_drivers_release_3.0_full.csv.gz",
    "https://data.ece.iiasa.ac.at/ssp/1710759470883-ssp_basic_drivers_release_3.0.1_full.csv.gz",
    "https://tntcat.iiasa.ac.at/SspDb/download/SspDb_country_data_2013-06-12.csv.zip",
)

# concept -> (IAMC variable name, aggregation note)
SSP_VARIABLES: dict[str, dict[str, str]] = {
    "ssp_population": {"variable": "Population", "note": "persons (millions)"},
    "ssp_gdp": {"variable": "GDP|PPP", "note": "billion US$2017/yr (PPP)"},
    "ssp_urban_population": {"variable": "Population|Urban", "note": "urban population"},
}

# scenario alias -> canonical prefix
_SCENARIO_ALIASES = {
    "ssp1": "SSP1", "ssp2": "SSP2", "ssp3": "SSP3", "ssp4": "SSP4", "ssp5": "SSP5",
}


def _ssp_urls() -> list[str]:
    extra = os.getenv("IIASA_SSP_URLS", "")
    return [u.strip() for u in extra.split(",") if u.strip()] + list(_SSP_URLS)


def normalize_scenario(scenario: str) -> str:
    key = scenario.strip()
    for alias, canon in _SCENARIO_ALIASES.items():
        if key.lower().startswith(alias):
            return canon
    return key


def _cache_path(out_dir: Path) -> Path:
    return out_dir / "scenario" / "iiasa" / "_cache" / "ssp_bulk.csv"


def download_ssp_bulk(out_dir: Path, history: list[dict[str, Any]] | None = None) -> Path:
    """Download and cache the SSP bulk CSV once (reused across countries)."""
    cache = _cache_path(out_dir)
    if cache.exists():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    last_err = ""
    for url in _ssp_urls():
        try:
            resp = _HTTP.get(url, timeout=180, history=history)
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}"
                continue
            body = resp.content
            if url.endswith(".gz"):
                body = gzip.decompress(body)
            elif url.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(body)) as zf:
                    names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                    if not names:
                        last_err = "zip contained no CSV"
                        continue
                    body = zf.read(names[0])
            cache.write_bytes(body)
            return cache
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"Could not download IIASA SSP bulk file: {last_err}")


def read_ssp_bulk(path: Path) -> pd.DataFrame:
    """Parse the IAMC CSV into a wide frame (Model/Scenario/Region/Variable/Unit + years)."""
    df = pd.read_csv(path, low_memory=False)
    year_cols = [c for c in df.columns if str(c).strip().isdigit()]
    if not year_cols:
        raise ValueError("IAMC CSV has no year columns")
    id_cols = [c for c in ("Model", "Scenario", "Region", "Variable", "Unit") if c in df.columns]
    df = df[id_cols + year_cols].copy()
    for col in year_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _region_matches(region: str, country: str) -> bool:
    rec = get_country_record(country)
    name = rec.country_name if rec else country
    r = str(region).strip()
    return r.casefold() in (country.casefold(), name.casefold())


def extract_ssp_country(
    df: pd.DataFrame,
    country: str,
    variable: str,
    scenario: str,
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    """Extract one country's (scenario, variable) rows into a long frame."""
    scen = normalize_scenario(scenario)
    years = [int(c) for c in df.columns if str(c).isdigit() and start_year <= int(c) <= end_year]
    mask = (
        df["Region"].map(lambda r: _region_matches(r, country))
        & (df["Variable"].astype(str).str.strip() == variable)
        & df["Scenario"].astype(str).str.strip().str.upper().str.startswith(scen.upper())
    )
    sub = df[mask]
    if sub.empty:
        return pd.DataFrame(columns=["iso3", "model", "scenario", "variable", "unit", "year", "value"])
    long = sub.melt(
        id_vars=[c for c in ("Model", "Scenario", "Variable", "Unit") if c in sub.columns],
        value_vars=[str(y) for y in years],
        var_name="year", value_name="value",
    )
    long = long.dropna(subset=["value"])
    long["iso3"] = country
    long["year"] = long["year"].astype(int)
    long = long.rename(columns={"Model": "model", "Scenario": "scenario",
                                "Variable": "variable", "Unit": "unit"})
    return long[["iso3", "model", "scenario", "variable", "unit", "year", "value"]].sort_values("year")


def _output_path(out_dir: Path, country: str) -> Path:
    return out_dir / "scenario" / "iiasa" / f"{country}.csv"


def acquire_iiasa(
    country: str,
    feature: str,
    scenario: str,
    start_year: int,
    end_year: int,
    out_dir: Path,
) -> AcquisitionOutcome:
    """Download/cache the SSP bulk file once, then extract this country's rows."""
    spec = SSP_VARIABLES.get(feature)
    if spec is None:
        return AcquisitionOutcome(
            source_id="iiasa", country=country, feature=feature, status="SCHEMA_MISMATCH",
            message=f"Unsupported IIASA SSP feature {feature!r} (choose from {sorted(SSP_VARIABLES)})",
            failure_reason="SCHEMA_MISMATCH",
        )
    history: list[dict[str, Any]] = []
    try:
        bulk_path = download_ssp_bulk(out_dir, history)
        bulk = read_ssp_bulk(bulk_path)
    except Exception as exc:  # noqa: BLE001
        return AcquisitionOutcome(
            source_id="iiasa", country=country, feature=feature, status="NETWORK_ERROR",
            message=f"IIASA SSP bulk download failed: {exc}", failure_reason="NETWORK_ERROR",
            attempts=history,
        )

    long = extract_ssp_country(bulk, country, spec["variable"], scenario, start_year, end_year)
    if long.empty:
        return AcquisitionOutcome(
            source_id="iiasa", country=country, feature=feature, status="NO_DATA",
            message=(f"IIASA SSP has no '{spec['variable']}' rows for {country} "
                     f"under scenario {normalize_scenario(scenario)} ({start_year}-{end_year})"),
            failure_reason="NO_DATA", attempts=history,
        )

    out_path = _output_path(out_dir, country)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        existing = pd.read_csv(out_path)
        existing = existing[existing["variable"] != spec["variable"]]
        long = pd.concat([existing, long], ignore_index=True)
    long.to_csv(out_path, index=False)

    return AcquisitionOutcome(
        source_id="iiasa", country=country, feature=feature, status="SUCCESS",
        message=(f"{len(long)} IIASA SSP rows for {feature} "
                 f"(scenario {normalize_scenario(scenario)}, {spec['variable']})"),
        records=len(long), path=str(out_path), frequency="5-year", unit=spec["note"],
        requested_start=f"{start_year}", requested_end=f"{end_year}",
        received_start=str(long['year'].min()), received_end=str(long['year'].max()),
        schema_columns=list(long.columns),
        verification_notes=["IAMC CSV parsed", f"region matched to {country}",
                            "bulk file cached (downloaded once)"],
        provenance={"bulk_file": str(_cache_path(out_dir)),
                    "variable": spec["variable"], "scenario": normalize_scenario(scenario)},
        attempts=history,
    )


def verify_iiasa() -> EndpointVerification:
    return EndpointVerification(
        source_id="iiasa", country="", feature="ssp_scenario", status="VERIFIED",
        message="IIASA SSP is an open bulk dataset (no key); verified at download time",
    )


def diagnose_iiasa(
    country: str, feature: str, scenario: str, start_year: int, end_year: int, out_dir: Path,
) -> dict[str, Any]:
    diag: dict[str, Any] = {
        "source": "iiasa", "country": country, "feature": feature,
        "scenario": normalize_scenario(scenario),
        "variable": SSP_VARIABLES.get(feature, {}).get("variable"),
        "bulk_urls": _ssp_urls(), "auth_supplied": False, "status": "", "records": 0,
        "output_path": "", "failure_reason": "",
    }
    outcome = acquire_iiasa(country, feature, scenario, start_year, end_year, out_dir)
    diag["status"] = outcome.status
    diag["records"] = outcome.records
    diag["output_path"] = outcome.path
    if outcome.status != "SUCCESS":
        diag["failure_reason"] = f"{outcome.failure_reason}: {outcome.message}"
    return diag


def iiasa_connector(
    country: str, feature: str, start: int, end: int,
    credentials: dict[str, str] | None, out_dir: Path, **kwargs: Any,
) -> tuple[EndpointVerification, AcquisitionOutcome]:
    verification = verify_iiasa()
    scenario = kwargs.get("scenario", "SSP2")
    outcome = acquire_iiasa(country, feature, scenario, start, end, out_dir)
    return verification, outcome


__all__ = [
    "SSP_VARIABLES", "normalize_scenario", "download_ssp_bulk", "read_ssp_bulk",
    "extract_ssp_country", "acquire_iiasa", "verify_iiasa", "diagnose_iiasa",
    "iiasa_connector",
]
