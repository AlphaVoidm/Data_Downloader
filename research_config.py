"""Researcher-adjustable readiness configuration (discovery/audit thresholds)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent / "config"
RESEARCH_CONFIG_JSON = CONFIG_DIR / "research_config.json"


@dataclass
class ResearchConfig:
    min_history_months: int = 120
    min_consecutive_months: int = 120
    require_monthly_resolution: bool = True
    min_core_coverage: float = 0.8
    min_extended_coverage: float = 0.0
    require_optional_features: bool = False
    experiment_windows: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_history_months": self.min_history_months,
            "min_consecutive_months": self.min_consecutive_months,
            "require_monthly_resolution": self.require_monthly_resolution,
            "min_core_coverage": self.min_core_coverage,
            "min_extended_coverage": self.min_extended_coverage,
            "require_optional_features": self.require_optional_features,
            "experiment_windows": self.experiment_windows,
        }


def load_research_config() -> ResearchConfig:
    if RESEARCH_CONFIG_JSON.exists():
        cfg = json.loads(RESEARCH_CONFIG_JSON.read_text(encoding="utf-8"))
        target = cfg.get("target", {})
        coverage = cfg.get("feature_coverage", {})
        return ResearchConfig(
            min_history_months=int(target.get("min_history_months", 120)),
            min_consecutive_months=int(target.get("min_consecutive_months", 120)),
            require_monthly_resolution=bool(target.get("require_monthly_resolution", True)),
            min_core_coverage=float(coverage.get("min_core_coverage", 0.8)),
            min_extended_coverage=float(coverage.get("min_extended_coverage", 0.0)),
            require_optional_features=bool(coverage.get("require_optional_features", False)),
            experiment_windows=cfg.get("experiment_windows", {}),
        )
    return ResearchConfig()


def build_research_config(
    min_history_months: int | None = None,
    min_consecutive_months: int | None = None,
    min_core_coverage: float | None = None,
    min_extended_coverage: float | None = None,
    require_optional_features: bool | None = None,
) -> ResearchConfig:
    """Merge researcher overrides (e.g. CLI flags) onto the file defaults."""
    cfg = load_research_config()
    if min_history_months is not None:
        cfg.min_history_months = int(min_history_months)
    if min_consecutive_months is not None:
        cfg.min_consecutive_months = int(min_consecutive_months)
    if min_core_coverage is not None:
        cfg.min_core_coverage = float(min_core_coverage)
    if min_extended_coverage is not None:
        cfg.min_extended_coverage = float(min_extended_coverage)
    if require_optional_features is not None:
        cfg.require_optional_features = bool(require_optional_features)
    return cfg


__all__ = ["ResearchConfig", "load_research_config", "build_research_config"]
