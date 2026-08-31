# HGT-QF Data Desk (v2.1)

A resilient multi-source data acquisition engine, automated quality tier evaluation system, cross-source conflict detector, and Streamlit dashboard for electricity-demand forecasting and energy economics research.

---

## Key Features

1. **Domain-Based Hierarchy**: Raw data is preserved in clean, unmerged source-native structures:
   `raw/<domain>/<category>/<source>/<ISO3>.csv`
2. **Extended Status Taxonomy**: 9 fine-grained status types (`success` 🟢, `partial` 🟡, `empty` ⚪, `rate-limited` 🟠, `credential-required` 🔑, `not-covered` ⚪, `schema-changed` 🟣, `invalid` 🔴, `failed` ❌).
3. **Data Quality Tiers**: Multi-dimensional scoring (Completeness, Continuity, Validity, Timeliness) classifying datasets into **Gold 🥇 (Tier 1)**, **Silver 🥈 (Tier 2)**, **Bronze 🥉 (Tier 3)**, and **Insufficient ⚠️ (Tier 4)**.
4. **Cross-Source Conflict Detection**: Automatic discrepancy detection, percentage delta ($\delta$) analysis, and severity alert flagging for overlapping indicators across providers.
5. **Standardized Observation Columns**: Explicit `observed: bool` flags and automatic sanitization of provider-specific missing sentinels (e.g. NASA POWER `-999.0` converted to `NaN`).
6. **Optimized Multi-Indicator Queries**: World Bank adapter with connection pooling, multi-page traversal, and multi-indicator fallback resilience.
7. **Spatial Centroid Lookup**: NASA POWER weather engine supporting 200+ country centroids with MERRA-2 date range clamping.
8. **Provenance & Auditing**: Automatic SHA-256 hashing, dataset schemas, response latencies, license citations, and sidecar `.meta.json` files.
9. **Country Normalization**: Normalizes ISO-3, ISO-2, common aliases, disputed territories (Kosovo `XKX`, Taiwan `TWN`, Palestine `PSE`, Hong Kong `HKG`, Vatican `VAT`), and regional presets (G7, G20, EU-27, Africa Top 12, Middle East, Asia-Pacific, Latin America).

---

## Quick Start

### 1. Environment Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Run Automated Test Suite

```powershell
python run_tests.py
```

### 3. Launch Streamlit Dashboard

```powershell
python -m streamlit run app.py
```

---

## Project Structure

```
DataDownloader/
├── app.py                      # Multi-tab Streamlit dashboard
├── pipeline.py                 # Core acquisition pipeline and adapter orchestrator
├── country_utils.py            # Country normalization, aliases, presets & 200+ centroids
├── quality_tiers.py            # Quality scoring & Gold/Silver/Bronze tier classification
├── conflict_detection.py       # Cross-source discrepancy & conflict detector
├── coverage_analysis.py        # Monthly electricity demand coverage & gap matrix
├── directory_structure.py      # Domain/category/source folder management
├── provenance.py               # SHA-256, schema, license attribution & sidecar generator
├── ember_adapter.py            # Ember monthly demand adapter
├── data_source_log.xlsx        # Excel country lookup table
├── run_tests.py                # Automated test runner
├── tests/                      # Comprehensive test suite
│   ├── test_country_normalization.py
│   ├── test_coverage_analysis.py
│   ├── test_quality_tiers.py
│   ├── test_conflict_detection.py
│   ├── test_provenance.py
│   └── test_pipeline_status.py
└── requirements.txt            # Python dependencies
```

---

## Output Architecture

When a run executes, datasets and audit logs land in the designated directory:

```
<output_dir>/
├── raw/                                  # Source-native CSV files and .meta.json sidecars
│   ├── electricity/demand/
│   │   ├── ember/<ISO3>.csv
│   │   └── neso/<ISO3>.csv
│   ├── weather/observations/
│   │   └── nasa_power/<ISO3>.csv
│   ├── socioeconomic/indicators/
│   │   └── worldbank/<ISO3>.csv
│   └── calendar/holidays/
│       └── nager_date/<ISO3>.csv
├── quality/                              # Quality reports and diagnostics
│   ├── quality_tiers_summary.csv        # Multi-dimensional quality breakdown
│   ├── data_quality_report.json         # Tier distribution and dataset evaluations
│   ├── demand_coverage_matrix.csv       # Monthly coverage % and gap spans
│   ├── source_conflicts.csv             # Multi-source discrepancies and delta %
│   ├── conflict_report.json             # Discrepancy report
│   └── provenance_manifest.json         # Consolidated audit log with SHA-256 hashes
├── manifest_<mode>.json                 # Run configuration and layer map
└── availability_<mode>.json             # Per-source outcome status log
```
