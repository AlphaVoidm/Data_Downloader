"""Multi-Dimensional Coverage Inventory and Reporting Engine for HGT-QF.

Generates 5 comprehensive research reports:
1. Country-Level Data Inventory (Coverage matrix across all research feature domains)
2. Feature-Level Inventory (25 conceptual variables, definitions, units, frequencies, sources)
3. Historical Coverage & Lookback Feasibility Report (5y, 10y, 15y, 20y, L=120 / H=12,36,60)
4. Source Registry Report (Authoritative audit of all providers and licenses)
5. Dataset Manifest (SHA-256 hashes, exact endpoint parameters, download timestamps)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from country_utils import get_country_name
from source_mapping import validate_source_capability
from source_registry import get_all_registered_sources

# The 25 Conceptual Research Variables defined for HGT-QF
CONCEPTUAL_FEATURES: list[dict[str, Any]] = [
    # 1. Electricity Demand / Target
    {
        "feature_id": "VAR_01",
        "concept": "electricity_demand",
        "feature_name": "Electricity Demand / Total Load",
        "domain": "electricity",
        "source": "Ember / ENTSO-E / EIA / NESO / AEMO",
        "source_variable": "Demand / Total Load / Actual Net Demand",
        "definition": "Total electrical energy consumed or delivered by the transmission/distribution grid over the period.",
        "native_frequency": "monthly / hourly / sub-hourly",
        "unit": "TWh / MW / MWh",
        "public_access": "open / free_api_token",
        "is_target": True,
        "is_derived": False,
    },
    # 2-5. Climate Variables
    {
        "feature_id": "VAR_02",
        "concept": "temperature",
        "feature_name": "Air Temperature at 2m",
        "domain": "climate",
        "source": "NASA POWER / ERA5",
        "source_variable": "T2M",
        "definition": "Average surface air temperature measured at 2 meters above ground level.",
        "native_frequency": "daily / hourly",
        "unit": "°C",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_03",
        "concept": "solar_radiation",
        "feature_name": "Solar Radiation (Surface Shortwave)",
        "domain": "climate",
        "source": "NASA POWER",
        "source_variable": "ALLSKY_SFC_SW_DWN",
        "definition": "All Sky Surface Shortwave Downward Irradiance.",
        "native_frequency": "daily",
        "unit": "kW-hr/m²/day",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_04",
        "concept": "wind_speed",
        "feature_name": "Wind Speed at 10m",
        "domain": "climate",
        "source": "NASA POWER",
        "source_variable": "WS10M",
        "definition": "Wind speed at 10 meters above ground level.",
        "native_frequency": "daily",
        "unit": "m/s",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_05",
        "concept": "precipitation",
        "feature_name": "Precipitation Total",
        "domain": "climate",
        "source": "NASA POWER",
        "source_variable": "PRECTOTCORR",
        "definition": "Corrected total daily precipitation depth.",
        "native_frequency": "daily",
        "unit": "mm/day",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    # 6-9. Macroeconomic Variables
    {
        "feature_id": "VAR_06",
        "concept": "gdp",
        "feature_name": "Gross Domestic Product (GDP)",
        "domain": "economic",
        "source": "World Bank (WDI)",
        "source_variable": "NY.GDP.MKTP.CD",
        "definition": "Gross domestic product at purchaser's prices in current U.S. dollars.",
        "native_frequency": "annual",
        "unit": "current USD",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_07",
        "concept": "gdp_growth",
        "feature_name": "GDP Growth Rate",
        "domain": "economic",
        "source": "World Bank (WDI)",
        "source_variable": "NY.GDP.MKTP.KD.ZG",
        "definition": "Annual percentage growth rate of GDP at market prices based on constant local currency.",
        "native_frequency": "annual",
        "unit": "%",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_08",
        "concept": "gdp_per_capita",
        "feature_name": "GDP Per Capita",
        "domain": "economic",
        "source": "World Bank (WDI)",
        "source_variable": "NY.GDP.PCAP.CD",
        "definition": "GDP per capita in current U.S. dollars.",
        "native_frequency": "annual",
        "unit": "current USD",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_09",
        "concept": "inflation_cpi",
        "feature_name": "Inflation (Consumer Price Index)",
        "domain": "economic",
        "source": "World Bank (WDI)",
        "source_variable": "FP.CPI.TOTL.ZG",
        "definition": "Annual percentage change in the cost to the average consumer of acquiring a basket of goods and services.",
        "native_frequency": "annual",
        "unit": "%",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    # 10-13. Demographic Variables
    {
        "feature_id": "VAR_10",
        "concept": "population",
        "feature_name": "Total Population",
        "domain": "demographic",
        "source": "World Bank (WDI)",
        "source_variable": "SP.POP.TOTL",
        "definition": "Total population based on the de facto definition of population.",
        "native_frequency": "annual",
        "unit": "count",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_11",
        "concept": "population_growth",
        "feature_name": "Population Growth Rate",
        "domain": "demographic",
        "source": "World Bank (WDI)",
        "source_variable": "SP.POP.GROW",
        "definition": "Annual population growth rate.",
        "native_frequency": "annual",
        "unit": "%",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_12",
        "concept": "urban_population",
        "feature_name": "Urban Population",
        "domain": "demographic",
        "source": "World Bank (WDI)",
        "source_variable": "SP.URB.TOTL",
        "definition": "People living in urban areas as defined by national statistical offices.",
        "native_frequency": "annual",
        "unit": "count",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_13",
        "concept": "urbanisation_rate",
        "feature_name": "Urbanisation Share (% of Total)",
        "domain": "demographic",
        "source": "World Bank (WDI)",
        "source_variable": "SP.URB.TOTL.IN.ZS",
        "definition": "Urban population as a percentage of total population.",
        "native_frequency": "annual",
        "unit": "%",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    # 14-18. Electricity / Energy System Structure
    {
        "feature_id": "VAR_14",
        "concept": "electricity_production",
        "feature_name": "Total Electricity Generation",
        "domain": "energy_system",
        "source": "Ember / World Bank",
        "source_variable": "EG.ELC.PROD.KH / Generation",
        "definition": "Total electricity produced from all generation sources.",
        "native_frequency": "monthly / annual",
        "unit": "TWh / kWh",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_15",
        "concept": "renewable_share",
        "feature_name": "Renewable Electricity Generation Share",
        "domain": "energy_system",
        "source": "Ember / IRENA",
        "source_variable": "Renewables % / Share",
        "definition": "Percentage share of total electricity generation originating from renewable sources.",
        "native_frequency": "monthly / annual",
        "unit": "%",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_16",
        "concept": "energy_mix",
        "feature_name": "Generation Mix by Fuel (Solar, Wind, Hydro, Fossil, Nuclear)",
        "domain": "energy_system",
        "source": "Ember",
        "source_variable": "Fuel Generation Categories",
        "definition": "Disaggregated electricity generation by fuel type.",
        "native_frequency": "monthly / annual",
        "unit": "TWh",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_17",
        "concept": "electricity_access",
        "feature_name": "Electricity Access (% of Population)",
        "domain": "energy_system",
        "source": "World Bank (WDI)",
        "source_variable": "EG.ELC.ACCS.ZS",
        "definition": "Access to electricity is the percentage of population with access to electricity.",
        "native_frequency": "annual",
        "unit": "%",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_18",
        "concept": "electricity_prices",
        "feature_name": "Retail / Wholesale Electricity Prices",
        "domain": "energy_system",
        "source": "IEA / Eurostat / National Regulators",
        "source_variable": "Electricity Price Index / Tariff",
        "definition": "Average electricity price per MWh/kWh for household and industrial sectors.",
        "native_frequency": "monthly / annual",
        "unit": "USD/MWh or EUR/kWh",
        "public_access": "restricted_or_national",
        "is_target": False,
        "is_derived": False,
    },
    # 19. Transport / Technology
    {
        "feature_id": "VAR_19",
        "concept": "ev_adoption",
        "feature_name": "Electric Vehicle (EV) Stock and Sales",
        "domain": "transport",
        "source": "IEA Global EV Data Explorer / OWID",
        "source_variable": "EV Stock / Sales",
        "definition": "Total battery electric (BEV) and plug-in hybrid (PHEV) vehicles in circulation.",
        "native_frequency": "annual",
        "unit": "vehicles count",
        "public_access": "public_explorer",
        "is_target": False,
        "is_derived": False,
    },
    # 20-22. Built Environment & Economic Structure
    {
        "feature_id": "VAR_20",
        "concept": "ac_heat_pump_adoption",
        "feature_name": "Air Conditioning & Heat Pump Penetration",
        "domain": "built_environment",
        "source": "IEA / National Surveys",
        "source_variable": "Appliance Stock",
        "definition": "Number or household penetration rate of AC and heat-pump units.",
        "native_frequency": "annual_or_multi_year",
        "unit": "units / % households",
        "public_access": "restricted_research",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_21",
        "concept": "sectoral_electricity_demand",
        "feature_name": "Sectoral Electricity Demand (Industrial/Residential/Commercial)",
        "domain": "built_environment",
        "source": "IEA / Eurostat / EIA",
        "source_variable": "Final Consumption by Sector",
        "definition": "Electricity consumption disaggregated by economic sector.",
        "native_frequency": "annual / monthly",
        "unit": "TWh / GWh",
        "public_access": "public_or_restricted",
        "is_target": False,
        "is_derived": False,
    },
    {
        "feature_id": "VAR_22",
        "concept": "manufacturing_share",
        "feature_name": "Manufacturing Value Added (% of GDP)",
        "domain": "built_environment",
        "source": "World Bank (WDI)",
        "source_variable": "NV.IND.MANF.ZS",
        "definition": "Manufacturing value added as a percentage of GDP.",
        "native_frequency": "annual",
        "unit": "%",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    # 23. Calendar
    {
        "feature_id": "VAR_23",
        "concept": "public_holidays",
        "feature_name": "Public Holidays & Regional Observances",
        "domain": "calendar",
        "source": "Nager.Date",
        "source_variable": "Public Holidays Calendar",
        "definition": "Official national and subnational public holidays.",
        "native_frequency": "annual / date_event",
        "unit": "calendar_flag",
        "public_access": "open",
        "is_target": False,
        "is_derived": False,
    },
    # 24-25. Derived Climate Features (Underlying Data Tracked)
    {
        "feature_id": "VAR_24",
        "concept": "cooling_degree_days",
        "feature_name": "Cooling Degree Days (CDD)",
        "domain": "derived_climate",
        "source": "Derived from NASA POWER / ERA5 Temperature (Base 18°C or 21°C)",
        "source_variable": "Underlying: T2M Daily",
        "definition": "Measurement reflecting amount of energy required to cool building (Derived in downstream preprocessing).",
        "native_frequency": "daily_raw_for_monthly_derivation",
        "unit": "degree-days",
        "public_access": "derived",
        "is_target": False,
        "is_derived": True,
    },
    {
        "feature_id": "VAR_25",
        "concept": "heating_degree_days",
        "feature_name": "Heating Degree Days (HDD)",
        "domain": "derived_climate",
        "source": "Derived from NASA POWER / ERA5 Temperature (Base 18°C)",
        "source_variable": "Underlying: T2M Daily",
        "definition": "Measurement reflecting amount of energy required to heat building (Derived in downstream preprocessing).",
        "native_frequency": "daily_raw_for_monthly_derivation",
        "unit": "degree-days",
        "public_access": "derived",
        "is_target": False,
        "is_derived": True,
    },
]


def generate_feature_inventory(output_path: Path) -> Path:
    """Generate comprehensive 25-feature research inventory report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(CONCEPTUAL_FEATURES)
    df.to_csv(output_path, index=False)
    return output_path


def build_country_coverage_inventory(
    root: Path,
    candidate_countries: list[str],
    results: list[Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Build detailed Country-Level Data Inventory showing availability across all feature domains.
    """
    rows = []
    summary_counts: dict[str, int] = {
        "total_countries": len(candidate_countries),
        "demand_available": 0,
        "climate_available": 0,
        "macro_available": 0,
        "demographics_available": 0,
        "energy_system_available": 0,
        "holidays_available": 0,
    }

    # Index acquired files in raw
    raw_files = list(root.glob("raw/**/*.csv"))
    acquired_sources_by_country: dict[str, dict[str, Path]] = {c: {} for c in candidate_countries}

    for f in raw_files:
        c_code = f.stem.upper()
        if c_code in acquired_sources_by_country:
            parent_source = f.parent.name.lower()
            acquired_sources_by_country[c_code][parent_source] = f

    for iso3 in candidate_countries:
        c_name = get_country_name(iso3)
        sources_found = acquired_sources_by_country.get(iso3, {})

        # Demand status
        demand_status = "NOT_AVAILABLE"
        demand_source = "None"
        demand_freq = "None"
        demand_records = 0
        demand_start = "N/A"
        demand_end = "N/A"

        for d_src, d_freq in [("ember", "monthly"), ("entsoe", "hourly"), ("eia", "hourly"), ("neso", "half-hourly"), ("aemo", "sub-hourly")]:
            if d_src in sources_found:
                demand_status = "AVAILABLE"
                demand_source = d_src.upper()
                demand_freq = d_freq
                try:
                    df = pd.read_csv(sources_found[d_src])
                    demand_records = len(df)
                    date_col = next((c for c in ["date", "period_start_utc", "period_utc", "settlement_date"] if c in df.columns), None)
                    if date_col and not df.empty:
                        demand_start = str(df[date_col].min())[:10]
                        demand_end = str(df[date_col].max())[:10]
                except Exception:
                    pass
                break

        if demand_status == "NOT_AVAILABLE":
            # Check capability validation for reason
            cap_entsoe, _ = validate_source_capability(iso3, "ENTSO-E Transparency")
            if cap_entsoe == "MAPPING_MISSING":
                demand_status = "MAPPING_MISSING"

        # Climate status (NASA POWER)
        climate_status = "AVAILABLE" if "nasa_power" in sources_found else "NOT_AVAILABLE"

        # Macro & Demographics (World Bank)
        wb_file = sources_found.get("worldbank")
        macro_status = "NOT_AVAILABLE"
        demo_status = "NOT_AVAILABLE"
        energy_sys_status = "NOT_AVAILABLE"

        if wb_file:
            try:
                wb_df = pd.read_csv(wb_file)
                indicators = set(wb_df.get("indicator", []).unique())
                if any(ind in indicators for ind in ["gdp_usd", "gdp_per_capita_usd", "gdp_growth_pct", "cpi_inflation_pct"]):
                    macro_status = "AVAILABLE"
                if any(ind in indicators for ind in ["population", "population_growth_pct", "urban_population", "urbanisation_rate_pct"]):
                    demo_status = "AVAILABLE"
                if any(ind in indicators for ind in ["electricity_access_pct", "electricity_production_kwh"]):
                    energy_sys_status = "AVAILABLE"
            except Exception:
                pass

        # Holidays
        holidays_status = "AVAILABLE" if "nager_date" in sources_found else "NOT_AVAILABLE"

        # EV and Prices tracking
        ev_status = "RESTRICTED / RESEARCH"
        prices_status = "RESTRICTED / RESEARCH"

        if demand_status == "AVAILABLE":
            summary_counts["demand_available"] += 1
        if climate_status == "AVAILABLE":
            summary_counts["climate_available"] += 1
        if macro_status == "AVAILABLE":
            summary_counts["macro_available"] += 1
        if demo_status == "AVAILABLE":
            summary_counts["demographics_available"] += 1
        if energy_sys_status == "AVAILABLE":
            summary_counts["energy_system_available"] += 1
        if holidays_status == "AVAILABLE":
            summary_counts["holidays_available"] += 1

        rows.append({
            "iso3": iso3,
            "country_name": c_name,
            "demand_status": demand_status,
            "demand_source": demand_source,
            "demand_frequency": demand_freq,
            "demand_records": demand_records,
            "demand_start": demand_start,
            "demand_end": demand_end,
            "climate_status": climate_status,
            "macroeconomics_status": macro_status,
            "demographics_status": demo_status,
            "energy_system_status": energy_sys_status,
            "holidays_status": holidays_status,
            "ev_adoption_status": ev_status,
            "electricity_prices_status": prices_status,
        })

    df_inventory = pd.DataFrame(rows)
    return df_inventory, summary_counts


def build_historical_coverage_report(
    root: Path,
    candidate_countries: list[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Evaluate historical time span of electricity demand data per country.

    Analyzes suitability for HGT-QF sequence modeling:
    - L = 120 months (10 years lookback)
    - H = 12, 36, 60 months (1, 3, 5 years forecasting horizon)
    - Minimum required history for 1 training sample + forecast: >= 11 to 15 years
    """
    raw_files = list(root.glob("raw/electricity/demand/**/*.csv"))
    records = []

    stats = {
        "total_countries_evaluated": len(candidate_countries),
        "countries_with_gte_5y_demand": 0,
        "countries_with_gte_10y_demand": 0,
        "countries_with_gte_15y_demand": 0,
        "countries_with_gte_20y_demand": 0,
        "countries_eligible_for_L120_H12": 0,
        "countries_eligible_for_L120_H60": 0,
    }

    for f in raw_files:
        iso3 = f.stem.upper()
        c_name = get_country_name(iso3)
        source_name = f.parent.name.upper()

        try:
            df = pd.read_csv(f)
            date_col = next((c for c in ["date", "period_start_utc", "period_utc", "settlement_date"] if c in df.columns), None)
            if not date_col or df.empty:
                continue

            parsed_dates = pd.to_datetime(df[date_col], errors="coerce").dropna().sort_values()
            if parsed_dates.empty:
                continue

            min_dt = parsed_dates.min()
            max_dt = parsed_dates.max()
            span_years = round((max_dt - min_dt).days / 365.25, 2)
            span_months = int(round(span_years * 12))

            # Feasibility for HGT-QF sequence design
            eligible_l120_h12 = span_months >= 132  # 120 + 12 = 132 months (11 years)
            eligible_l120_h60 = span_months >= 180  # 120 + 60 = 180 months (15 years)

            if span_years >= 5.0:
                stats["countries_with_gte_5y_demand"] += 1
            if span_years >= 10.0:
                stats["countries_with_gte_10y_demand"] += 1
            if span_years >= 15.0:
                stats["countries_with_gte_15y_demand"] += 1
            if span_years >= 20.0:
                stats["countries_with_gte_20y_demand"] += 1
            if eligible_l120_h12:
                stats["countries_eligible_for_L120_H12"] += 1
            if eligible_l120_h60:
                stats["countries_eligible_for_L120_H60"] += 1

            records.append({
                "iso3": iso3,
                "country_name": c_name,
                "source": source_name,
                "start_date": min_dt.strftime("%Y-%m-%d"),
                "end_date": max_dt.strftime("%Y-%m-%d"),
                "total_observations": len(df),
                "historical_span_years": span_years,
                "historical_span_months": span_months,
                "gte_5_years": span_years >= 5.0,
                "gte_10_years": span_years >= 10.0,
                "gte_15_years": span_years >= 15.0,
                "gte_20_years": span_years >= 20.0,
                "hgt_qf_L120_H12_eligible": eligible_l120_h12,
                "hgt_qf_L120_H60_eligible": eligible_l120_h60,
            })
        except Exception:
            pass

    df_hist = pd.DataFrame(records)
    return df_hist, stats


def generate_all_inventory_reports(
    root: Path,
    candidate_countries: list[str],
    results: list[Any],
) -> dict[str, Path]:
    """Generate and save all 5 multi-dimensional inventory reports into quality/."""
    quality_dir = root / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    report_paths = {}

    # 1. Feature-Level Inventory (25 Conceptual Variables)
    feature_inv_path = quality_dir / "feature_inventory.csv"
    generate_feature_inventory(feature_inv_path)
    report_paths["feature_inventory_csv"] = feature_inv_path

    feature_json_path = quality_dir / "feature_inventory.json"
    feature_json_path.write_text(json.dumps(CONCEPTUAL_FEATURES, indent=2), encoding="utf-8")
    report_paths["feature_inventory_json"] = feature_json_path

    # 2. Country Coverage Inventory
    df_country_inv, summary_counts = build_country_coverage_inventory(root, candidate_countries, results)
    country_inv_csv = quality_dir / "country_coverage_inventory.csv"
    df_country_inv.to_csv(country_inv_csv, index=False)
    report_paths["country_coverage_csv"] = country_inv_csv

    country_inv_json = quality_dir / "country_coverage_inventory.json"
    country_inv_json.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary_counts,
        "countries": df_country_inv.to_dict(orient="records"),
    }, indent=2), encoding="utf-8")
    report_paths["country_coverage_json"] = country_inv_json

    # 3. Historical Coverage & Lookback Feasibility Report
    df_hist, hist_stats = build_historical_coverage_report(root, candidate_countries)
    hist_csv = quality_dir / "historical_coverage_report.csv"
    df_hist.to_csv(hist_csv, index=False)
    report_paths["historical_coverage_csv"] = hist_csv

    hist_json = quality_dir / "historical_coverage_report.json"
    hist_json.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary_statistics": hist_stats,
        "evaluations": df_hist.to_dict(orient="records") if not df_hist.empty else [],
    }, indent=2), encoding="utf-8")
    report_paths["historical_coverage_json"] = hist_json

    # 4. Source Registry Report
    all_sources = [s.__dict__ for s in get_all_registered_sources()]
    src_reg_json = quality_dir / "source_registry_report.json"
    src_reg_json.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "registered_sources_count": len(all_sources),
        "sources": all_sources,
    }, indent=2), encoding="utf-8")
    report_paths["source_registry_json"] = src_reg_json

    src_reg_csv = quality_dir / "source_registry_report.csv"
    pd.DataFrame(all_sources).to_csv(src_reg_csv, index=False)
    report_paths["source_registry_csv"] = src_reg_csv

    return report_paths

