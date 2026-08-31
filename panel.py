"""Country-month panel assembly (the normalization layer).

Reads the feature files written by the acquisition connectors and assembles a
monthly panel per country:

    {out_dir}/panel/{ISO3}_{start}_{end}.parquet   (wide panel)
    {out_dir}/panel/{ISO3}_{start}_{end}_provenance.csv

Panel rules (no imputation, no interpolation, no scaling):
  * monthly features occupy their native month rows;
  * annual features occupy the January row of their year (NaN elsewhere) and
    are flagged ``frequency=annual`` in provenance — they are NEVER
    forward/backward filled;
  * features with no acquired file are left as NaN and flagged
    ``quality_flag=MISSING`` in provenance.

Provenance is long-format: one row per (country, feature) recording source,
dataset, variable, unit, frequency, aggregation method, original timestamp,
and quality flag.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# concept -> acquisition file spec(s), tried in priority order.
#   path:     location relative to the output root ({iso3} is substituted)
#   col:      value column in the file (None = use index / melted indicator)
#   freq:     "monthly" | "annual"
#   source:   provenance source name
#   dataset:  provenance dataset name
#   unit:     provenance unit
#   agg:      aggregation method (provenance)
FEATURE_SPECS: dict[str, list[dict[str, str]]] = {
    "electricity_demand": [{
        "path": "raw/electricity/demand/ember/{iso3}.csv", "col": "value",
        "freq": "monthly", "source": "Ember", "dataset": "electricity-demand/monthly",
        "unit": "TWh", "agg": "native monthly series",
    }],
    "renewable_generation_share": [{
        "path": "raw/electricity/renewable_generation_share/ember/{iso3}.csv", "col": "value",
        "freq": "monthly", "source": "Ember", "dataset": "electricity-generation/monthly (Renewables/Clean share)",
        "unit": "%", "agg": "native monthly share",
    }],
    "total_electricity_generation": [{
        "path": "raw/electricity/total_electricity_generation/ember/{iso3}.csv", "col": "value",
        "freq": "monthly", "source": "Ember", "dataset": "electricity-generation/monthly (Total generation)",
        "unit": "TWh", "agg": "native monthly series",
    }],
    "temperature_2m": [
        {"path": "climate/{iso3}_nasa_power.csv", "col": "temperature_2m", "freq": "monthly",
         "source": "NASA POWER", "dataset": "T2M daily->monthly mean", "unit": "°C",
         "agg": "daily mean -> monthly mean (centroid)"},
        {"path": "climate/{iso3}_era5.parquet", "col": "temperature_2m", "freq": "monthly",
         "source": "ERA5/CDS", "dataset": "era5 monthly 2m_temperature (bbox, area-weighted)",
         "unit": "°C", "agg": "area-weighted country mean"},
    ],
    "solar_radiation": [
        {"path": "climate/{iso3}_nasa_power.csv", "col": "solar_radiation", "freq": "monthly",
         "source": "NASA POWER", "dataset": "ALLSKY_SFC_SW_DWN daily->monthly mean", "unit": "kWh/m²/day",
         "agg": "daily mean -> monthly mean (centroid)"},
        {"path": "climate/{iso3}_era5.parquet", "col": "solar_radiation", "freq": "monthly",
         "source": "ERA5/CDS", "dataset": "era5 monthly surface_solar_radiation (bbox)",
         "unit": "kWh/m²/day", "agg": "area-weighted country mean"},
    ],
    "wind_speed_10m": [
        {"path": "climate/{iso3}_nasa_power.csv", "col": "wind_speed_10m", "freq": "monthly",
         "source": "NASA POWER", "dataset": "WS10M daily->monthly mean", "unit": "m/s",
         "agg": "daily mean -> monthly mean (centroid)"},
        {"path": "climate/{iso3}_era5.parquet", "col": "wind_speed_10m", "freq": "monthly",
         "source": "ERA5/CDS", "dataset": "era5 monthly 10m_wind_speed (bbox)",
         "unit": "m/s", "agg": "area-weighted country mean"},
    ],
    "precipitation": [
        {"path": "climate/{iso3}_nasa_power.csv", "col": "precipitation", "freq": "monthly",
         "source": "NASA POWER", "dataset": "PRECTOTCORR daily->monthly sum", "unit": "mm",
         "agg": "daily sum -> monthly total (centroid)"},
        {"path": "climate/{iso3}_era5.parquet", "col": "precipitation", "freq": "monthly",
         "source": "ERA5/CDS", "dataset": "era5 monthly total_precipitation (bbox)",
         "unit": "mm", "agg": "area-weighted country total"},
    ],
    "cooling_degree_days": [{
        "path": "climate/{iso3}_nasa_power.csv", "col": "cooling_degree_days", "freq": "monthly",
        "source": "NASA POWER", "dataset": "CDD derived from daily T2M (base 18°C)", "unit": "degree-days",
        "agg": "sum of daily (T-18)_+",
    }],
    "heating_degree_days": [{
        "path": "climate/{iso3}_nasa_power.csv", "col": "heating_degree_days", "freq": "monthly",
        "source": "NASA POWER", "dataset": "HDD derived from daily T2M (base 18°C)", "unit": "degree-days",
        "agg": "sum of daily (18-T)_+",
    }],
    # Annual socioeconomic features (World Bank, long format).
    "gdp": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv", "col": "gdp",
             "freq": "annual", "source": "World Bank", "dataset": "WDI NY.GDP.MKTP.CD", "unit": "current USD",
             "agg": "annual observation"}],
    "gdp_growth": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv", "col": "gdp_growth",
                    "freq": "annual", "source": "World Bank", "dataset": "WDI GDP growth", "unit": "%",
                    "agg": "annual observation"}],
    "gdp_per_capita": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv", "col": "gdp_per_capita",
                        "freq": "annual", "source": "World Bank", "dataset": "WDI GDP per capita", "unit": "current USD",
                        "agg": "annual observation"}],
    "inflation_cpi": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv", "col": "inflation_cpi",
                       "freq": "annual", "source": "World Bank", "dataset": "WDI CPI inflation", "unit": "%",
                       "agg": "annual observation"}],
    "total_population": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv", "col": "total_population",
                          "freq": "annual", "source": "World Bank", "dataset": "WDI SP.POP.TOTL", "unit": "count",
                          "agg": "annual observation"}],
    "population_growth": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv", "col": "population_growth",
                           "freq": "annual", "source": "World Bank", "dataset": "WDI SP.POP.GROW", "unit": "%",
                           "agg": "annual observation"}],
    "urban_population": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv", "col": "urban_population",
                          "freq": "annual", "source": "World Bank", "dataset": "WDI SP.URB.TOTL", "unit": "count",
                          "agg": "annual observation"}],
    "urbanisation_rate": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv", "col": "urbanisation_rate",
                           "freq": "annual", "source": "World Bank", "dataset": "WDI SP.URB.TOTL.IN.ZS", "unit": "%",
                           "agg": "annual observation"}],
    "electricity_access": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv", "col": "electricity_access",
                            "freq": "annual", "source": "World Bank", "dataset": "WDI EG.ELC.ACCS.ZS", "unit": "%",
                            "agg": "annual observation"}],
    "manufacturing_value_added": [{"path": "raw/socioeconomic/indicators/worldbank/{iso3}.csv",
                                   "col": "manufacturing_value_added", "freq": "annual", "source": "World Bank",
                                   "dataset": "WDI NV.IND.MANF.ZS", "unit": "%", "agg": "annual observation"}],
    # GPWv4 raster population (5-year, verification source).
    "population_gpwv4": [{"path": "population/gpwv4/{iso3}.csv", "col": "population",
                          "freq": "annual", "source": "GPWv4", "dataset": "GPWv4 population density rev11 zonal sum",
                          "unit": "persons", "agg": "zonal sum density × cell area"}],
}

DEFAULT_PANEL_FEATURES = [
    "electricity_demand",
    "temperature_2m", "solar_radiation", "wind_speed_10m", "precipitation",
    "gdp", "gdp_growth", "gdp_per_capita", "total_population", "population_growth",
    "urban_population", "urbanisation_rate", "electricity_access",
    "manufacturing_value_added", "renewable_generation_share",
    "total_electricity_generation",
]


def _load_frame(path: Path, spec: dict[str, str]) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _feature_series(root: Path, iso3: str, concept: str) -> tuple[pd.DataFrame, dict[str, str]]:
    """Return (series_df(date, value), provenance_spec) for a feature, or (empty, spec)."""
    specs = FEATURE_SPECS.get(concept, [])
    if not specs:
        return pd.DataFrame(columns=["date", "value"]), {"source": "", "dataset": "",
                                                         "unit": "", "freq": "", "agg": ""}
    for spec in specs:
        path = root / spec["path"].format(iso3=iso3)
        if not path.exists():
            continue
        try:
            df = _load_frame(path, spec)
        except Exception:  # noqa: BLE001
            continue
        if df.empty:
            continue
        col = spec["col"]
        if spec["freq"] == "annual" and "worldbank" in spec["path"]:
            # long format: filter the indicator and melt to (year, value)
            if "indicator" in df.columns and "value" in df.columns:
                sub = df[df["indicator"].astype(str).str.strip() == col][["year", "value"]]
                sub = sub.dropna(subset=["value"]).rename(columns={"year": "date"})
                sub["date"] = pd.to_datetime(sub["date"].astype(int), format="%Y")
                return sub[["date", "value"]], spec
            continue
        date_col = "date" if "date" in df.columns else df.index.name
        if date_col is None:
            continue
        if "date" in df.columns:
            sub = df[["date", col]].copy()
        else:
            sub = df[[col]].copy()
            sub["date"] = df.index
        sub = sub.rename(columns={col: "value"})
        sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
        sub["date"] = pd.to_datetime(sub["date"], errors="coerce")
        sub = sub.dropna(subset=["date", "value"])
        return sub[["date", "value"]].sort_values("date"), spec
    return pd.DataFrame(columns=["date", "value"]), specs[0]


def _monthly_index(start: int, end: int) -> pd.DatetimeIndex:
    return pd.period_range(f"{start}-01", f"{end}-12", freq="M").to_timestamp()


def build_country_panel(
    iso3: str,
    start: int,
    end: int,
    root: Path | str,
    features: list[str] | None = None,
) -> dict[str, Path]:
    """Build the monthly panel + provenance for one country."""
    root = Path(root)
    features = features or DEFAULT_PANEL_FEATURES
    idx = _monthly_index(start, end)
    panel = pd.DataFrame({"date": idx})
    panel["iso3"] = iso3

    provenance_rows: list[dict[str, Any]] = []
    for concept in features:
        series, spec = _feature_series(root, iso3, concept)
        if series.empty:
            panel[concept] = np.nan
            provenance_rows.append({
                "iso3": iso3, "feature": concept, "source": "", "dataset": "",
                "variable": concept, "unit": "", "frequency": "", "aggregation_method": "",
                "original_timestamp": "", "quality_flag": "MISSING",
            })
            continue
        series = series.drop_duplicates(subset=["date"], keep="first")
        if spec.get("freq") == "annual":
            # annual -> January of the year (never forward/backward filled)
            jan = pd.to_datetime(series["date"].dt.year.astype(int), format="%Y")
            series = series.assign(date=jan)
            series = series.drop_duplicates(subset=["date"], keep="first")
            panel[concept] = pd.Series(index=idx, dtype="float64")
            hit = panel["date"].isin(series["date"])
            panel.loc[hit, concept] = series.set_index("date")["value"].reindex(
                panel.loc[hit, "date"]).values
        else:
            monthly = series.set_index("date")["value"]
            panel[concept] = monthly.reindex(idx).values
        provenance_rows.append({
            "iso3": iso3, "feature": concept,
            "source": spec.get("source", ""), "dataset": spec.get("dataset", ""),
            "variable": concept, "unit": spec.get("unit", ""),
            "frequency": spec.get("freq", ""), "aggregation_method": spec.get("agg", ""),
            "original_timestamp": "",
            "quality_flag": "RAW" if not panel[concept].isna().all() else "MISSING",
        })

    panel = panel[["iso3", "date"] + list(features)]
    out_dir = root / "panel"
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = out_dir / f"{iso3}_{start}_{end}.parquet"
    csv_path = out_dir / f"{iso3}_{start}_{end}.csv"
    panel.to_parquet(parquet_path, index=False)
    panel.to_csv(csv_path, index=False)

    prov = pd.DataFrame(provenance_rows)
    prov_path = out_dir / f"{iso3}_{start}_{end}_provenance.csv"
    prov.to_csv(prov_path, index=False)
    return {"panel": parquet_path, "csv": csv_path, "provenance": prov_path}


def assemble_panel(
    countries: list[str],
    start: int,
    end: int,
    root: Path | str,
    features: list[str] | None = None,
) -> dict[str, Any]:
    """Build panels for many countries and one combined panel."""
    root = Path(root)
    built: list[dict[str, Path]] = []
    frames: list[pd.DataFrame] = []
    for iso3 in countries:
        try:
            paths = build_country_panel(iso3, start, end, root, features)
            built.append(paths)
            frames.append(pd.read_parquet(paths["panel"]))
        except Exception as exc:  # noqa: BLE001
            built.append({"iso3": iso3, "error": str(exc)})
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    combined_path = root / "panel" / f"all_countries_{start}_{end}.parquet"
    if not combined.empty:
        (root / "panel").mkdir(parents=True, exist_ok=True)
        combined.to_parquet(combined_path, index=False)
    return {"panels": built, "combined": str(combined_path) if not combined.empty else ""}


__all__ = [
    "FEATURE_SPECS", "DEFAULT_PANEL_FEATURES", "build_country_panel", "assemble_panel",
]
