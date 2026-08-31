"""Component 4 — Coverage Engine for HGT-QF.

Deterministic, zero-network evaluation of:

    country x feature x source x period  ->  availability decision

It consumes the three registries (Country, Feature, Source) and produces the
coverage matrix *before* anything is downloaded. This is the piece that stops
the system from requesting data it already knows does not exist (e.g. asking
ENTSO-E for Egypt, or AEMO for France).

Decision statuses:

    AVAILABLE            data exists, in period, at the required (or finer) frequency
    PARTIAL_AVAILABLE    data exists but only covers part of the period, or is
                         coarser than the required frequency (e.g. annual-only)
    NOT_COVERED          the source does not cover this country
    VARIABLE_NOT_AVAILABLE  the source covers the country but not this variable
    PERIOD_NOT_AVAILABLE the source's temporal range does not overlap the request
    ACCESS_REQUIRES_AUTH data exists but a credential is required and not provided
    UNKNOWN              the source is not registered
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from country_registry import COUNTRY_REGISTRY, get_country_record
from feature_registry import FEATURE_REGISTRY, FeatureSpec, get_all_features
from source_mapping import EUROPEAN_ISO3, validate_source_capability
from source_registry import SOURCE_REGISTRY, SourceMetadata, get_source_metadata

CONFIG_DIR = Path(__file__).parent / "config"
if not (CONFIG_DIR / "ember_monthly_geographies.csv").exists():
    CONFIG_DIR = Path(__file__).parent
EMBER_MONTHLY_CSV = CONFIG_DIR / "ember_monthly_geographies.csv"

# Frequency resolution ranking (higher = finer). Used to test whether a source's
# native frequency satisfies a feature's required frequency.
FREQUENCY_RANK: dict[str, int] = {
    "annual": 1,
    "yearly": 1,
    "five-year": 1,
    "quarterly": 2,
    "monthly": 3,
    "daily": 4,
    "hourly": 5,
    "half-hourly": 6,
    "five-minute": 7,
    "sub-hourly": 7,
}

# --- Status constants -------------------------------------------------------
AVAILABLE = "AVAILABLE"
PARTIAL_AVAILABLE = "PARTIAL_AVAILABLE"
NOT_COVERED = "NOT_COVERED"
VARIABLE_NOT_AVAILABLE = "VARIABLE_NOT_AVAILABLE"
PERIOD_NOT_AVAILABLE = "PERIOD_NOT_AVAILABLE"
ACCESS_REQUIRES_AUTH = "ACCESS_REQUIRES_AUTH"
UNKNOWN = "UNKNOWN"

STATUS_PRIORITY: dict[str, int] = {
    AVAILABLE: 6,
    PARTIAL_AVAILABLE: 5,
    ACCESS_REQUIRES_AUTH: 4,
    PERIOD_NOT_AVAILABLE: 3,
    VARIABLE_NOT_AVAILABLE: 2,
    NOT_COVERED: 1,
    UNKNOWN: 0,
}

STATUS_BADGES: dict[str, str] = {
    AVAILABLE: "🟢 AVAILABLE",
    PARTIAL_AVAILABLE: "🟡 PARTIAL",
    NOT_COVERED: "⚪ NOT_COVERED",
    VARIABLE_NOT_AVAILABLE: "🟣 VARIABLE_NOT_AVAILABLE",
    PERIOD_NOT_AVAILABLE: "⏳ PERIOD_NOT_AVAILABLE",
    ACCESS_REQUIRES_AUTH: "🔑 ACCESS_REQUIRES_AUTH",
    UNKNOWN: "❓ UNKNOWN",
}

# Default Ember *monthly* geography set (approximation; overridable via
# config/ember_monthly_geographies.csv). Ember annual coverage is global.
_DEFAULT_EMBER_MONTHLY: frozenset[str] = frozenset({
    # Europe (EU-27 + GB + EFTA + Balkans + TR + UA)
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA",
    "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD",
    "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE", "GBR", "NOR", "CHE",
    "ALB", "BIH", "MNE", "MKD", "SRB", "XKX", "TUR", "UKR", "MDA", "ISL",
    # Americas
    "USA", "CAN", "MEX", "BRA", "ARG", "CHL", "COL", "PER", "URY", "VEN",
    "ECU", "PAN", "CRI", "DOM", "GTM", "PRI",
    # Asia-Pacific
    "CHN", "JPN", "KOR", "IND", "IDN", "MYS", "PHL", "THA", "VNM", "SGP",
    "AUS", "NZL", "PAK", "BGD", "LKA", "KAZ", "TWN", "HKG",
    # Middle East & North Africa
    "SAU", "ARE", "IRN", "IRQ", "ISR", "JOR", "KWT", "OMN", "QAT", "LBN",
    "EGY", "DZA", "MAR", "TUN", "LBY",
    # Sub-Saharan Africa
    "ZAF", "NGA", "KEN", "ETH", "GHA", "SEN", "CIV", "TZA", "UGA", "ZMB",
    "ZWE", "MOZ", "COD", "CMR", "AGO", "BWA", "NAM", "SOM", "SDN", "RWA",
})


@dataclass
class CoverageDecision:
    source: str
    status: str
    status_badge: str
    reason: str
    frequency: str = ""
    start_year: int = 0
    end_year: int = 0
    auth_required: str = "none"
    credential_env: str = ""
    access_satisfied: bool = True
    dataset_type: str = "tabular"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class FeatureCoveragePlan:
    feature_id: str
    concept: str
    feature_name: str
    country: str
    required_frequency: str
    availability_class: str
    is_target: bool
    is_derived: bool
    decisions: list[CoverageDecision] = field(default_factory=list)
    best_source: str = ""
    best_status: str = NOT_COVERED
    best_frequency: str = ""
    derived_from_feature: str = ""

    @property
    def is_available(self) -> bool:
        return self.best_status in (AVAILABLE, PARTIAL_AVAILABLE)

    @property
    def is_fully_available(self) -> bool:
        return self.best_status == AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "concept": self.concept,
            "feature_name": self.feature_name,
            "country": self.country,
            "required_frequency": self.required_frequency,
            "availability_class": self.availability_class,
            "is_target": self.is_target,
            "is_derived": self.is_derived,
            "best_source": self.best_source,
            "best_status": self.best_status,
            "best_frequency": self.best_frequency,
            "derived_from_feature": self.derived_from_feature,
            "decisions": [d.to_dict() for d in self.decisions],
        }


# ---------------------------------------------------------------------------
# Ember monthly geography set
# ---------------------------------------------------------------------------

def load_ember_monthly_geographies() -> frozenset[str]:
    if EMBER_MONTHLY_CSV.exists():
        geos: set[str] = set()
        with EMBER_MONTHLY_CSV.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip().upper()
                if not line or line.startswith("#") or line == "ISO3":
                    continue
                for token in line.split(","):
                    token = token.strip()
                    if token:
                        geos.add(token)
        if geos:
            return frozenset(geos)
    return _DEFAULT_EMBER_MONTHLY


EMBER_MONTHLY_GEOGRAPHIES = load_ember_monthly_geographies()

# EU-27 (post-Brexit) + UK + EFTA — used for Eurostat / EU-only products.
EU27_PLUS: frozenset[str] = frozenset({
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA",
    "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD",
    "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE", "GBR", "NOR", "CHE",
    "ISL", "LIE",
})

# Countries with EV stock/sales data in OWID's "electric car stock" dataset
# (approximation; reconcile against the OWID catalogue).
OWID_EV_ISO3: frozenset[str] = frozenset({
    "USA", "CAN", "MEX", "BRA", "ARG", "CHL", "COL", "CRI", "PAN", "URY",
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA",
    "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD",
    "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE", "GBR", "NOR", "CHE",
    "ISL", "TUR", "UKR", "RUS",
    "CHN", "JPN", "KOR", "IND", "IDN", "MYS", "PHL", "THA", "VNM", "SGP",
    "AUS", "NZL", "PAK", "TWN", "HKG",
    "ISR", "ARE", "SAU", "QAT", "ZAF", "MAR", "EGY",
})

# Named country sets referenced via coverage_scope="set:<name>".
SCOPE_SETS: dict[str, frozenset[str]] = {
    "owid_ev": OWID_EV_ISO3,
}


# ---------------------------------------------------------------------------
# Frequency helpers
# ---------------------------------------------------------------------------

def frequency_rank(freq: str) -> int:
    return FREQUENCY_RANK.get((freq or "").strip().lower(), 0)


def frequency_satisfies(required: str, offered: str) -> tuple[bool, str]:
    """Return whether `offered` satisfies `required`, plus a note."""
    req = (required or "").strip().lower()
    off = (offered or "").strip().lower()
    if not off:
        return False, "unknown-frequency"
    # A source offering multiple frequencies is treated by its finest one.
    offs = [f.strip() for f in off.split(";") if f.strip()]
    finest = max((frequency_rank(f) for f in offs), default=0)
    if finest >= frequency_rank(req):
        return True, "satisfies"
    return False, "coarser-than-required"


# ---------------------------------------------------------------------------
# Geographic coverage
# ---------------------------------------------------------------------------

def geographic_coverage(meta: SourceMetadata, iso3: str) -> tuple[bool, str]:
    """Decide whether a source covers a country, independent of variable/period."""
    scope = meta.coverage_scope
    iso3 = iso3.strip().upper()
    if scope == "global":
        return True, f"Global coverage ({meta.coverage_countries_approx or 'all countries'})"
    if scope == "national:usa":
        return iso3 == "USA", "U.S. only"
    if scope == "national:gbr":
        return iso3 == "GBR", "Great Britain only"
    if scope == "national:aus":
        return iso3 == "AUS", "Australia (NEM) only"
    if scope == "regional:europe":
        return iso3 in EUROPEAN_ISO3, "European perimeter only"
    if scope == "regional:eu27":
        return iso3 in EU27_PLUS, "EU-27 / EFTA / GB only"
    if scope.startswith("set:"):
        set_name = scope.split(":", 1)[1]
        members = SCOPE_SETS.get(set_name)
        if members is None:
            return False, f"Unknown country set '{set_name}'"
        return iso3 in members, f"Country set '{set_name}' only"
    if scope == "regional:europe_entsoe":
        status, msg = validate_source_capability(iso3, "ENTSO-E Transparency")
        return status == "OK", msg
    # Fall back to the shared capability validator for national operators.
    status, msg = validate_source_capability(iso3, meta.source)
    if status == "OK":
        return True, msg
    if status in ("SOURCE_NOT_COVERED",):
        return False, msg
    if status == "MAPPING_MISSING":
        return False, msg
    if status == "RESEARCH_TIER":
        return True, msg
    return False, msg


def _covers_ember_monthly(meta: SourceMetadata, iso3: str) -> bool:
    """Ember monthly coverage is narrower than its annual (global) coverage."""
    return iso3 in EMBER_MONTHLY_GEOGRAPHIES


# ---------------------------------------------------------------------------
# Access check
# ---------------------------------------------------------------------------

def credential_is_set(meta: SourceMetadata, credentials: dict[str, str] | None) -> bool:
    if meta.auth_required in (None, "", "none"):
        return True
    if meta.auth_required == "restricted":
        # Subscription/licensing-based access: not satisfiable via an env key.
        return False
    if credentials and credentials.get(meta.credential_env):
        return True
    if meta.credential_env and os.getenv(meta.credential_env):
        return True
    return False


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_source(
    source_name: str,
    feature: FeatureSpec,
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> CoverageDecision:
    """Evaluate one source for one feature x country x period (no network)."""
    country_iso3 = country_iso3.strip().upper()
    meta = get_source_metadata(source_name)
    if meta is None:
        return CoverageDecision(
            source=source_name, status=UNKNOWN, status_badge=STATUS_BADGES[UNKNOWN],
            reason=f"{source_name} is not present in the source registry",
        )

    decision = CoverageDecision(
        source=source_name,
        status=UNKNOWN,
        status_badge=STATUS_BADGES[UNKNOWN],
        reason="",
        frequency=meta.coverage_frequency or meta.native_frequency,
        start_year=meta.historical_start,
        end_year=meta.historical_end,
        auth_required=meta.auth_required,
        credential_env=meta.credential_env,
        dataset_type=meta.dataset_type,
    )

    # 1. Geographic coverage
    covered, geo_msg = geographic_coverage(meta, country_iso3)
    if not covered:
        decision.status = NOT_COVERED
        decision.status_badge = STATUS_BADGES[NOT_COVERED]
        decision.reason = f"Country unsupported: {geo_msg}"
        return decision

    # 2. Variable availability
    variables = {v.strip().lower() for v in meta.variables.split(",")} if meta.variables else set()
    concept_norm = feature.concept.strip().lower()
    if meta.concept.strip().lower() != concept_norm and concept_norm not in variables:
        decision.status = VARIABLE_NOT_AVAILABLE
        decision.status_badge = STATUS_BADGES[VARIABLE_NOT_AVAILABLE]
        decision.reason = f"{source_name} does not publish '{feature.concept}'"
        return decision

    # Ember: monthly is a narrower geography than annual.
    if meta.source == "Ember" and feature.concept == "electricity_demand":
        if not _covers_ember_monthly(meta, country_iso3):
            decision.frequency = "annual"
            decision.status = PARTIAL_AVAILABLE
            decision.status_badge = STATUS_BADGES[PARTIAL_AVAILABLE]
            decision.reason = "Ember annual data available (monthly not published for this geography)"
            decision.access_satisfied = credential_is_set(meta, credentials)
            return decision

    # 3. Temporal coverage
    hs, he = meta.historical_start, meta.historical_end
    if end_year < hs or start_year > he:
        decision.status = PERIOD_NOT_AVAILABLE
        decision.status_badge = STATUS_BADGES[PERIOD_NOT_AVAILABLE]
        decision.reason = f"Period {start_year}-{end_year} outside source range {hs}-{he}"
        return decision
    if start_year < hs or end_year > he:
        decision.status = PARTIAL_AVAILABLE
        decision.status_badge = STATUS_BADGES[PARTIAL_AVAILABLE]
        decision.reason = f"Partial period overlap (source covers {hs}-{he}; requested {start_year}-{end_year})"
    else:
        decision.status = AVAILABLE
        decision.status_badge = STATUS_BADGES[AVAILABLE]
        decision.reason = f"Covered: {geo_msg}"

    # 4. Frequency check (informational; coarser frequency downgrades to PARTIAL)
    if decision.status == AVAILABLE:
        ok, note = frequency_satisfies(feature.required_frequency, decision.frequency)
        if not ok:
            decision.status = PARTIAL_AVAILABLE
            decision.status_badge = STATUS_BADGES[PARTIAL_AVAILABLE]
            decision.reason = f"Frequency {decision.frequency} is coarser than required {feature.required_frequency}"

    # 5. Access check
    decision.access_satisfied = credential_is_set(meta, credentials)
    if decision.status in (AVAILABLE, PARTIAL_AVAILABLE) and not decision.access_satisfied:
        decision.status = ACCESS_REQUIRES_AUTH
        decision.status_badge = STATUS_BADGES[ACCESS_REQUIRES_AUTH]
        if meta.auth_required == "restricted":
            decision.reason = "Data exists but programmatic access is restricted (subscription/licensing)"
        else:
            decision.reason = (
                f"Data exists but requires credential "
                f"({meta.credential_env or meta.auth_required}); none provided"
            )
    return decision


def resolve_feature(
    feature_id: str,
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> FeatureCoveragePlan:
    """Walk a feature's ordered source candidates and pick the best available."""
    feature = FEATURE_REGISTRY.get(feature_id)
    country_iso3 = country_iso3.strip().upper()
    if feature is None:
        raise KeyError(f"Unknown feature id: {feature_id}")

    plan = FeatureCoveragePlan(
        feature_id=feature.feature_id,
        concept=feature.concept,
        feature_name=feature.feature_name,
        country=country_iso3,
        required_frequency=feature.required_frequency,
        availability_class=feature.availability_class,
        is_target=feature.is_target,
        is_derived=feature.is_derived,
        derived_from_feature=feature.derived_from,
    )

    # Derived features inherit availability from their underlying feature.
    if feature.is_derived and feature.derived_from:
        parent_id = feature.derived_from
        parent = FEATURE_REGISTRY.get(parent_id)
        if parent is not None:
            parent_plan = resolve_feature(parent_id, country_iso3, start_year, end_year, credentials)
            if parent_plan.is_available:
                plan.best_source = parent_plan.best_source
                plan.best_status = AVAILABLE
                plan.best_frequency = parent_plan.best_frequency
                plan.decisions = parent_plan.decisions
                return plan
        plan.best_source = ""
        plan.best_status = NOT_COVERED
        return plan

    best: CoverageDecision | None = None
    for source_name in feature.source_candidates:
        decision = evaluate_source(source_name, feature, country_iso3, start_year, end_year, credentials)
        plan.decisions.append(decision)
        if decision.status == AVAILABLE:
            best = decision
            break
        if decision.status == PARTIAL_AVAILABLE and (best is None or best.status != AVAILABLE):
            if best is None or best.status != PARTIAL_AVAILABLE:
                best = decision
        if decision.status == ACCESS_REQUIRES_AUTH and best is None:
            best = decision

    if best is None and plan.decisions:
        # Preserve the highest-priority non-available decision as the reason.
        best = max(plan.decisions, key=lambda d: STATUS_PRIORITY[d.status])

    if best is not None:
        plan.best_source = best.source
        plan.best_status = best.status
        plan.best_frequency = best.frequency
    else:
        plan.best_status = NOT_COVERED
    return plan


def resolve_country(
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    feature_ids: list[str] | None = None,
) -> list[FeatureCoveragePlan]:
    """Resolve every feature for a single country."""
    ids = feature_ids or [f.feature_id for f in get_all_features()]
    return [resolve_feature(fid, country_iso3, start_year, end_year, credentials) for fid in ids]


# ---------------------------------------------------------------------------
# Matrix builders
# ---------------------------------------------------------------------------

def build_coverage_matrix(
    countries: list[str],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    feature_ids: list[str] | None = None,
) -> "pd.DataFrame":
    """Wide coverage matrix: rows = countries, columns = features, values = best source/status."""
    import pandas as pd

    ids = feature_ids or [f.feature_id for f in get_all_features()]
    rows = []
    for iso3 in countries:
        plans = resolve_country(iso3, start_year, end_year, credentials, ids)
        row: dict[str, Any] = {"iso3": iso3}
        for plan in plans:
            if plan.is_available:
                row[plan.feature_id] = plan.best_source or plan.best_status
            else:
                row[plan.feature_id] = plan.best_status
        rows.append(row)
    return pd.DataFrame(rows).set_index("iso3")


def build_feature_detail(
    countries: list[str],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    feature_ids: list[str] | None = None,
) -> "pd.DataFrame":
    """Long-form country x feature coverage detail with source, status, reason."""
    import pandas as pd

    ids = feature_ids or [f.feature_id for f in get_all_features()]
    records = []
    for iso3 in countries:
        for plan in resolve_country(iso3, start_year, end_year, credentials, ids):
            record = {
                "iso3": iso3,
                "country_name": (get_country_record(iso3).country_name if get_country_record(iso3) else iso3),
                "feature_id": plan.feature_id,
                "concept": plan.concept,
                "feature_name": plan.feature_name,
                "required_frequency": plan.required_frequency,
                "availability_class": plan.availability_class,
                "is_target": plan.is_target,
                "best_source": plan.best_source,
                "best_status": plan.best_status,
                "best_frequency": plan.best_frequency,
                "derived_from_feature": plan.derived_from_feature,
            }
            if plan.decisions:
                top = max(plan.decisions, key=lambda d: STATUS_PRIORITY[d.status])
                record["primary_reason"] = top.reason
            else:
                record["primary_reason"] = ""
            records.append(record)
    return pd.DataFrame(records)


def build_source_selection_table(
    countries: list[str],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    feature_ids: list[str] | None = None,
) -> "pd.DataFrame":
    """Exhaustive country x feature x source decision table (the source plan)."""
    import pandas as pd

    ids = feature_ids or [f.feature_id for f in get_all_features()]
    records = []
    for iso3 in countries:
        for plan in resolve_country(iso3, start_year, end_year, credentials, ids):
            for decision in plan.decisions:
                records.append({
                    "iso3": iso3,
                    "feature_id": plan.feature_id,
                    "concept": plan.concept,
                    "source": decision.source,
                    "status": decision.status,
                    "status_badge": decision.status_badge,
                    "reason": decision.reason,
                    "frequency": decision.frequency,
                    "auth_required": decision.auth_required,
                    "access_satisfied": decision.access_satisfied,
                    "dataset_type": decision.dataset_type,
                    "chosen": (decision.source == plan.best_source),
                })
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def summarize_feature_counts(detail_df: "pd.DataFrame") -> dict[str, Any]:
    """Per-feature availability counts for the audit report."""
    summary: dict[str, Any] = {}
    for feature_id, group in detail_df.groupby("feature_id", sort=False):
        first = group.iloc[0]
        available = int((group["best_status"].isin([AVAILABLE, PARTIAL_AVAILABLE])).sum())
        full = int((group["best_status"] == AVAILABLE).sum())
        auth = int((group["best_status"] == ACCESS_REQUIRES_AUTH).sum())
        summary[feature_id] = {
            "concept": first["concept"],
            "feature_name": first["feature_name"],
            "required_frequency": first["required_frequency"],
            "availability_class": first["availability_class"],
            "countries_available": available,
            "countries_full": full,
            "countries_auth_required": auth,
        }
    return summary


def summarize_demand_counts(
    countries: list[str],
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Split electricity-demand availability into monthly-capable vs annual-only."""
    monthly = 0
    annual_only = 0
    none = 0
    for iso3 in countries:
        plan = resolve_feature("VAR_01", iso3, start_year, end_year, credentials)
        finer = [
            d for d in plan.decisions
            if d.status in (AVAILABLE, PARTIAL_AVAILABLE, ACCESS_REQUIRES_AUTH)
            and frequency_satisfies("monthly", d.frequency)[0]
        ]
        annual = [
            d for d in plan.decisions
            if d.status in (AVAILABLE, PARTIAL_AVAILABLE, ACCESS_REQUIRES_AUTH)
        ]
        if finer:
            monthly += 1
        elif annual:
            annual_only += 1
        else:
            none += 1
    return {
        "demand_monthly_capable": monthly,
        "demand_annual_only": annual_only,
        "demand_unavailable": none,
    }


def recommend_countries(
    detail_df: "pd.DataFrame",
    top_n: int = 20,
) -> "pd.DataFrame":
    """Rank countries by weighted feature coverage for the HGT-QF recommendation."""
    import pandas as pd

    class_weight = {"mandatory": 3, "optional": 2, "experimental": 1}
    records = []
    for iso3, group in detail_df.groupby("iso3", sort=False):
        total = len(group)
        available = int((group["best_status"].isin([AVAILABLE, PARTIAL_AVAILABLE])).sum())
        weighted = 0
        max_weight = 0
        mandatory_available = 0
        mandatory_total = 0
        for _, row in group.iterrows():
            w = class_weight.get(row["availability_class"], 1)
            max_weight += w
            if row["best_status"] in (AVAILABLE, PARTIAL_AVAILABLE):
                weighted += w
                if row["availability_class"] == "mandatory":
                    mandatory_available += 1
            if row["availability_class"] == "mandatory":
                mandatory_total += 1
        records.append({
            "iso3": iso3,
            "country_name": group.iloc[0]["country_name"],
            "features_available": available,
            "features_total": total,
            "coverage_ratio": round(available / total, 3) if total else 0.0,
            "weighted_score": round(weighted / max_weight, 3) if max_weight else 0.0,
            "mandatory_available": mandatory_available,
            "mandatory_total": mandatory_total,
        })
    df = pd.DataFrame(records).sort_values(
        by=["weighted_score", "features_available"], ascending=False
    ).reset_index(drop=True)
    return df.head(top_n)


__all__ = [
    "CoverageDecision", "FeatureCoveragePlan",
    "evaluate_source", "resolve_feature", "resolve_country",
    "build_coverage_matrix", "build_feature_detail", "build_source_selection_table",
    "summarize_feature_counts", "summarize_demand_counts", "recommend_countries",
    "frequency_rank", "frequency_satisfies", "credential_is_set",
    "AVAILABLE", "PARTIAL_AVAILABLE", "NOT_COVERED", "VARIABLE_NOT_AVAILABLE",
    "PERIOD_NOT_AVAILABLE", "ACCESS_REQUIRES_AUTH", "UNKNOWN", "STATUS_BADGES",
]
