"""Coverage / Discovery Engine for HGT-QF (redesigned).

Deterministic, registry-driven discovery of which sources support which
country x feature x period — run BEFORE any download. No network requests.

Discovery statuses (per the spec):

    SUPPORTED                 source supports country+feature (eligible candidate)
    NOT_SUPPORTED             source genuinely does not cover this country/feature
    AUTH_REQUIRED             data exists but a credential is required & missing
    MAPPING_REQUIRED          source covers the country but the area/series code is unknown
    TEMPORARILY_UNAVAILABLE   source known offline / no period overlap / known outage
    UNKNOWN                   source not present in the registry

Demand coverage classification (electricity_demand target):

    MONTHLY_SUFFICIENT        a monthly-or-finer source with >= min_history_months
    MONTHLY_PARTIAL           monthly-or-finer source but insufficient history
    ANNUAL_ONLY               only annual data available
    UNAVAILABLE               no supported source
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from country_registry import COUNTRY_REGISTRY, get_country_record
from feature_registry import (
    FEATURE_REGISTRY,
    FeatureSpec,
    get_all_features,
    get_target_feature,
)
from source_registry import SOURCE_REGISTRY, SourceDefinition, get_source

CONFIG_DIR = Path(__file__).parent / "config"
EMBER_MONTHLY_CSV = CONFIG_DIR / "ember_monthly_geographies.csv"
OWID_EV_CSV = CONFIG_DIR / "owid_ev_countries.csv"

# --- Discovery statuses -----------------------------------------------------
SUPPORTED = "SUPPORTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
AUTH_REQUIRED = "AUTH_REQUIRED"
MAPPING_REQUIRED = "MAPPING_REQUIRED"
TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
UNKNOWN = "UNKNOWN"

DISCOVERY_BADGES: dict[str, str] = {
    SUPPORTED: "🟢 SUPPORTED",
    NOT_SUPPORTED: "⚪ NOT_SUPPORTED",
    AUTH_REQUIRED: "🔑 AUTH_REQUIRED",
    MAPPING_REQUIRED: "🔵 MAPPING_REQUIRED",
    TEMPORARILY_UNAVAILABLE: "🟠 TEMPORARILY_UNAVAILABLE",
    UNKNOWN: "❓ UNKNOWN",
}

DISCOVERY_PRIORITY: dict[str, int] = {
    SUPPORTED: 6,
    AUTH_REQUIRED: 5,
    MAPPING_REQUIRED: 4,
    TEMPORARILY_UNAVAILABLE: 3,
    NOT_SUPPORTED: 2,
    UNKNOWN: 1,
}

# --- Demand classification ---------------------------------------------------
MONTHLY_SUFFICIENT = "MONTHLY_SUFFICIENT"
MONTHLY_PARTIAL = "MONTHLY_PARTIAL"
ANNUAL_ONLY = "ANNUAL_ONLY"
UNAVAILABLE = "UNAVAILABLE"

DEMAND_BADGES: dict[str, str] = {
    MONTHLY_SUFFICIENT: "🟢 MONTHLY_SUFFICIENT",
    MONTHLY_PARTIAL: "🟡 MONTHLY_PARTIAL",
    ANNUAL_ONLY: "🟠 ANNUAL_ONLY",
    UNAVAILABLE: "⚪ UNAVAILABLE",
}

# Frequency resolution ranking (higher = finer).
FREQUENCY_RANK: dict[str, int] = {
    "annual": 1, "yearly": 1, "five-year": 1, "quarterly": 2,
    "monthly": 3, "daily": 4, "hourly": 5, "half-hourly": 6, "five-minute": 7,
}

MIN_MONTHLY_RANK = FREQUENCY_RANK["monthly"]


def frequency_rank(freq: str) -> int:
    return FREQUENCY_RANK.get((freq or "").strip().lower(), 0)


def frequency_satisfies(required: str, offered: str) -> bool:
    offs = [f.strip().lower() for f in (offered or "").split(";") if f.strip()]
    finest = max((frequency_rank(f) for f in offs), default=0)
    return finest >= frequency_rank(required)


def _months_between(start_year: int, end_year: int) -> int:
    return max(0, (end_year - start_year + 1) * 12)


# ---------------------------------------------------------------------------
# Country-set loading
# ---------------------------------------------------------------------------

def _load_country_set(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().upper()
        if not line or line.startswith("#") or line == "ISO3":
            continue
        for token in line.split(","):
            token = token.strip()
            if token:
                out.add(token)
    return frozenset(out)


EMBER_MONTHLY_GEOGRAPHIES = _load_country_set(EMBER_MONTHLY_CSV)
OWID_EV_COUNTRIES = _load_country_set(OWID_EV_CSV)

EU27_PLUS: frozenset[str] = frozenset({
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA",
    "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD",
    "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE", "GBR", "NOR", "CHE",
    "ISL", "LIE",
})

_EUROPEAN_ISO3: frozenset[str] = EU27_PLUS | frozenset({
    "ALB", "BIH", "MNE", "MKD", "SRB", "XKX", "UKR", "MDA", "GEO", "TUR",
})

# Ember features whose *monthly* geography is narrower than annual.
EMBER_FREQ_SENSITIVE_FEATURES = {
    "electricity_demand", "total_electricity_generation",
    "renewable_generation_share", "generation_mix",
}


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def credential_is_set(source_id: str, credentials: dict[str, str] | None) -> bool:
    import os
    src = get_source(source_id)
    if src is None:
        return False
    if not src.auth_required:
        return True
    if src.auth_type == "restricted":
        return False  # subscription/licensing — no env key satisfies it
    env = src.auth_env
    if credentials and credentials.get(env):
        return True
    if env and os.getenv(env):
        return True
    return False


# ---------------------------------------------------------------------------
# Geographic capability per source
# ---------------------------------------------------------------------------

def _eic_code_for(country_iso3: str) -> str | None:
    from source_mapping import get_primary_area_code
    return get_primary_area_code(country_iso3, "ENTSO-E Transparency")


def geographic_capability(src: SourceDefinition, country_iso3: str, concept: str) -> tuple[bool, str, str]:
    """Return (covered, freq_hint, reason)."""
    iso3 = country_iso3.strip().upper()
    sid = src.source_id

    if sid == "entsoe":
        if _eic_code_for(iso3):
            return True, "hourly", f"Verified EIC area code for {iso3}"
        if iso3 in _EUROPEAN_ISO3:
            return True, "hourly", f"European perimeter, but EIC area code not registered for {iso3}"
        return False, "", f"ENTSO-E covers European bidding zones only, not {iso3}"

    if sid == "eia":
        return (iso3 == "USA"), "hourly", ("U.S. EIA covers USA" if iso3 == "USA" else f"EIA covers USA only, not {iso3}")

    if sid == "neso":
        return (iso3 == "GBR"), "half-hourly", ("NESO covers Great Britain" if iso3 == "GBR" else f"NESO covers GBR only, not {iso3}")

    if sid == "aemo":
        return (iso3 == "AUS"), "five-minute", ("AEMO covers Australia NEM" if iso3 == "AUS" else f"AEMO covers AUS only, not {iso3}")

    if sid == "ember":
        if concept in EMBER_FREQ_SENSITIVE_FEATURES:
            if iso3 in EMBER_MONTHLY_GEOGRAPHIES:
                return True, "monthly", "Ember monthly electricity data"
            return True, "annual", "Ember annual data only (no monthly for this geography)"
        return True, "monthly", "Ember global coverage"

    if sid == "world_bank":
        return True, "annual", "World Bank WDI global coverage"

    if sid == "nasa_power":
        rec = get_country_record(iso3)
        if rec:
            return True, "daily", f"NASA POWER point query at centroid ({rec.centroid_lat:.2f},{rec.centroid_lon:.2f})"
        return True, "daily", "NASA POWER global coverage (centroid not registered)"

    if sid == "era5":
        rec = get_country_record(iso3)
        if rec:
            return True, "monthly", f"ERA5 bbox subset available for {iso3}"
        return True, "monthly", "ERA5 global coverage (bbox not registered)"

    if sid == "nager":
        return True, "annual", "Nager.Date holiday coverage"

    if sid == "eurostat":
        return (iso3 in EU27_PLUS), "monthly", ("Eurostat EU/EFTA coverage" if iso3 in EU27_PLUS else f"Eurostat covers EU-27/EFTA/GB only, not {iso3}")

    if sid == "owid":
        return (iso3 in OWID_EV_COUNTRIES), "annual", ("OWID EV stock coverage" if iso3 in OWID_EV_COUNTRIES else f"OWID EV data not present for {iso3}")

    if sid == "irena":
        return True, "annual", "IRENA global renewable statistics (annual)"

    if sid == "iea":
        return True, "annual", "IEA coverage variable by dataset (restricted access)"

    return False, "", f"No geographic capability rule registered for {sid}"


# ---------------------------------------------------------------------------
# Decision dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CoverageDecision:
    source_id: str
    source_name: str
    status: str
    status_badge: str
    reason: str
    frequency: str = ""
    freq_rank: int = 0
    history_start: int = 0
    history_end: int = 0
    history_months: int = 0
    period_overlap_months: int = 0
    auth_required: bool = False
    auth_satisfied: bool = True
    mapping_required: bool = False
    dataset_type: str = "tabular"
    endpoint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class FeatureCoveragePlan:
    concept: str
    name: str
    role: str
    frequency: str
    unit: str
    country: str
    derived_from: str = ""
    decisions: list[CoverageDecision] = field(default_factory=list)
    best_source_id: str = ""
    best_source_name: str = ""
    best_status: str = UNKNOWN
    best_frequency: str = ""
    best_freq_rank: int = 0
    best_history_months: int = 0
    candidates_in_order: list[str] = field(default_factory=list)

    @property
    def is_supported(self) -> bool:
        return self.best_status == SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "name": self.name,
            "role": self.role,
            "frequency": self.frequency,
            "unit": self.unit,
            "country": self.country,
            "derived_from": self.derived_from,
            "best_source_id": self.best_source_id,
            "best_source_name": self.best_source_name,
            "best_status": self.best_status,
            "best_frequency": self.best_frequency,
            "best_freq_rank": self.best_freq_rank,
            "best_history_months": self.best_history_months,
            "candidates_in_order": self.candidates_in_order,
            "decisions": [d.to_dict() for d in self.decisions],
        }


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_source(
    source_id: str,
    feature: FeatureSpec,
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> CoverageDecision:
    country_iso3 = country_iso3.strip().upper()
    src = get_source(source_id)
    if src is None:
        return CoverageDecision(
            source_id=source_id, source_name=source_id, status=UNKNOWN,
            status_badge=DISCOVERY_BADGES[UNKNOWN],
            reason=f"{source_id} is not present in the source registry",
        )

    freq_hint = src.frequencies[0] if src.frequencies else ""
    decision = CoverageDecision(
        source_id=src.source_id,
        source_name=src.source_name,
        status=UNKNOWN,
        status_badge=DISCOVERY_BADGES[UNKNOWN],
        reason="",
        frequency=freq_hint,
        freq_rank=frequency_rank(freq_hint),
        history_start=src.historical_start,
        history_end=src.historical_end,
        history_months=_months_between(src.historical_start, src.historical_end),
        auth_required=src.auth_required,
        auth_satisfied=True,
        mapping_required=False,
        dataset_type="geospatial" if src.source_id == "era5" else "tabular",
        endpoint=src.endpoint,
    )

    # 1. Variable availability. Derived features (e.g. CDD/HDD) are checked
    #    against their parent concept (the variable a source actually provides).
    required_concept = feature.derived_from if feature.is_derived else feature.concept
    if required_concept not in src.features:
        decision.status = NOT_SUPPORTED
        decision.status_badge = DISCOVERY_BADGES[NOT_SUPPORTED]
        decision.reason = f"{src.source_name} does not publish '{required_concept}'"
        return decision

    # 1b. Minimum source-frequency requirement (e.g. CDD/HDD need DAILY data).
    if feature.min_source_frequency:
        offered = src.frequencies[0] if src.frequencies else ""
        if not frequency_satisfies(feature.min_source_frequency, offered):
            decision.status = NOT_SUPPORTED
            decision.status_badge = DISCOVERY_BADGES[NOT_SUPPORTED]
            decision.reason = (
                f"{src.source_name} offers {offered}, but '{feature.concept}' requires "
                f"{feature.min_source_frequency} or finer source data"
            )
            return decision

    # 2. Geographic capability
    covered, geo_freq, geo_reason = geographic_capability(src, country_iso3, feature.concept)
    if not covered:
        decision.status = NOT_SUPPORTED
        decision.status_badge = DISCOVERY_BADGES[NOT_SUPPORTED]
        decision.reason = geo_reason
        return decision
    if geo_freq:
        decision.frequency = geo_freq
        decision.freq_rank = frequency_rank(geo_freq)

    # 3. Mapping requirement (e.g. ENTSO-E European country without EIC code)
    if src.source_id == "entsoe" and _eic_code_for(country_iso3) is None:
        decision.mapping_required = True
        decision.status = MAPPING_REQUIRED
        decision.status_badge = DISCOVERY_BADGES[MAPPING_REQUIRED]
        decision.reason = geo_reason
        return decision

    # 4. Period overlap
    overlap_start = max(start_year, src.historical_start)
    overlap_end = min(end_year, src.historical_end)
    overlap_years = max(0, overlap_end - overlap_start + 1)
    decision.period_overlap_months = overlap_years * 12
    if overlap_years == 0:
        decision.status = TEMPORARILY_UNAVAILABLE
        decision.status_badge = DISCOVERY_BADGES[TEMPORARILY_UNAVAILABLE]
        decision.reason = (
            f"No historical overlap: source covers {src.historical_start}-{src.historical_end}, "
            f"requested {start_year}-{end_year}"
        )
        return decision

    # 5. Authentication
    decision.auth_satisfied = credential_is_set(src.source_id, credentials)
    decision.status = SUPPORTED
    decision.status_badge = DISCOVERY_BADGES[SUPPORTED]
    decision.reason = geo_reason
    if src.auth_required and not decision.auth_satisfied:
        decision.status = AUTH_REQUIRED
        decision.status_badge = DISCOVERY_BADGES[AUTH_REQUIRED]
        if src.auth_type == "restricted":
            decision.reason = "Data exists but access is restricted (subscription/licensing)"
        else:
            decision.reason = f"Data exists but credential required ({src.auth_env}); none provided"
    return decision


def resolve_feature(
    concept: str,
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> FeatureCoveragePlan:
    feature = FEATURE_REGISTRY.get(concept.strip().lower())
    country_iso3 = country_iso3.strip().upper()
    if feature is None:
        raise KeyError(f"Unknown feature concept: {concept}")

    plan = FeatureCoveragePlan(
        concept=feature.concept,
        name=feature.name,
        role=feature.role,
        frequency=feature.frequency,
        unit=feature.unit,
        country=country_iso3,
        derived_from=feature.derived_from,
    )

    # Derived features (e.g. CDD/HDD) evaluate their own source list with any
    # min_source_frequency gate applied (see evaluate_source), not a naive
    # inheritance from the parent feature.
    ordered = feature.ordered_sources(country_iso3)
    plan.candidates_in_order = list(ordered)

    for source_id in ordered:
        decision = evaluate_source(source_id, feature, country_iso3, start_year, end_year, credentials)
        plan.decisions.append(decision)

    supported = [d for d in plan.decisions if d.status == SUPPORTED]
    if supported:
        best = supported[0]
        plan.best_source_id = best.source_id
        plan.best_source_name = best.source_name
        plan.best_status = SUPPORTED
        plan.best_frequency = best.frequency
        plan.best_freq_rank = best.freq_rank
        plan.best_history_months = best.history_months
    elif plan.decisions:
        best = max(plan.decisions, key=lambda d: DISCOVERY_PRIORITY[d.status])
        plan.best_source_id = best.source_id
        plan.best_source_name = best.source_name
        plan.best_status = best.status
        plan.best_frequency = best.frequency
        plan.best_freq_rank = best.freq_rank
        plan.best_history_months = best.history_months
    return plan


def resolve_country(
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
) -> list[FeatureCoveragePlan]:
    return [resolve_feature(f.concept, country_iso3, start_year, end_year, credentials) for f in get_all_features()]


# ---------------------------------------------------------------------------
# Demand classification
# ---------------------------------------------------------------------------

def classify_demand(
    country_iso3: str,
    start_year: int,
    end_year: int,
    credentials: dict[str, str] | None = None,
    min_consecutive_months: int | None = None,
) -> dict[str, Any]:
    """Classify electricity-demand coverage for a country.

    MONTHLY_SUFFICIENT / MONTHLY_PARTIAL / ANNUAL_ONLY / UNAVAILABLE.
    Never treats annual-only data as monthly.

    The returned dict also carries evidence-based continuity metrics
    (first/last month, expected vs observed monthly observations, missing
    months, longest continuous run, gap count). In discovery mode these are
    derived from the source registry's historical range; after acquisition
    they can be re-computed from the actual downloaded series.
    """
    target = get_target_feature()
    plan = resolve_feature(target.concept, country_iso3, start_year, end_year, credentials)
    min_history = target.min_history_months or 120
    if min_consecutive_months is None:
        min_consecutive_months = min_history

    monthly_capable = [
        d for d in plan.decisions
        if d.status in (SUPPORTED, AUTH_REQUIRED) and d.freq_rank >= MIN_MONTHLY_RANK
    ]
    any_capable = [d for d in plan.decisions if d.status in (SUPPORTED, AUTH_REQUIRED)]

    def _evidence(best) -> dict[str, Any]:
        overlap_months = best.period_overlap_months
        first_month = f"{max(start_year, best.history_start)}-01"
        last_month = f"{min(end_year, best.history_end)}-12"
        return {
            "iso3": country_iso3,
            "best_monthly_source": best.source_name,
            "resolution": best.frequency,
            "first_month": first_month,
            "last_month": last_month,
            "expected_months": _months_between(start_year, end_year),
            "observed_months": overlap_months,   # registry-implied; verified at acquisition
            "missing_months": 0,                  # discovery assumes continuity
            "longest_continuous_run": overlap_months,
            "gap_count": 0,
            "months_available": overlap_months,
            "min_required_months": min_history,
            "min_consecutive_months": min_consecutive_months,
        }

    if monthly_capable:
        # plan.decisions is already in feature priority order -> first is preferred.
        best = monthly_capable[0]
        months = best.period_overlap_months
        evidence = _evidence(best)
        if months >= min_history and months >= min_consecutive_months:
            evidence["status"] = MONTHLY_SUFFICIENT
            evidence["badge"] = DEMAND_BADGES[MONTHLY_SUFFICIENT]
            return evidence
        evidence["status"] = MONTHLY_PARTIAL
        evidence["badge"] = DEMAND_BADGES[MONTHLY_PARTIAL]
        return evidence
    if any_capable:
        best = any_capable[0]
        # Annual-only: there is NO monthly series, so monthly continuity is zero.
        overlap_months = best.period_overlap_months
        return {
            "iso3": country_iso3,
            "status": ANNUAL_ONLY,
            "badge": DEMAND_BADGES[ANNUAL_ONLY],
            "best_monthly_source": "",
            "annual_source": best.source_name,
            "resolution": best.frequency or "annual",
            "first_month": "",
            "last_month": "",
            "expected_months": _months_between(start_year, end_year),
            "observed_months": 0,
            "missing_months": 0,
            "longest_continuous_run": 0,
            "gap_count": 0,
            "annual_observations_expected": overlap_months // 12,
            "months_available": 0,
            "min_required_months": min_history,
            "min_consecutive_months": min_consecutive_months,
        }
    return {
        "iso3": country_iso3,
        "status": UNAVAILABLE,
        "badge": DEMAND_BADGES[UNAVAILABLE],
        "best_monthly_source": "",
        "annual_source": "",
        "resolution": "",
        "first_month": "",
        "last_month": "",
        "expected_months": _months_between(start_year, end_year),
        "observed_months": 0,
        "missing_months": 0,
        "longest_continuous_run": 0,
        "gap_count": 0,
        "months_available": 0,
        "min_required_months": min_history,
        "min_consecutive_months": min_consecutive_months,
    }


__all__ = [
    "CoverageDecision", "FeatureCoveragePlan",
    "evaluate_source", "resolve_feature", "resolve_country", "classify_demand",
    "frequency_rank", "frequency_satisfies", "credential_is_set",
    "SUPPORTED", "NOT_SUPPORTED", "AUTH_REQUIRED", "MAPPING_REQUIRED",
    "TEMPORARILY_UNAVAILABLE", "UNKNOWN", "DISCOVERY_BADGES", "DISCOVERY_PRIORITY",
    "MONTHLY_SUFFICIENT", "MONTHLY_PARTIAL", "ANNUAL_ONLY", "UNAVAILABLE", "DEMAND_BADGES",
    "EMBER_MONTHLY_GEOGRAPHIES", "OWID_EV_COUNTRIES", "EU27_PLUS",
]
