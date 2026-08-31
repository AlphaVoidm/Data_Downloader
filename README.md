# HGT-QF Data Downloader (v3.0)

A **source-aware scientific data acquisition &amp; verification system** for the HGT-QF
electricity-demand forecasting project. It does exactly one job — **discover, verify,
download, and prove** the data — and nothing downstream.

It determines *what data actually exists for every country and feature* **before
downloading anything**, selects the best available authoritative source per
country × feature, verifies the endpoint, retrieves only the minimum required subset,
and produces a complete country × feature coverage audit plus full provenance.

The core principle: **download → verify → extract → discard the huge source file** —
never keep multi-GB global rasters when a country-level monthly value is sufficient.

> **Scope.** This is **acquisition/verification ONLY**. It does **not** do
> imputation, interpolation, normalization, standardization, scaling, feature
> engineering, lag/rolling-feature creation, train/test splitting, model training,
> or ML evaluation. Computed CDD/HDD from daily temperature is performed as *data
> extraction*, not preprocessing. CMIP6 is used only for the future climate
> scenario module — never for historical backfill.

---

## Design

| # | Component | Module |
|---|-----------|--------|
| 1 | Country Registry (ISO-3, name, region, centroid, bbox) | `country_registry.py` |
| 2 | Feature Registry (target / core / extended / optional tiers) | `feature_registry.py` |
| 3 | Source Registry (13 sources, full metadata) | `source_registry.py` |
| 4 | Coverage Engine (deterministic discovery, no HTTP) | `coverage_engine.py` |
| 5 | Response Validator (never trust HTTP 200) | `response_validator.py` |
| 6 | Connectors (EIA / ENTSO-E / CDS / Ember / NASA / …) | `connectors/` |
| 7 | Acquisition Engine (coverage-gated, fallback download) | `acquisition_engine.py` |
| 8 | Source-status vocabulary (`SOURCE_*` fallback reporting) | `status_vocabulary.py` |
| 9 | Readiness Evaluation (TARGET / FEATURE / RESEARCH, diverse) | `readiness.py` |
| 10 | Research configuration (researcher-adjustable thresholds) | `research_config.py` |
| 11 | Availability Audit (Reports A &amp; C) | `availability_audit.py` |
| 12 | Acquisition Report &amp; Provenance (Report B) | `acquisition_report.py` |

Configuration lives in `config/`:

```
config/
├── country_registry.csv           # 194 countries + bboxes (regenerated)
├── feature_config.json            # 25 features in target/core/extended/optional tiers
├── research_config.json           # researcher-adjustable RESEARCH_READY thresholds
├── source_registry.json           # centralized 13-source registry
├── source_area_mapping.csv        # ENTSO-E EIC / EIA / NESO / AEMO area codes
├── ember_monthly_geographies.csv  # Ember monthly geography set (~88)
└── owid_ev_countries.csv          # OWID EV country set (~63)
```

### Feature model (three tiers, 25 features)

- **TARGET** (1): `electricity_demand` — the forecasting target; THE fundamental
  requirement. Nothing else is mandatory.
- **CORE** (13): `temperature_2m`, `solar_radiation`, `wind_speed_10m`,
  `precipitation`, `gdp`, `gdp_growth`, `gdp_per_capita`, `total_population`,
  `population_growth`, `urbanisation_rate`, `electricity_access`,
  `manufacturing_value_added`, `renewable_generation_share`.
- **EXTENDED** (6): `total_electricity_generation`, `generation_mix`,
  `inflation_cpi`, `urban_population`, `cooling_degree_days`,
  `heating_degree_days` — tracked, never required (`generation_mix` has no
  reliable public path yet, so it stays out of core).
- **OPTIONAL** (5): `electricity_prices`, `ev_stock_sales`,
  `sectoral_electricity_demand`, `ac_heat_pump_penetration`, `public_holidays` —
  coverage-limited; **never disqualify a country**.

`cooling_degree_days` / `heating_degree_days` are **derived features** computed from
*daily* temperature at extraction time (base 18 °C) — they require a daily-capable
source (NASA POWER).

---

## Status systems (kept separate)

**Discovery** — `SUPPORTED` · `NOT_SUPPORTED` · `AUTH_REQUIRED` ·
`MAPPING_REQUIRED` · `TEMPORARILY_UNAVAILABLE` · `UNKNOWN`.
A source that simply doesn't cover a country is `NOT_SUPPORTED`, never an error.

**Demand classification** — `MONTHLY_SUFFICIENT` · `MONTHLY_PARTIAL` ·
`ANNUAL_ONLY` · `UNAVAILABLE`. Annual data is **never** treated as monthly, and
monthly series are **never** synthesized from annual data. Each classification is
backed by evidence: first/last month, expected vs observed monthly observations,
missing months, longest continuous run, and gap count.

**Three-tier readiness** (independent — never collapsed into one flag):
- **TARGET_READY** — electricity-demand availability ONLY
  (`MONTHLY_SUFFICIENT`/…), from a configurable rule (`min_history_months`,
  `min_consecutive_months`).
- **FEATURE_COVERAGE** — independent `core / extended / optional` counts per
  country (e.g. `13/13`, `6/6`, `2/5`).
- **RESEARCH_READY** — `TARGET_READY` **AND** configurable minimum core coverage
  (default 80%); optional/extended features are reported but never gate.

**Per-source fallback statuses** — `SOURCE_SUCCESS` · `SOURCE_NOT_COVERED` ·
`SOURCE_AUTH_REQUIRED` · `SOURCE_TEMPORARILY_UNAVAILABLE` · `SOURCE_RATE_LIMITED` ·
`SOURCE_FORMAT_ERROR` · `SOURCE_API_ERROR` · `SOURCE_DATA_EMPTY`.
A country is never marked unavailable merely because ONE source did not cover it;
the full per-source fallback chain is recorded with reasons.

**Response validation** — `OK` · `AUTH_FAILED` · `PORTAL_HTML` ·
`NON_DATA_RESPONSE` · `INVALID_XML` · `INVALID_JSON` · `INVALID_CSV` ·
`EMPTY_RESPONSE` · `SCHEMA_MISMATCH` · `NO_RECORDS` · `RATE_LIMITED` ·
`NETWORK_ERROR` · `TIMEOUT`. HTTP 200 alone is **never** success: Content-Type,
magic bytes, format validity, schema/columns, and record presence are all checked.

---

## Quick Start

### 1. Environment

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the keys you have (all optional — the
system degrades to open sources when a key is absent):

```bash
EIA_API_KEY=            # EIA Open Data v2
ENTSOE_API_TOKEN=       # ENTSO-E Transparency REST
CDS_API_KEY=            # Copernicus Climate Data Store (ERA5)
EMBER_API_KEY=          # Ember API
```

Keys are loaded from `.env` (gitignored) and are never hard-coded or printed.

### 2. Tests

```bash
python run_tests.py
```

### 3. Command-line usage

```bash
# 1) DISCOVERY AUDIT (no downloads) — always run this FIRST
python main.py audit --start 2000 --end 2024 --output hgt_qf_audit

# Audit a subset of countries, or tune the RESEARCH_READY rule:
python main.py audit --countries EGY DEU FRA GBR USA JPN --start 2000 --end 2024
python main.py audit --min-demand-history 300 --min-core-coverage 1.0 --require-optional

# 2) ACQUISITION — only after the matrix is validated.
#    Downloads only the countries + features you select.
python main.py acquire --countries EGY DEU GBR --features electricity_demand temperature_2m \
    --start 2000 --end 2024 --output hgt_qf_data

# 3) audit -> acquire for RESEARCH_READY countries only
python main.py run --start 2000 --end 2024 --output hgt_qf_data --min-core-coverage 0.8

# Introspection
python main.py countries          # 194 registered countries
python main.py sources            # 13 registered sources + metadata
```

### 4. Streamlit dashboard

```bash
python -m streamlit run app.py
```

The **🧭 Availability Audit** tab runs the deterministic discovery audit (Reports A
&amp; C) on the selected countries before any acquisition. It is discovery-only:
no downloads.

---

## Reports

Produced in `<output>/metadata/`:

| Report | File | Contents |
|--------|------|----------|
| **A — Global availability / provenance registry** | `report_A_source_coverage.csv` | country × feature × candidate sources with per-source `SOURCE_*` status, best source, frequency, license, auth, retrieval method, verification URL |
| **B — Acquisition** | `report_B_acquisition.csv` + `provenance.json` | per-download provenance: source, dataset id, URL, requested/actual period, frequency, units, records, `SOURCE_*` status, attempt history, checksum, output path |
| **C — Three-tier readiness** | `report_C_readiness.csv` | per-country `TARGET_READY` evidence (first/last month, expected/observed/missing months, longest continuous run, gaps), `core/extended/optional` coverage, `RESEARCH_READY` + reason |
| **Feature summary** | `report_feature_summary.csv` | per-feature availability counts across all countries (`X/194`) |

Plus `availability_audit.json`, the full machine-readable audit payload.

### Provenance (every download)

country · ISO3 · feature · source · dataset · source identifier · source URL ·
requested period · received period · frequency · units · record count · retrieval
timestamp (UTC) · auth status · verification status · verification notes · attempt
history · checksum (SHA-256) · output path. No unexplained CSVs.

---

## Storage architecture

```text
hgt_qf_data/                 # acquisition output (gitignored)
├── raw/                     # source-native tabular CSVs + .meta.json sidecars
├── climate/                 # compact country-level monthly Parquet/CSV (ERA5 reduced)
└── metadata/
    ├── report_B_acquisition.csv
    └── provenance.json

hgt_qf_audit/                # audit output (gitignored, reproducible)
└── metadata/
    ├── report_A_source_coverage.csv
    ├── report_C_readiness.csv
    ├── report_feature_summary.csv
    └── availability_audit.json
```

ERA5/CMIP6 requests use **targeted bbox/variable/period extraction**, are reduced to
a compact country-level monthly series (cos-latitude area weighting), and the
temporary bulk NetCDF is **deleted**.

---

## Data source overview

| Source | Feature(s) | Coverage | Frequency | Auth |
|--------|-----------|----------|-----------|------|
| ENTSO-E Transparency | electricity_demand | European perimeter (EIC-mapped) | hourly | token |
| EIA Open Data (v2) | electricity_demand, sectoral demand | USA (+ facet discovery) | hourly | key |
| ESO / NESO | electricity_demand | Great Britain | half-hourly | open |
| AEMO | electricity_demand | Australia (NEM) | five-minute | open |
| Ember | demand, generation, mix, renewables | ~215 yearly / ~88 monthly | monthly+annual | key |
| World Bank | GDP, inflation, population, access, MVA | global | annual | open |
| NASA POWER | temperature, solar, wind, precipitation | global | daily | open |
| ERA5 / CDS | temperature, solar, wind, precipitation | global (bbox extraction) | monthly/hourly | CDS key |
| Nager.Date | public holidays | 100+ countries | annual | open |
| Eurostat | electricity prices, sectoral demand | EU-27 + EFTA + GB | monthly/annual | open |
| Our World in Data | EV stock/sales | ~63 countries | annual | open |
| IRENA | renewable generation share | global | annual | open |
| IEA | EV / prices / sectoral | variable | annual/monthly | restricted |

> Per-source country membership (Ember monthly, OWID EV, ENTSO-E EIC codes) is
> stored as documented country sets in `config/` and reconciled against provider
> catalogues; the audit flags `MAPPING_REQUIRED` when an area/series code is not
> explicitly mapped rather than fabricating one.

---

## Acquisition strategy

For every requested country × feature, the downloader picks the least wasteful
strategy and falls back through the source priority list before reporting failure:

```text
API with server-side filtering   →  retrieve only the required subset
small downloadable file          →  fetch directly
filtered downloadable file       →  fetch the filtered slice
bulk dataset + remote extraction →  subset + reduce + delete the temporary bulk file
skip / report unavailable        →  honest SOURCE_* status (never fabricate)
```

Climate data (ERA5/CMIP6) is requested with a **targeted bbox + variables +
period**, reduced to a compact `country × month` table (cos-latitude area
weighting), and the temporary bulk NetCDF is deleted. The downloader **never**
stores `country × latitude × longitude × month` rasters per country.
