"""Dry-run source resolution (the `plan` command).

For every requested country × feature, show — WITHOUT downloading — which
source will be used, why others were skipped, what frequency/auth/method is
expected, and the discovery status. Lets the researcher review the acquisition
plan before spending bandwidth/storage.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from coverage_engine import resolve_feature
from feature_registry import resolve_feature_concept
from source_registry import get_source
from status_vocabulary import source_status

_STATUS_LABELS = {
    "SUPPORTED": "READY",
    "AUTH_REQUIRED": "AUTH_NEEDED",
    "MAPPING_REQUIRED": "MAPPING_NEEDED",
    "NOT_SUPPORTED": "NOT_COVERED",
    "TEMPORARILY_UNAVAILABLE": "UNAVAILABLE",
    "UNKNOWN": "UNKNOWN",
}


def _method_for(source_id: str) -> str:
    if source_id == "era5":
        return "API + server-side bbox subset"
    if source_id == "nasa_power":
        return "API point query"
    if source_id in ("entsoe", "eia", "ember", "world_bank", "neso"):
        return "API"
    if source_id == "aemo":
        return "bulk NEMWEB (deferred)"
    return "deferred/bulk"


def build_acquisition_plan(
    countries: list[str],
    features: list[str],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> pd.DataFrame:
    rows = []
    for iso3 in countries:
        for raw_feature in features:
            concept = resolve_feature_concept(raw_feature)
            plan = resolve_feature(concept, iso3, start_year, end_year, credentials)
            src = get_source(plan.best_source_id) if plan.best_source_id else None

            # Fallback chain: every candidate the engine would try AFTER the
            # selected source (i.e. not a source known to lack this
            # country/feature). Indexed by the engine's best-source choice.
            best_idx = next(
                (i for i, d in enumerate(plan.decisions)
                 if d.source_id == plan.best_source_id),
                None,
            )
            after_best = plan.decisions[(best_idx + 1):] if best_idx is not None else []
            fallbacks = [
                d.source_name for d in after_best
                if d.status not in ("NOT_SUPPORTED", "UNKNOWN")
            ]

            rows.append({
                "country": iso3,
                "feature": concept,
                "selected_source": plan.best_source_name,
                "fallback_sources": ";".join(fallbacks),
                "coverage_status": _STATUS_LABELS.get(plan.best_status, plan.best_status),
                "frequency": plan.best_frequency,
                "authentication": (src.auth_type if src else ""),
                "method": _method_for(plan.best_source_id or ""),
                "source_status": source_status(plan.best_status),
                "per_source_statuses": ";".join(
                    f"{d.source_id}:{source_status(d.status)}" for d in plan.decisions
                ),
            })
    return pd.DataFrame(rows)


def render_acquisition_plan(plan: pd.DataFrame) -> str:
    if plan.empty:
        return "No plan rows produced."
    lines: list[str] = []
    lines.append("")
    lines.append("ACQUISITION PLAN (dry-run — no downloads)")
    lines.append("=" * 96)
    lines.append(f"{'COUNTRY':<8} {'FEATURE':<24} {'SELECTED':<16} {'STATUS':<14} {'FREQ':<12} {'AUTH':<8} {'METHOD':<26}")
    lines.append("-" * 96)
    for _, r in plan.iterrows():
        lines.append(
            f"{r['country']:<8} {r['feature'][:23]:<24} {str(r['selected_source'])[:15]:<16} "
            f"{r['coverage_status']:<14} {r['frequency'][:11]:<12} {r['authentication']:<8} {r['method'][:25]:<26}"
        )
    return "\n".join(lines)


__all__ = ["build_acquisition_plan", "render_acquisition_plan"]
