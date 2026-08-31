"""HGT-QF Data Downloader — command-line entrypoint.

Subcommands
-----------
    audit     Run the global data availability audit (no downloads).
    acquire   Run coverage-gated acquisition for the selected countries.
    run       audit + acquire in sequence (the recommended order).
    countries List the registered country ISO-3 codes.
    rebuild-country-registry   Regenerate config/country_registry.csv.

Examples
--------
    python main.py audit --start 2000 --end 2024
    python main.py acquire --countries EGY DEU FRA GBR USA JPN --start 2000 --end 2024
    python main.py run --countries "G7" --start 2000 --end 2024
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from country_utils import REGIONAL_PRESETS, get_country_name, normalize_country


def _parse_countries(raw: list[str]) -> list[str]:
    """Expand presets and normalize every entry to ISO-3."""
    codes: list[str] = []
    for item in raw:
        item = item.strip()
        if not item:
            continue
        if item in REGIONAL_PRESETS:
            for preset_code in REGIONAL_PRESETS[item]:
                if preset_code not in codes:
                    codes.append(preset_code)
            continue
        # Accept comma-separated inline lists.
        for part in item.replace(",", " ").split():
            iso3 = normalize_country(part)
            if iso3 and iso3 not in codes:
                codes.append(iso3)
            elif not iso3:
                print(f"  ! unrecognized country: {part!r} (skipped)", file=sys.stderr)
    return codes


def _credentials(args: argparse.Namespace) -> dict[str, str]:
    creds: dict[str, str] = {}
    for env_var in ("EMBER_API_KEY", "ENTSOE_API_TOKEN", "ENTSOE_API_KEY",
                    "EIA_API_KEY", "CDS_API_KEY", "CDSAPI_KEY"):
        if os.getenv(env_var):
            creds[env_var] = os.getenv(env_var, "")
    return creds


def _add_country_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--countries", nargs="*", default=None,
        help="Country names/ISO-3 codes or preset keys (e.g. G7, EU-27 (Sample)). "
             "Defaults to the full registered country set.",
    )
    parser.add_argument("--start", type=int, default=2000, help="Start year (default 2000)")
    parser.add_argument("--end", type=int, default=2024, help="End year (default 2024)")
    parser.add_argument(
        "--output", default="hgt_qf_data",
        help="Output root directory (default ./hgt_qf_data)",
    )


def cmd_audit(args: argparse.Namespace) -> int:
    from country_registry import get_all_countries
    from availability_audit import render_audit_report, run_availability_audit

    countries = _parse_countries(args.countries) if args.countries else [r.iso3 for r in get_all_countries()]
    audit = run_availability_audit(
        countries=countries, start_year=args.start, end_year=args.end,
        credentials=_credentials(args), output_dir=args.output, top_n=args.top,
    )
    print(render_audit_report(audit))
    return 0


def cmd_acquire(args: argparse.Namespace) -> int:
    from acquisition_engine import run_acquisition
    from acquisition_report import generate_acquisition_report

    countries = _parse_countries(args.countries)
    if not countries:
        print("No valid countries supplied.", file=sys.stderr)
        return 2

    results = run_acquisition(
        countries=countries, start=args.start, end=args.end, output_dir=args.output,
        credentials=_credentials(args), feature_ids=args.features,
        progress=lambda msg: print(f"  … {msg}", flush=True),
    )

    generate_acquisition_report(args.output, results)

    ok = sum(1 for r in results if r.status in ("SUCCESS", "PARTIAL_SUCCESS"))
    print(f"\nAcquisition complete: {ok}/{len(results)} variables acquired "
          f"({len(countries)} countries).")
    print(f"Report: {args.output}/metadata/acquisition_report.csv")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    rc = cmd_audit(args)
    if rc != 0:
        return rc
    print("\nProceeding to coverage-gated acquisition…\n")
    return cmd_acquire(args)


def cmd_countries(args: argparse.Namespace) -> int:
    from country_registry import get_all_countries
    records = get_all_countries()
    for r in records:
        print(f"{r.iso3}\t{r.country_name}\t{r.region}\t{r.bbox_source}")
    print(f"\n{len(records)} countries registered.")
    return 0


def cmd_rebuild_country_registry(args: argparse.Namespace) -> int:
    from country_registry import regenerate_country_registry
    out = regenerate_country_registry()
    print(f"Wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="Global availability audit (no downloads)")
    _add_country_args(p_audit)
    p_audit.add_argument("--top", type=int, default=20, help="Recommended-country list length")
    p_audit.set_defaults(func=cmd_audit)

    p_acquire = sub.add_parser("acquire", help="Coverage-gated acquisition")
    _add_country_args(p_acquire)
    p_acquire.add_argument(
        "--features", nargs="*", default=None,
        help="Optional subset of feature IDs (e.g. VAR_01 VAR_02)",
    )
    p_acquire.set_defaults(func=cmd_acquire)

    p_run = sub.add_parser("run", help="audit + acquire")
    _add_country_args(p_run)
    p_run.add_argument("--top", type=int, default=20)
    p_run.add_argument("--features", nargs="*", default=None)
    p_run.set_defaults(func=cmd_run)

    sub.add_parser("countries", help="List registered countries").set_defaults(func=cmd_countries)
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
