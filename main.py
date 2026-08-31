"""HGT-QF Data Downloader — command-line entrypoint (redesigned).

Subcommands
-----------
    audit      Discovery-only: country × feature × source matrix + HGT-QF readiness.
    plan       Dry-run source resolution (no downloads): selected source + fallbacks.
    auth-check Tiny per-source credential/endpoint auth test (never prints keys).
    matrix     Show the source capability matrix (mode / role / coverage / auth).
    acquire    Coverage-gated acquisition (endpoint verification + fallback).
    run        audit -> acquire for target-ready countries.
    countries  List registered countries.
    sources    List registered sources.
    rebuild-country-registry   Regenerate config/country_registry.csv.

Recommended flow (spec): run `audit` first, review the matrix, then `run`/`acquire`.

Credentials are loaded from .env (gitignored) and never printed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from country_utils import REGIONAL_PRESETS, normalize_country

CREDENTIAL_ENVS = ("EIA_API_KEY", "ENTSOE_API_TOKEN", "EMBER_API_KEY", "CDS_API_KEY", "CDSAPI_KEY")


def _parse_countries(raw: list[str] | None) -> list[str]:
    codes: list[str] = []
    if not raw:
        return codes
    for item in raw:
        item = item.strip()
        if not item:
            continue
        if item in REGIONAL_PRESETS:
            for c in REGIONAL_PRESETS[item]:
                if c not in codes:
                    codes.append(c)
            continue
        for part in item.replace(",", " ").split():
            iso3 = normalize_country(part)
            if iso3 and iso3 not in codes:
                codes.append(iso3)
            elif not iso3:
                print(f"  ! unrecognized country: {part!r} (skipped)", file=sys.stderr)
    return codes


def _credentials() -> dict[str, str]:
    creds: dict[str, str] = {}
    for env in CREDENTIAL_ENVS:
        val = os.getenv(env)
        if val:
            creds[env] = val
    return creds


def _resolve_features(raw: list[str] | None) -> tuple[list[str], list[str]]:
    """Resolve feature aliases/typos to canonical concepts; report unknowns."""
    from feature_registry import FeatureNotFoundError, format_feature_not_found, resolve_feature_concept
    if not raw:
        return [], []
    resolved: list[str] = []
    errors: list[str] = []
    for f in raw:
        try:
            canon = resolve_feature_concept(f)
            if canon not in resolved:
                resolved.append(canon)
        except FeatureNotFoundError:
            errors.append(format_feature_not_found(f))
    return resolved, errors


_SUMMARY_BUCKETS: dict[str, list[str]] = {
    "SUCCESS": ["SUCCESS", "PARTIAL_SUCCESS"],
    "NO_DATA": ["NO_DATA", "NO_DATA_FOR_COUNTRY_INDICATOR", "NO_RECORDS",
                "EMPTY_RESPONSE", "SOURCE_DATA_EMPTY"],
    "FAILED": ["NETWORK_ERROR", "NOT_VERIFIED", "DOWNLOAD_ERROR", "VERIFY_FAILED",
               "SCHEMA_MISMATCH", "PARSE_ERROR", "INVALID_RESPONSE",
               "DEPENDENCY_MISSING", "UNEXPECTED_ERROR", "FAILED", "RATE_LIMITED",
               "TIMEOUT", "SOURCE_FORMAT_ERROR", "SOURCE_API_ERROR",
               "SOURCE_TEMPORARY_FAILURE", "RETRY_EXHAUSTED", "INVALID_REQUEST",
               "CONFIGURATION_ERROR"],
    "SKIPPED": ["SKIPPED", "MAPPING_REQUIRED", "BULK_MANUAL",
                "TEMPORARILY_UNAVAILABLE", "SOURCE_TEMPORARILY_UNAVAILABLE",
                "ENDPOINT_OR_INDICATOR_NOT_FOUND"],
    "UNSUPPORTED": ["NOT_SUPPORTED", "UNKNOWN_FEATURE", "UNKNOWN",
                    "SOURCE_NOT_COVERED"],
    "AUTH_REQUIRED": ["AUTH_REQUIRED", "AUTH_FAILED", "SOURCE_AUTH_REQUIRED"],
}


def _print_acquisition_summary(results: list[Any]) -> None:
    from collections import Counter
    granular = Counter(r.status for r in results)
    buckets = Counter()
    for status, n in granular.items():
        bucket = next((b for b, codes in _SUMMARY_BUCKETS.items() if status in codes), "FAILED")
        buckets[bucket] += n
    print("\nAcquisition summary:")
    for bucket in ("SUCCESS", "NO_DATA", "FAILED", "SKIPPED", "UNSUPPORTED", "AUTH_REQUIRED"):
        print(f"  {bucket:<12} {buckets.get(bucket, 0)}")
    print("  (detail)      " + "  ".join(
        f"{s}={n}" for s, n in sorted(granular.items(), key=lambda kv: -kv[1])))


def _report_acquisition_error(feature_errors: list[str]) -> None:
    for msg in feature_errors:
        print("\n" + msg, file=sys.stderr)
    print("\nNo acquisition performed (feature resolution failed).", file=sys.stderr)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--countries", nargs="*", default=None,
                        help="Country names/ISO-3 codes or presets (e.g. G7). Default: all registered countries.")
    parser.add_argument("--start", type=int, default=2000, help="Start year (default 2000)")
    parser.add_argument("--end", type=int, default=2024, help="End year (default 2024)")
    parser.add_argument("--output", default="hgt_qf_data", help="Output root directory")


def _add_research_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--min-demand-history", type=int, default=None,
                        help="Minimum consecutive monthly demand months for RESEARCH_READY "
                             "(default: research_config.json, 120)")
    parser.add_argument("--min-core-coverage", type=float, default=None,
                        help="Minimum core feature coverage ratio 0-1 (default: research_config.json, 0.8)")
    parser.add_argument("--require-optional", action="store_true",
                        help="Require ALL optional features for RESEARCH_READY (default: off)")


def _research_config_from_args(args: argparse.Namespace):
    from research_config import build_research_config
    return build_research_config(
        min_history_months=args.min_demand_history,
        min_consecutive_months=args.min_demand_history,
        min_core_coverage=args.min_core_coverage,
        require_optional_features=True if args.require_optional else None,
    )


def cmd_audit(args: argparse.Namespace) -> int:
    from country_registry import get_all_countries
    from availability_audit import render_audit_report, run_availability_audit

    countries = _parse_countries(args.countries) or [r.iso3 for r in get_all_countries()]
    audit = run_availability_audit(
        countries=countries, start_year=args.start, end_year=args.end,
        credentials=_credentials(), output_dir=args.output, max_per_region=args.max_per_region,
        config=_research_config_from_args(args),
    )
    print(render_audit_report(audit))
    if args.output:
        print(f"\nReports written to {args.output}/metadata/")
    return 0


def cmd_acquire(args: argparse.Namespace) -> int:
    from acquisition_engine import run_acquisition
    from acquisition_report import generate_acquisition_report

    countries = _parse_countries(args.countries)
    if not countries:
        print("No valid countries supplied.", file=sys.stderr)
        return 2

    resolved, feature_errors = _resolve_features(args.features)
    if feature_errors:
        _report_acquisition_error(feature_errors)
        return 2

    results = run_acquisition(
        countries=countries, start=args.start, end=args.end, out_dir=args.output,
        credentials=_credentials(), concepts=resolved or None,
        progress=lambda msg: print(f"  … {msg}", flush=True),
    )
    generate_acquisition_report(args.output, results)

    ok = sum(1 for r in results if r.status in ("SUCCESS", "PARTIAL_SUCCESS"))
    print(f"\nAcquisition complete: {ok}/{len(results)} country-features acquired.")
    _print_acquisition_summary(results)
    print(f"Report B: {args.output}/metadata/report_B_acquisition.csv")
    return 0


def cmd_auth_check(args: argparse.Namespace) -> int:
    from auth_check import render_auth_check, run_auth_checks
    results = run_auth_checks(_credentials())
    print(render_auth_check(results))
    return 0


def cmd_matrix(args: argparse.Namespace) -> int:
    import pandas as pd
    from source_registry import get_source_capability_matrix
    df = pd.DataFrame(get_source_capability_matrix())
    cols = ["source", "features", "country_coverage", "temporal_resolution",
            "authentication", "acquisition_mode", "historical_coverage",
            "role", "rate_limit"]
    print("\nSOURCE CAPABILITY MATRIX")
    print("=" * 100)
    print(df[cols].to_string(index=False))
    print("\nAcquisition modes:")
    from source_registry import ACQUISITION_MODES
    for mode, label in ACQUISITION_MODES.items():
        print(f"  {mode:<20} {label}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from acquisition_plan import build_acquisition_plan, render_acquisition_plan

    countries = _parse_countries(args.countries)
    if not countries:
        print("No valid countries supplied.", file=sys.stderr)
        return 2

    resolved, feature_errors = _resolve_features(args.features)
    if feature_errors:
        _report_acquisition_error(feature_errors)
        return 2

    plan = build_acquisition_plan(
        countries=countries, features=resolved, start_year=args.start,
        end_year=args.end, credentials=_credentials(),
    )
    print(render_acquisition_plan(plan))
    if args.output:
        meta = Path(args.output) / "metadata"
        meta.mkdir(parents=True, exist_ok=True)
        plan.to_csv(meta / "acquisition_plan.csv", index=False)
        print(f"\nPlan written to {args.output}/metadata/acquisition_plan.csv")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from country_registry import get_all_countries
    from availability_audit import build_report_c, render_audit_report, run_availability_audit
    from acquisition_engine import run_acquisition
    from acquisition_report import generate_acquisition_report

    cfg = _research_config_from_args(args)
    countries = _parse_countries(args.countries) or [r.iso3 for r in get_all_countries()]
    audit = run_availability_audit(
        countries=countries, start_year=args.start, end_year=args.end,
        credentials=_credentials(), output_dir=args.output, max_per_region=args.max_per_region,
        config=cfg,
    )
    print(render_audit_report(audit))

    report_c = build_report_c(countries, args.start, args.end, _credentials(), cfg)

    # Acquisition eligibility is driven by TARGET readiness (monthly demand),
    # NOT by full RESEARCH_READY. Optional/extended gaps never block useful
    # data. `--research-ready` tightens to the full research rule.
    eligible_df = report_c[report_c["target_status"].isin(("MONTHLY_SUFFICIENT", "MONTHLY_PARTIAL"))]
    if getattr(args, "research_ready", False):
        eligible_df = eligible_df[eligible_df["research_ready"] == "RESEARCH_READY"]
    eligible = eligible_df["iso3"].tolist()

    if args.limit:
        eligible = eligible[: args.limit]

    if not eligible:
        print("\nNo TARGET_READY countries found; skipping acquisition.")
        return 0

    print(f"\nAcquiring {len(eligible)} target-ready countries: {', '.join(eligible)}\n")
    results = run_acquisition(
        countries=eligible, start=args.start, end=args.end, out_dir=args.output,
        credentials=_credentials(), progress=lambda m: print(f"  … {m}", flush=True),
    )
    generate_acquisition_report(args.output, results)
    ok = sum(1 for r in results if r.status in ("SUCCESS", "PARTIAL_SUCCESS"))
    print(f"\nAcquisition complete: {ok}/{len(results)} country-features acquired.")
    _print_acquisition_summary(results)
    return 0


def cmd_countries(args: argparse.Namespace) -> int:
    from country_registry import get_all_countries
    for r in get_all_countries():
        print(f"{r.iso3}\t{r.country_name}\t{r.region}\t{r.bbox_source}")
    print(f"\n{len(get_all_countries())} countries registered.")
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    from source_registry import get_all_registered_sources
    for s in get_all_registered_sources():
        auth = "auth:" + s.auth_type if s.auth_required else "open"
        print(f"{s.source_id:<12} {s.source_name:<20} features={','.join(s.features)[:60]:<60} "
              f"{';'.join(s.frequencies):<20} {auth}")
    return 0


def cmd_rebuild_country_registry(args: argparse.Namespace) -> int:
    from country_registry import regenerate_country_registry
    out = regenerate_country_registry()
    print(f"Wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="Discovery-only coverage + readiness audit")
    _add_common_args(p_audit)
    _add_research_args(p_audit)
    p_audit.add_argument("--max-per-region", type=int, default=6, help="Diverse-selection cap per region")
    p_audit.set_defaults(func=cmd_audit)

    p_acquire = sub.add_parser("acquire", help="Coverage-gated acquisition")
    _add_common_args(p_acquire)
    p_acquire.add_argument("--features", nargs="*", default=None,
                           help="Optional feature concepts/aliases (e.g. electricity_demand temperature_2m wind)")
    p_acquire.set_defaults(func=cmd_acquire)

    p_plan = sub.add_parser("plan", help="Dry-run source resolution (no downloads)")
    _add_common_args(p_plan)
    p_plan.add_argument("--features", nargs="*", default=None,
                        help="Feature concepts/aliases (e.g. electricity_demand temperature_2m wind)")
    p_plan.set_defaults(func=cmd_plan)

    p_auth = sub.add_parser("auth-check", help="Tiny per-source credential/endpoint auth test")
    p_auth.set_defaults(func=cmd_auth_check)

    p_matrix = sub.add_parser("matrix", help="Show the source capability matrix")
    p_matrix.set_defaults(func=cmd_matrix)

    p_run = sub.add_parser("run", help="audit -> acquire for target-ready countries")
    _add_common_args(p_run)
    _add_research_args(p_run)
    p_run.add_argument("--max-per-region", type=int, default=6)
    p_run.add_argument("--limit", type=int, default=None, help="Cap the number of countries acquired")
    p_run.add_argument("--research-ready", action="store_true",
                       help="Restrict acquisition to RESEARCH_READY countries (default: TARGET_READY)")
    p_run.set_defaults(func=cmd_run)

    sub.add_parser("countries", help="List registered countries").set_defaults(func=cmd_countries)
    sub.add_parser("sources", help="List registered sources").set_defaults(func=cmd_sources)
    sub.add_parser("rebuild-country-registry", help="Regenerate config/country_registry.csv").set_defaults(
        func=cmd_rebuild_country_registry
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
