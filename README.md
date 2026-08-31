# HGT-QF Data Downloader (v3.0)

A **source-aware scientific data acquisition system** for the HGT-QF electricity-demand
forecasting project. It determines *what data actually exists for every country and
feature* **before downloading anything**, selects the best available authoritative
source, retrieves only the minimum required subset, and produces a complete
country × feature coverage matrix plus a full provenance report.

The core principle: **download → extract → discard the huge source file** — never
keep multi-GB global rasters when a country-level monthly value is sufficient.

---

## The Seven Components

| # | Component | Module |
|---|-----------|--------|
| 1 | Country Registry (ISO-3, name, region, centroid, bbox) | `country_registry.py` |
| 2 | Feature Registry (25 HGT-QF variables, source candidates, priorities) | `feature_registry.py` |
| 3 | Source Registry (coverage scope, frequency, auth, variables) | `source_registry.py` |
| 4 | Coverage Engine (country × feature × source × period) | `coverage_engine.py` |
| 5 | Acquisition Engine (coverage-gated download) | `acquisition_engine.py` |
| 6 | Scientific Data Extractor (ERA5/CMIP6 → compact aggregate) | `scientific_extractor.py` |
| 7 | Provenance & Quality Report | `acquisition_report.py` |

Config lives in `config/`:

```
config/
├── country_registry.csv           # 194 countries + bboxes (74 curated)
├── feature_registry.csv           # 25 features + ordered source candidates
├── source_registry.csv            # 16 sources + coverage metadata
├── source_area_mapping.csv        # ENTSO-E EIC / EIA / NESO / AEMO area codes
└── ember_monthly_geographies.csv  # Ember monthly geography set (~88; reconcile with catalogue)
```

---

## Coverage decision statuses

`AVAILABLE` · `PARTIAL_AVAILABLE` (coarser freq / partial period) · `NOT_COVERED` ·
`VARIABLE_NOT_AVAILABLE` · `PERIOD_NOT_AVAILABLE` · `ACCESS_REQUIRES_AUTH` · `UNKNOWN`

The Coverage Engine is **deterministic and makes no network requests**. It uses the
registries to answer, for every country, which source is the best candidate for each
feature at the required frequency over the requested period — and which sources must
be skipped (e.g. ENTSO-E for Egypt, AEMO for France) **without any HTTP call**.

---

## Quick Start

### 1. Environment

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Optional, for the scientific geospatial extractor (ERA5/CMIP6 via CDS):

```bash
pip install cdsapi xarray pyarrow
```

### 2. Tests

```bash
python run_tests.py
```

### 3. Command-line usage

```bash
# Global availability audit (no downloads) — run this FIRST
python main.py audit --start 2000 --end 2024

# Audit a subset of countries / a preset
python main.py audit --countries EGY DEU FRA GBR USA JPN --start 2000 --end 2024
python main.py audit --countries "G7" --top 20

# List registered countries
python main.py countries

# Coverage-gated acquisition (only downloads confirmed-available data)
python main.py acquire --countries EGY DEU GBR --start 2000 --end 2024

# Audit then acquire
python main.py run --countries EGY DEU GBR --start 2000 --end 2024

# Regenerate the country registry from data_source_log.xlsx
python main.py rebuild-country-registry
```

### 4. Streamlit dashboard

```bash
python -m streamlit run app.py
```

The dashboard includes a **🧭 Availability Audit** tab that runs the deterministic
coverage audit on the selected countries before any acquisition.

---

## Storage architecture

```text
<output_dir>/
├── raw/            # tabular source-native CSVs + .meta.json sidecars
├── climate/        # compact country-level monthly Parquet/CSV (ERA5 reduced)
├── quality/        # quality tiers, demand coverage, conflicts
└── metadata/       # NEW — the coverage matrix, audit + acquisition provenance
    ├── coverage_matrix.csv            # country × feature availability
    ├── feature_coverage_detail.csv    # country × feature × best source + status
    ├── source_selection_table.csv     # every country × feature × source decision
    ├── feature_coverage_summary.csv   # per-feature availability counts
    ├── recommended_countries.csv      # ranked HGT-QF country recommendations
    ├── availability_audit.json        # full audit payload
    ├── acquisition_report.csv         # per-variable provenance (Component 7)
    └── provenance.json                # consolidated provenance
```

Temporary bulk NetCDF files from ERA5/CMIP6 requests are written to a system temp
directory, reduced to a country-level series, then **deleted**.

---

## Extraction modes (Component 6)

- **MODE A — RAW_SUBSET**: retain the country bounding-box grid subset (compact).
- **MODE B — COUNTRY_AGGREGATE** (default): reduce to a country-level monthly series
  (cos-latitude area weighting), store Parquet/CSV, delete the temporary NetCDF.

The extractor degrades gracefully: without `CDS_API_KEY` (or `CDSAPI_KEY`/`~/.cdsapirc`)
it returns `ACCESS_REQUIRES_AUTH`; without `cdsapi`/`xarray` it returns
`DEPENDENCY_MISSING` — never a crash.

---

## Data source overview

| Source | Feature | Coverage | Frequency | Priority |
|--------|---------|----------|-----------|----------|
| Ember | demand / generation / mix / renewables | 215 yearly, ~88 monthly | monthly/annual | ⭐⭐⭐ |
| World Bank | GDP / population / access / structure | global | annual | ⭐⭐⭐ |
| ERA5 / CDS | climate (temperature, wind, solar, precip) | global | monthly/hourly | ⭐⭐⭐ |
| NASA POWER | climate fallback | global | daily | ⭐⭐⭐ |
| ENTSO-E | European demand | 35+ bidding zones | hourly | ⭐⭐⭐ |
| EIA | US demand | USA | hourly | ⭐⭐ |
| NESO / ESO | GB demand | Great Britain | half-hourly | ⭐⭐ |
| AEMO | Australian demand | Australia NEM | 5-minute | ⭐⭐ |
| Nager.Date | public holidays | 100+ countries | annual | ⭐⭐ |
| IEA | EV / prices / AC / sectoral | variable (restricted) | annual/monthly | ⭐⭐ |
| IRENA | renewables | global | annual | ⭐⭐ |
| Eurostat | EU prices / sectoral | EU-27 + EFTA + GB | monthly/annual | ⭐⭐ |
| OWID | EV stock | ~60 countries | annual | ⭐ |
| CMIP6 / CDS | future climate (SSP) | global | monthly | ⭐ |
| IIASA SSP | population / GDP scenarios | global | 5-year | ⭐ |
| GPWv4 | gridded population | global | 5-year | ⭐ |

> **Note:** per-source country membership (e.g. Ember monthly, OWID EV) is stored as
> configurable, documented country sets and should be reconciled against the provider
> catalogues. The audit output flags this explicitly.
