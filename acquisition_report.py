"""Component 7 — Provenance & Quality Report for HGT-QF.

Consolidates every acquired variable into a single audit trail so the research
project can reproduce *where every variable came from*:

    metadata/acquisition_report.csv   one row per acquired variable
    metadata/provenance.json          full provenance + per-file sidecars
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from source_registry import get_source_metadata


def _sha256(path: Path) -> str:
    if not path or not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_acquisition_records(results: list[Any]) -> list[dict[str, Any]]:
    """Convert AcquisitionResults into the Component-7 provenance schema."""
    records = []
    for r in results:
        meta = get_source_metadata(r.source)
        records.append({
            "country": r.country_name,
            "iso3": r.country,
            "feature": r.concept,
            "feature_id": r.feature_id,
            "feature_name": r.feature_name,
            "source": r.source,
            "dataset": getattr(meta, "dataset_name", "") if meta else "",
            "frequency": r.frequency or (getattr(meta, "native_frequency", "") if meta else ""),
            "start": getattr(r, "start_year", ""),
            "end": getattr(r, "end_year", ""),
            "units": getattr(meta, "unit", "") if meta else "",
            "records": r.records,
            "download_timestamp_utc": r.retrieved_at,
            "source_url": getattr(meta, "official_url", "") if meta else r.source_url,
            "doi": getattr(r, "doi", "") or (getattr(meta, "doi", "") if meta and hasattr(meta, "doi") else ""),
            "license": getattr(meta, "license", "") if meta else "",
            "status": r.status,
            "skip_reason": getattr(r, "skip_reason", ""),
            "output_path": r.path,
        })
    return records


def generate_acquisition_report(
    root: Path | str,
    results: list[Any],
) -> dict[str, Path]:
    """Write acquisition_report.csv and provenance.json under <root>/metadata."""
    root = Path(root)
    meta_dir = root / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)

    records = build_acquisition_records(results)
    df = pd.DataFrame(records)
    csv_path = meta_dir / "acquisition_report.csv"
    df.to_csv(csv_path, index=False)

    successful = [r for r in records if r["status"] in ("SUCCESS", "PARTIAL_SUCCESS")]
    provenance = {
        "project": "HGT-QF Data Desk",
        "compiled_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_variables_attempted": len(records),
        "total_variables_acquired": len(successful),
        "status_summary": {
            status: int((df["status"] == status).sum()) for status in df["status"].unique()
        },
        "variables": records,
    }
    json_path = meta_dir / "provenance.json"
    json_path.write_text(json.dumps(provenance, indent=2, default=str), encoding="utf-8")
    return {"acquisition_report_csv": csv_path, "provenance_json": json_path}


def write_climate_sidecar(
    data_path: Path,
    source: str,
    country_iso3: str,
    variables: list[str],
    records: int,
    request: dict[str, Any] | None = None,
) -> Path:
    """Write a provenance sidecar for a compact climate Parquet/CSV output."""
    meta = get_source_metadata(source)
    sidecar = {
        "dataset_name": f"{source} - {country_iso3}",
        "file_name": data_path.name,
        "source": source,
        "country_iso3": country_iso3,
        "concept": "climate_reanalysis",
        "frequency": "monthly",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": _sha256(data_path),
        "size_bytes": data_path.stat().st_size if data_path.exists() else 0,
        "variables": variables,
        "records": records,
        "request": request or {},
        "attribution": {
            "organization": getattr(meta, "organization", source) if meta else source,
            "license": getattr(meta, "license", "") if meta else "",
            "documentation_url": getattr(meta, "documentation_url", "") if meta else "",
            "official_url": getattr(meta, "official_url", "") if meta else "",
        },
        "extraction": "country-level monthly aggregate (area-weighted, cos-latitude); temporary bulk NetCDF deleted",
    }
    sidecar_path = data_path.with_suffix(data_path.suffix + ".meta.json")
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return sidecar_path


__all__ = [
    "build_acquisition_records", "generate_acquisition_report", "write_climate_sidecar",
]
