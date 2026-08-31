"""Provenance and Dataset Manifest Tracking for HGT-QF Data Desk.

Generates SHA-256 checksums, dataset schemas, latency records, license attribution,
and sidecar metadata files for complete reproducibility and auditing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from country_utils import get_country_name
from source_registry import get_source_metadata


def sanitize_url(url: str) -> str:
    """Mask credentials, API keys, or tokens in URLs to prevent leakage in metadata."""
    if not url:
        return ""
    sanitized = re.sub(r"([?&](?:api_key|token|key|secret|password|access_token|securityToken)=)[^&]+", r"\1[REDACTED]", url, flags=re.IGNORECASE)
    return sanitized


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    if not file_path.exists() or not file_path.is_file():
        return ""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def inspect_csv_metadata(file_path: Path) -> dict[str, Any]:
    """Inspect CSV content to extract summary statistics, schema, and completeness."""
    if not file_path.exists():
        return {"error": "file_not_found"}

    try:
        df = pd.read_csv(file_path)
    except Exception as exc:
        return {"error": f"failed_to_parse_csv: {exc}"}

    total_rows = len(df)
    columns_info = {}
    sentinels_detected = False
    sentinels_found = set()
    sentinel_targets = {-999.0, -9999.0, -99.0, 99999.0, 999999.0, -999, -9999, -99}

    for col in df.columns:
        null_count = int(df[col].isna().sum())
        columns_info[col] = {
            "dtype": str(df[col].dtype),
            "null_count": null_count,
            "non_null_count": total_rows - null_count,
        }
        if pd.api.types.is_numeric_dtype(df[col]) and col.lower() not in ["year", "iso3"]:
            matching_sentinels = set(df[col].dropna()).intersection(sentinel_targets)
            if matching_sentinels:
                sentinels_detected = True
                sentinels_found.update(matching_sentinels)

    # Extract temporal range if date/year column exists
    time_bounds = {}
    date_col = next((c for c in ["date", "year", "SettlementDate", "settlement_date", "period_utc", "period_start_utc"] if c in df.columns), None)
    if date_col and total_rows > 0:
        clean_dates = df[date_col].dropna()
        if not clean_dates.empty:
            time_bounds["min_time"] = str(clean_dates.min())
            time_bounds["max_time"] = str(clean_dates.max())

    observed_count = total_rows
    if "observed" in df.columns:
        observed_count = int(df["observed"].astype(bool).sum())

    return {
        "row_count": total_rows,
        "column_count": len(df.columns),
        "columns": list(df.columns),
        "schema": columns_info,
        "time_bounds": time_bounds,
        "observed_records": observed_count,
        "missing_records": total_rows - observed_count,
        "sentinel_values_detected": sentinels_detected,
        "sentinel_values": sorted(list(sentinels_found)),
    }


def generate_file_sidecar(
    file_path: Path,
    source_name: str,
    country_iso3: str,
    frequency: str,
    endpoint_url: str,
    request_params: dict[str, Any] | None = None,
    latency_sec: float | None = None,
) -> Path:
    """
    Generate sidecar metadata JSON file ({file_path}.meta.json) with rich provenance.
    """
    csv_meta = inspect_csv_metadata(file_path)
    file_hash = compute_sha256(file_path)
    file_size_bytes = file_path.stat().st_size if file_path.exists() else 0

    source_meta = get_source_metadata(source_name)

    # Sanitize request parameters
    sanitized_params = {}
    if request_params:
        for k, v in request_params.items():
            if any(secret_term in k.lower() for secret_term in ["key", "token", "secret", "password", "securitytoken"]):
                sanitized_params[k] = "[REDACTED]"
            else:
                sanitized_params[k] = v

    metadata = {
        "dataset_name": f"{source_name} - {country_iso3}",
        "file_name": file_path.name,
        "source": source_name,
        "country_iso3": country_iso3,
        "country_name": get_country_name(country_iso3),
        "concept": getattr(source_meta, "concept", "raw_data"),
        "frequency": frequency,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": file_hash,
        "size_bytes": file_size_bytes,
        "endpoint_url": sanitize_url(endpoint_url),
        "request_params": sanitized_params,
        "api_latency_seconds": round(latency_sec, 3) if latency_sec is not None else None,
        "attribution": {
            "organization": getattr(source_meta, "organization", source_name),
            "license": getattr(source_meta, "license", "Open Access"),
            "documentation_url": getattr(source_meta, "documentation_url", ""),
            "official_url": getattr(source_meta, "official_url", ""),
        },
        "content_summary": csv_meta,
        "system_provenance": {
            "python_version": sys.version.split()[0],
            "hgt_qf_version": "2.2.0",
        },
    }

    sidecar_path = file_path.with_suffix(".csv.meta.json")
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return sidecar_path


def generate_dataset_manifest(root: Path, mode: str, results: list[Any]) -> Path:
    """
    Compile a standardized dataset manifest conforming to Section 17 of HGT-QF Specification.
    """
    quality_dir = root / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    sidecar_files = list(root.glob("raw/**/*.csv.meta.json"))
    manifest_records = []

    for sidecar in sorted(sidecar_files):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            csv_path = sidecar.parent / sidecar.name.replace(".meta.json", "")
            csv_meta = data.get("content_summary", {})
            time_bounds = csv_meta.get("time_bounds", {})
            attrib = data.get("attribution", {})

            manifest_records.append({
                "dataset_id": f"DS_{data.get('country_iso3')}_{data.get('source', '').replace(' ', '_').replace('/', '_')}",
                "country": data.get("country_name", ""),
                "iso3": data.get("country_iso3", ""),
                "source": data.get("source", ""),
                "concept": data.get("concept", ""),
                "frequency": data.get("frequency", ""),
                "start_date": time_bounds.get("min_time", "N/A"),
                "end_date": time_bounds.get("max_time", "N/A"),
                "record_count": csv_meta.get("row_count", 0),
                "geographic_definition": "Country / Spatial Point Centroid",
                "source_url": data.get("endpoint_url", ""),
                "documentation_url": attrib.get("documentation_url", ""),
                "license": attrib.get("license", "Open Access"),
                "download_timestamp_utc": data.get("generated_at_utc", ""),
                "raw_file_path": str(csv_path.relative_to(root)),
                "file_format": "CSV",
                "sha256": data.get("sha256", ""),
                "status": "SUCCESS",
                "sentinel_values_detected": csv_meta.get("sentinel_values_detected", False),
                "sentinel_values": csv_meta.get("sentinel_values", []),
            })
        except Exception:
            continue

    manifest_path = quality_dir / "dataset_manifest.json"
    manifest_data = {
        "project": "HGT-QF Data Desk",
        "manifest_version": "2.2.0",
        "mode": mode,
        "compiled_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_datasets_tracked": len(manifest_records),
        "datasets": manifest_records,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    # Also save as CSV for quick table viewing
    manifest_csv = quality_dir / "dataset_manifest.csv"
    if manifest_records:
        pd.DataFrame(manifest_records).to_csv(manifest_csv, index=False)
    else:
        pd.DataFrame(columns=["dataset_id", "country", "iso3", "source", "record_count", "sha256"]).to_csv(manifest_csv, index=False)

    return manifest_path
