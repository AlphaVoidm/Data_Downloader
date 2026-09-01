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

# Re-export for selection_manager integration
try:
    from typing import Any as _Any
except ImportError:
    pass

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


def build_enhanced_plan(
    countries: list[str],
    features: list[str],
    start_year: int,
    end_year: int,
    source_mode: str = "automatic",
    source_overrides: dict[str, str] | None = None,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an enhanced acquisition plan using the Selection Manager.

    This integrates with selection_manager.py and provides:
        - Full download plan with validation
        - Per-country, per-feature source resolution
        - Auth status, coverage overlap, fallback chains
        - Pre-download validation

    Returns a dict suitable for GUI rendering.
    """
    from selection_manager import (
        build_download_plan, validate_selection, render_plan_preview,
        MODE_AUTOMATIC, MODE_MANUAL,
    )
    from download_policy import validate_selection_policy, DownloadPolicy

    plan_mode = MODE_MANUAL if source_mode == "manual" else MODE_AUTOMATIC

    # Build the download plan
    validation = validate_selection(
        countries=countries,
        features=features,
        start_year=start_year,
        end_year=end_year,
        source_mode=plan_mode,
        source_overrides=source_overrides,
        credentials=credentials,
    )

    plan = validation["plan"]

    # Check against download policy
    policy_result = validate_selection_policy(validation["summary"])

    # Build the preview
    preview_text = render_plan_preview(plan)

    # Build per-row details for a DataFrame
    rows = []
    for cp in plan.countries:
        for sel in cp.selections:
            # Get fallback info
            try:
                concept_plan = resolve_feature(
                    sel.feature_concept, cp.iso3, start_year, end_year, credentials
                )
                best_idx = next(
                    (i for i, d in enumerate(concept_plan.decisions)
                     if d.source_id == sel.source_id),
                    None,
                )
                after_best = concept_plan.decisions[(best_idx + 1):] if best_idx is not None else []
                fallbacks = [
                    d.source_name for d in after_best
                    if d.status not in ("NOT_SUPPORTED", "UNKNOWN")
                ]
            except Exception:
                fallbacks = []

            rows.append({
                "country": cp.iso3,
                "country_name": cp.country_name,
                "feature": sel.feature_concept,
                "feature_name": sel.feature_name,
                "feature_tier": sel.feature_tier,
                "selected_source": sel.source_name,
                "source_id": sel.source_id,
                "fallback_sources": ";".join(fallbacks),
                "coverage_status": _STATUS_LABELS.get(sel.coverage_status, sel.coverage_status),
                "frequency": sel.frequency,
                "auth_required": sel.auth_required,
                "auth_satisfied": sel.auth_satisfied,
                "period_overlap_months": sel.period_overlap_months,
                "reason": sel.reason,
                "method": _method_for(sel.source_id),
            })

    df = pd.DataFrame(rows) if rows else pd.DataFrame()

    return {
        "valid": validation["valid"],
        "errors": validation["errors"],
        "warnings": validation["warnings"] + policy_result.warnings,
        "policy_proceed": policy_result.proceed,
        "policy_errors": policy_result.errors,
        "summary": validation["summary"],
        "plan": plan,
        "preview_text": preview_text,
        "dataframe": df,
    }


__all__ = [
    "build_acquisition_plan", "render_acquisition_plan",
    "build_enhanced_plan",
]
