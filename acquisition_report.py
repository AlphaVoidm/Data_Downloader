"""REPORT B — Acquisition report + provenance (spec §13, §17).

Every downloaded dataset gets full provenance: country, ISO3, feature, source,
dataset, source identifier, source URL, endpoint, requested/actual period,
frequency, units, record count, retrieval timestamp, auth status, verification
status, source revision, and checksum where practical.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from source_registry import get_source


def _attempts_summary(r: Any) -> str:
    """Compress the full per-source attempt history into one provenance field."""
    if not getattr(r, "attempts", None):
        return ""
    parts = []
    for a in r.attempts:
        status = a.get("failure_reason") or a.get("verification") or "?"
        parts.append(f"{a.get('source', '?')}={status}")
    return " -> ".join(parts)


def _sha256(path: str) -> str:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_report_b(results: list[Any]) -> pd.DataFrame:
    rows = []
    for r in results:
        src = get_source(r.source_id)
        rows.append({
            "country": r.country_name,
            "iso3": r.country,
            "feature": r.concept,
            "feature_name": r.name,
            "role": r.role,
            "source": r.source_name,
            "dataset": (src.dataset_name if src else ""),
            "source_identifier": (src.dataset_id if src else ""),
            "source_url": (src.endpoint if src else ""),
            "requested_period": f"{r.requested_start} / {r.requested_end}",
            "received_period": f"{r.received_start} / {r.received_end}",
            "frequency": r.frequency,
            "units": r.unit,
            "records": r.records,
            "status": r.status,
            "verification_status": r.verification_status,
            "verification_notes": " | ".join(r.verification_notes),
            "attempts": _attempts_summary(r),
            "failure_reason": r.failure_reason,
            "retrieval_timestamp_utc": r.retrieved_at,
            "checksum_sha256": _sha256(r.path),
            "output_path": r.path,
        })
    return pd.DataFrame(rows)


def generate_acquisition_report(
    root: Path | str,
    results: list[Any],
) -> dict[str, Path]:
    root = Path(root)
    meta = root / "metadata"
    meta.mkdir(parents=True, exist_ok=True)

    df = build_report_b(results)
    csv_path = meta / "report_B_acquisition.csv"
    df.to_csv(csv_path, index=False)

    acquired = df[df["status"].isin(("SUCCESS", "PARTIAL_SUCCESS"))]
    provenance = {
        "project": "HGT-QF Data Desk",
        "compiled_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_attempted": len(df),
        "total_acquired": len(acquired),
        "status_summary": df["status"].value_counts().to_dict(),
        "variables": df.to_dict(orient="records"),
    }
    json_path = meta / "provenance.json"
    json_path.write_text(json.dumps(provenance, indent=2, default=str), encoding="utf-8")
    return {"report_B_csv": csv_path, "provenance_json": json_path}


__all__ = ["build_report_b", "generate_acquisition_report"]
