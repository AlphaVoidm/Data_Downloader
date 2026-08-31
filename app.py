"""HGT-QF Data Acquisition and Inventory System (Final Specification).

Interactive research console for source-verified global data acquisition,
provenance auditing, and multi-dimensional coverage inventory for electricity-demand forecasting.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from country_utils import (
    REGIONAL_PRESETS,
    get_country_coordinates,
    get_country_name,
    get_preset_countries,
    normalize_country,
)
from availability_audit import render_audit_report, run_availability_audit
from pipeline import (
    SOURCES,
    STATUS_BADGES,
    load_country_log,
    run_pipeline,
)
from source_mapping import load_source_area_mappings
from source_registry import get_all_registered_sources

load_dotenv()
st.set_page_config(
    page_title="HGT-QF Data Desk | Acquisition & Inventory",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Space+Grotesk:wght@500;600;700&display=swap');
:root {
    --ink: #172126;
    --muted: #65747a;
    --paper: #f4f1e9;
    --mint: #c9e5d3;
    --coral: #e9775b;
    --blue: #2980b9;
    --line: #d9d6cc;
}
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; color: var(--ink); }
.stApp { background: radial-gradient(circle at 85% 8%, #f8d8bd 0, transparent 28%), linear-gradient(135deg, #f4f1e9 0%, #e7f1ea 100%); }
h1, h2, h3, h4 { font-family: 'Space Grotesk', sans-serif; letter-spacing: -0.02em; }
.hero { padding: 1.2rem 0 1rem; border-bottom: 1px solid var(--line); margin-bottom: 1rem; }
.kicker { color: var(--coral); font-weight: 700; letter-spacing: .12em; text-transform: uppercase; font-size: .8rem; }
.card { background: rgba(255,255,255,.75); border: 1px solid var(--line); padding: 1rem; border-radius: 8px; margin-bottom: 0.8rem; }
div.stButton > button { background: var(--coral); color: white; border: 0; border-radius: 6px; font-weight: 700; min-height: 2.8rem; }
[data-testid="stMetric"] { background: rgba(255,255,255,.75); border: 1px solid var(--line); padding: 0.9rem; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <div class="kicker">HGT-QF / DATA ACQUISITION & INVENTORY SYSTEM</div>
  <h1>Global Electricity Demand & Explanatory Data Desk</h1>
  <p>Source-verified acquisition, area mapping resolution, raw data preservation, and reproducible inventory auditing.</p>
</div>
""", unsafe_allow_html=True)

# ============================================================================
# Sidebar Configuration
# ============================================================================

with st.sidebar:
    st.header("⚙️ Run Configuration")
    mode = st.radio(
        "Execution Mode",
        ["short-term", "long-term"],
        format_func=lambda x: "⚡ Short-term (Hourly/Sub-hourly grid load)" if x == "short-term" else "🌍 Long-term (Monthly/Annual/Daily panel data)",
    )

    raw_dir = st.text_input("Output directory", value=str(Path.cwd() / "hgt_qf_data"))

    st.subheader("🌐 Candidate Countries")
    preset_choice = st.selectbox("Quick Presets", ["Custom / Manual Input"] + list(REGIONAL_PRESETS.keys()))

    country_log_path = st.text_input("Country log (.xlsx)", value=str(Path.cwd() / "data_source_log.xlsx"))
    excel_loaded_countries = []
    if Path(country_log_path).exists():
        try:
            excel_loaded_countries = load_country_log(country_log_path)
            st.caption(f"📁 Loaded {len(excel_loaded_countries)} candidate countries from Excel.")
        except Exception as exc:
            st.caption(f"⚠️ Excel parsing: {exc}")

    if preset_choice != "Custom / Manual Input":
        default_countries = "\n".join(get_preset_countries(preset_choice))
    elif excel_loaded_countries:
        default_countries = "\n".join(excel_loaded_countries[:10])
    else:
        default_countries = "Germany\nFrance\nUnited Kingdom\nUnited States\nEgypt\nAustralia\nSouth Africa"

    country_text = st.text_area("Countries or ISO-3 codes (one per line)", value=default_countries, height=130)

    current_yr = date.today().year
    col_yr1, col_yr2 = st.columns(2)
    with col_yr1:
        start_year = st.number_input("Start Year", min_value=1980, max_value=current_yr, value=2015 if mode == "short-term" else 2000)
    with col_yr2:
        end_year = st.number_input("End Year", min_value=1980, max_value=2100, value=current_yr)

    st.subheader("🔑 API Credentials")
    with st.expander("Configure Tokens / Keys", expanded=False):
        st.caption("Credentials remain in memory and are never written to data manifests.")
        cred_keys = sorted({s.credential for s in SOURCES if s.credential})
        entered_credentials = {}
        for k in cred_keys:
            env_val = os.getenv(k, "")
            status_icon = "🟢 Set via Env" if env_val else "⚪ Not Set"
            entered_credentials[k] = st.text_input(
                f"{k} ({status_icon})",
                value=env_val,
                type="password",
                help=f"API token for {k}",
            )

    run_btn = st.button("🚀 Run Acquisition & Build Inventory", type="primary", use_container_width=True)

# ============================================================================
# Main Dashboard Tabs
# ============================================================================

tabs = st.tabs([
    "🚀 Acquisition & Status",
    "🧭 Availability Audit",
    "🗺️ Source Area Mappings",
    "🌐 Country Coverage Inventory",
    "📦 Feature Inventory (25 Vars)",
    "⏳ Historical Depth & L=120 Feasibility",
    "📜 Dataset Manifests & Hashes",
    "📚 Source Registry & Licenses",
])

# Initialize session state
if "pipeline_results" not in st.session_state:
    st.session_state.pipeline_results = None
if "run_mode" not in st.session_state:
    st.session_state.run_mode = mode
if "output_root" not in st.session_state:
    st.session_state.output_root = raw_dir
if "countries" not in st.session_state:
    st.session_state.countries = []

# Handle Run Execution
if run_btn:
    parsed_countries: list[str] = []
    invalid_inputs: list[str] = []
    for line in country_text.replace(",", "\n").splitlines():
        line_clean = line.strip()
        if line_clean:
            iso3 = normalize_country(line_clean)
            if iso3:
                if iso3 not in parsed_countries:
                    parsed_countries.append(iso3)
            else:
                invalid_inputs.append(line_clean)

    if invalid_inputs:
        st.error(f"❌ Unrecognized country entries: {', '.join(invalid_inputs)}")
    elif not parsed_countries:
        st.error("❌ Please provide at least one valid candidate country.")
    elif end_year < start_year:
        st.error("❌ End year must be greater than or equal to start year.")
    else:
        with tabs[0]:
            status_box = st.status("Data acquisition and verification running...", expanded=True)
            results = run_pipeline(
                countries=parsed_countries,
                mode=mode,
                raw_dir=raw_dir,
                start=int(start_year),
                end=int(end_year),
                progress=status_box.write,
                credentials=entered_credentials,
            )
            status_box.update(label="✅ Acquisition and multi-dimensional inventory complete!", state="complete", expanded=False)

        st.session_state.pipeline_results = results
        st.session_state.run_mode = mode
        st.session_state.output_root = raw_dir
        st.session_state.start_yr = int(start_year)
        st.session_state.end_yr = int(end_year)
        st.session_state.countries = parsed_countries

# ============================================================================
# Tab 1: Acquisition & Status
# ============================================================================
with tabs[0]:
    if st.session_state.pipeline_results is not None:
        results = st.session_state.pipeline_results
        df_res = pd.DataFrame([r.__dict__ for r in results])

        st.subheader("Acquisition Results Summary")
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        total_attempts = len(df_res)
        success_count = int((df_res.status == "SUCCESS").sum())
        partial_count = int((df_res.status == "PARTIAL_SUCCESS").sum())
        mapping_missing = int((df_res.status == "MAPPING_MISSING").sum())
        not_covered = int((df_res.status == "SOURCE_NOT_COVERED").sum())
        no_data = int((df_res.status == "NO_DATA_AVAILABLE").sum())

        m1.metric("Countries", len(st.session_state.countries))
        m2.metric("🟢 SUCCESS", success_count)
        m3.metric("🟡 PARTIAL", partial_count)
        m4.metric("🔵 MAPPING_MISSING", mapping_missing)
        m5.metric("⚪ NOT_COVERED", not_covered)
        m6.metric("⚪ NO_DATA", no_data)

        st.write("---")
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            status_filter = st.multiselect(
                "Filter by Status",
                options=df_res["status"].unique().tolist(),
                default=df_res["status"].unique().tolist(),
            )
        with col_f2:
            country_filter = st.multiselect(
                "Filter by Country",
                options=df_res["country_name"].unique().tolist(),
                default=df_res["country_name"].unique().tolist(),
            )

        filtered_df = df_res[
            (df_res["status"].isin(status_filter)) &
            (df_res["country_name"].isin(country_filter))
        ]

        display_cols = ["country_name", "country", "source", "status_badge", "records", "message", "retrieved_at"]
        st.dataframe(
            filtered_df[display_cols].rename(columns={
                "country_name": "Country",
                "country": "ISO-3",
                "source": "Source",
                "status_badge": "Outcome Status",
                "records": "Observed Records",
                "message": "Detail / Verification Notes",
                "retrieved_at": "Timestamp (UTC)",
            }),
            use_container_width=True,
            hide_index=True,
        )
        st.info(f"📁 Raw source-native datasets are stored unmutated under `{st.session_state.output_root}/raw`")
    else:
        st.subheader("Source Plan for Current Mode")
        active_sources = [s for s in SOURCES if s.mode == mode]
        st.dataframe(
            pd.DataFrame([{
                "Source": s.name,
                "Frequency": s.frequency,
                "Concept / Domain": s.indicator,
                "Access Model": s.access,
                "Description": s.description,
            } for s in active_sources]),
            use_container_width=True,
            hide_index=True,
        )
        st.info("👈 Choose candidate countries in the sidebar and press **Run Acquisition & Build Inventory**.")

# ============================================================================
# Tab 2: Availability Audit
# ============================================================================
with tabs[1]:
    st.subheader("🧭 HGT-QF Global Data Availability Audit")
    st.markdown(
        "Deterministic **country × feature × source × period** audit computed from the "
        "registries — **no downloads and no API calls**. Run this before bulk acquisition."
    )

    def _parse_audit_countries() -> list[str]:
        codes: list[str] = []
        for line in country_text.replace(",", "\n").splitlines():
            line_clean = line.strip()
            if line_clean:
                iso3 = normalize_country(line_clean)
                if iso3 and iso3 not in codes:
                    codes.append(iso3)
        return codes

    audit_countries = _parse_audit_countries()
    if not audit_countries:
        from country_registry import get_all_countries
        audit_countries = [r.iso3 for r in get_all_countries()]

    col_a, col_b = st.columns([1, 2])
    with col_a:
        top_n = st.number_input("Recommended countries", min_value=1, max_value=50, value=20)
        run_audit_btn = st.button("🧭 Run Availability Audit", type="primary", use_container_width=True)
    with col_b:
        st.caption(
            f"Auditing {len(audit_countries)} countries ({int(start_year)}–{int(end_year)}). "
            "🔑 ACCESS_REQUIRES_AUTH = data exists but a credential is missing."
        )

    if run_audit_btn:
        with st.spinner("Running deterministic coverage audit…"):
            audit = run_availability_audit(
                countries=audit_countries,
                start_year=int(start_year),
                end_year=int(end_year),
                output_dir=str(Path.cwd() / "hgt_qf_audit"),
                top_n=int(top_n),
            )
        st.session_state.audit_report = render_audit_report(audit)
        st.session_state.audit_result = audit
        st.download_button(
            "📥 Download audit JSON",
            data=json.dumps(audit, indent=2, default=str),
            file_name="availability_audit.json",
            mime="application/json",
        )

    if st.session_state.get("audit_report"):
        st.code(st.session_state.audit_report, language=None)
        detail_csv = Path.cwd() / "hgt_qf_audit" / "metadata" / "feature_coverage_detail.csv"
        if detail_csv.exists():
            with st.expander("📊 Full country × feature coverage detail", expanded=False):
                st.dataframe(pd.read_csv(detail_csv), use_container_width=True, hide_index=True)

# ============================================================================
# Tab 3: Source Area Mappings
# ============================================================================
with tabs[2]:
    st.subheader("🗺️ Verified Source-Specific Geographic Area Mappings")
    st.markdown("Maps canonical ISO-3 country codes to provider-specific identifiers (ENTSO-E EIC codes, EIA balancing authorities, AEMO region codes).")

    mappings = load_source_area_mappings()
    if mappings:
        df_map = pd.DataFrame([m.__dict__ for m in mappings])
        st.dataframe(
            df_map[[
                "iso3", "country_name", "source", "source_area_code",
                "source_area_name", "mapping_type", "mapping_source",
                "verified", "verification_date", "notes"
            ]].rename(columns={
                "iso3": "ISO-3",
                "country_name": "Country",
                "source": "Provider",
                "source_area_code": "Source Area Identifier",
                "source_area_name": "Area Description",
                "mapping_type": "Mapping Type",
                "mapping_source": "Authority",
                "verified": "Verified",
                "verification_date": "Verification Date",
                "notes": "Notes",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.warning("No mappings found in config/source_area_mapping.csv.")

# ============================================================================
# Tab 4: Country Coverage Inventory
# ============================================================================
with tabs[3]:
    st.subheader("🌐 Country-Level Research Data Inventory")
    st.markdown("Availability matrix mapping candidate countries against HGT-QF feature domains.")

    out_dir = Path(st.session_state.output_root)
    country_inv_csv = out_dir / "quality" / "country_coverage_inventory.csv"
    country_inv_json = out_dir / "quality" / "country_coverage_inventory.json"

    if country_inv_csv.exists():
        try:
            df_cinv = pd.read_csv(country_inv_csv)
            st.dataframe(
                df_cinv[[
                    "country_name", "iso3", "demand_status", "demand_source",
                    "demand_frequency", "demand_records", "climate_status",
                    "macroeconomics_status", "demographics_status",
                    "energy_system_status", "holidays_status"
                ]].rename(columns={
                    "country_name": "Country",
                    "iso3": "ISO-3",
                    "demand_status": "Electricity Demand",
                    "demand_source": "Demand Source",
                    "demand_frequency": "Demand Freq",
                    "demand_records": "Demand Obs",
                    "climate_status": "Climate Data",
                    "macroeconomics_status": "Macroeconomics (GDP/CPI)",
                    "demographics_status": "Demographics (Pop/Urban)",
                    "energy_system_status": "Energy System / Renewables",
                    "holidays_status": "Calendar Holidays",
                }),
                use_container_width=True,
                hide_index=True,
            )

            if country_inv_json.exists():
                st.download_button(
                    "📥 Download Country Inventory (JSON)",
                    data=country_inv_json.read_text(encoding="utf-8"),
                    file_name="country_coverage_inventory.json",
                    mime="application/json",
                )
        except Exception as exc:
            st.warning(f"Could not load country inventory: {exc}")
    else:
        st.info("Country coverage inventory will be generated automatically after acquisition.")

# ============================================================================
# Tab 5: Feature Inventory (25 Variables)
# ============================================================================
with tabs[4]:
    st.subheader("📦 HGT-QF 25-Feature Research Input Space Inventory")
    st.markdown("Authoritative specifications for all 25 conceptual variables defined in the HGT-QF research design.")

    out_dir = Path(st.session_state.output_root)
    feat_inv_csv = out_dir / "quality" / "feature_inventory.csv"

    if feat_inv_csv.exists():
        df_finv = pd.read_csv(feat_inv_csv)
    else:
        from inventory_engine import CONCEPTUAL_FEATURES
        df_finv = pd.DataFrame(CONCEPTUAL_FEATURES)

    st.dataframe(
        df_finv[[
            "feature_id", "concept", "feature_name", "domain",
            "source", "source_variable", "native_frequency", "unit",
            "public_access", "is_target", "definition"
        ]].rename(columns={
            "feature_id": "ID",
            "concept": "Concept",
            "feature_name": "Variable Name",
            "domain": "Research Domain",
            "source": "Authoritative Source",
            "source_variable": "Source Variable / Code",
            "native_frequency": "Native Frequency",
            "unit": "Unit",
            "public_access": "Public Access",
            "is_target": "Is Target?",
            "definition": "Definition",
        }),
        use_container_width=True,
        hide_index=True,
    )

# ============================================================================
# Tab 6: Historical Coverage & L=120 Feasibility
# ============================================================================
with tabs[5]:
    st.subheader("⏳ Historical Coverage Depth & Lookback Feasibility ($L=120, H=12,36,60$)")
    st.markdown("Evaluates historical time horizons of electricity demand data to determine candidate suitability for sequence modeling.")

    out_dir = Path(st.session_state.output_root)
    hist_csv = out_dir / "quality" / "historical_coverage_report.csv"
    hist_json = out_dir / "quality" / "historical_coverage_report.json"

    if hist_csv.exists() and hist_json.exists():
        try:
            df_h = pd.read_csv(hist_csv)
            h_data = json.loads(hist_json.read_text(encoding="utf-8"))
            stats = h_data.get("summary_statistics", {})

            h1, h2, h3, h4, h5 = st.columns(5)
            h1.metric("≥5 Years Demand", stats.get("countries_with_gte_5y_demand", 0))
            h2.metric("≥10 Years Demand", stats.get("countries_with_gte_10y_demand", 0))
            h3.metric("≥15 Years Demand", stats.get("countries_with_gte_15y_demand", 0))
            h4.metric("Eligible L=120, H=12", stats.get("countries_eligible_for_L120_H12", 0), help="Requires >=11 years demand history")
            h5.metric("Eligible L=120, H=60", stats.get("countries_eligible_for_L120_H60", 0), help="Requires >=15 years demand history")

            st.write("---")
            st.dataframe(
                df_h[[
                    "country_name", "iso3", "source", "start_date",
                    "end_date", "historical_span_years", "historical_span_months",
                    "total_observations", "hgt_qf_L120_H12_eligible", "hgt_qf_L120_H60_eligible"
                ]].rename(columns={
                    "country_name": "Country",
                    "iso3": "ISO-3",
                    "source": "Source",
                    "start_date": "Earliest Date",
                    "end_date": "Latest Date",
                    "historical_span_years": "History (Years)",
                    "historical_span_months": "History (Months)",
                    "total_observations": "Total Obs",
                    "hgt_qf_L120_H12_eligible": "Eligible (L=120, H=12)",
                    "hgt_qf_L120_H60_eligible": "Eligible (L=120, H=60)",
                }),
                use_container_width=True,
                hide_index=True,
            )
        except Exception as exc:
            st.warning(f"Could not render historical coverage report: {exc}")
    else:
        st.info("Historical coverage analysis will appear here after electricity demand acquisition.")

# ============================================================================
# Tab 7: Dataset Manifests & Hashes
# ============================================================================
with tabs[6]:
    st.subheader("📜 Dataset Manifest & SHA-256 Cryptographic Audit")
    st.markdown("Reproducibility manifest recording SHA-256 hashes, file locations, request parameters, and sentinel detection.")

    out_dir = Path(st.session_state.output_root)
    manifest_json_path = out_dir / "quality" / "dataset_manifest.json"
    manifest_csv_path = out_dir / "quality" / "dataset_manifest.csv"

    if manifest_json_path.exists():
        try:
            m_data = json.loads(manifest_json_path.read_text(encoding="utf-8"))
            st.write(f"**Total Tracked Raw Datasets:** {m_data.get('total_datasets_tracked', 0)}")

            if manifest_csv_path.exists():
                df_m = pd.read_csv(manifest_csv_path)
                st.dataframe(
                    df_m[[
                        "dataset_id", "country", "iso3", "source", "concept",
                        "frequency", "record_count", "sha256", "license", "sentinel_values_detected"
                    ]].rename(columns={
                        "dataset_id": "Dataset ID",
                        "country": "Country",
                        "iso3": "ISO-3",
                        "source": "Provider",
                        "concept": "Concept",
                        "frequency": "Frequency",
                        "record_count": "Records",
                        "sha256": "SHA-256 Checksum",
                        "license": "License",
                        "sentinel_values_detected": "Sentinels Detected",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

            st.download_button(
                "📥 Download Dataset Manifest (JSON)",
                data=json.dumps(m_data, indent=2),
                file_name="dataset_manifest.json",
                mime="application/json",
            )
        except Exception as exc:
            st.warning(f"Could not load dataset manifest: {exc}")
    else:
        st.info("Dataset manifest will be compiled upon acquisition.")

# ============================================================================
# Tab 8: Source Registry & Licenses
# ============================================================================
with tabs[7]:
    st.subheader("📚 Verified Source Registry, Documentation & Licensing Terms")

    sources_list = get_all_registered_sources()
    for s in sources_list:
        with st.expander(f"🏛️ {s.source} — {s.dataset_name}"):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Organization:** `{s.organization}`")
                st.markdown(f"**Geographic Scope:** `{s.geographic_scope}`")
                st.markdown(f"**Native Frequency:** `{s.native_frequency}`")
                st.markdown(f"**Unit:** `{s.unit}`")
                st.markdown(f"**Public Accessibility:** `{s.public_access}`")
            with c2:
                st.markdown(f"**License:** `{s.license}`")
                st.markdown(f"**Historical Coverage:** `{s.historical_start} – {s.historical_end}`")
                st.markdown(f"**Official Portal:** [{s.official_url}]({s.official_url})")
                st.markdown(f"**Documentation:** [{s.documentation_url}]({s.documentation_url})")
            st.markdown(f"**Academic Relevance:** {s.academic_relevance}")
            if s.notes:
                st.caption(f"Notes: {s.notes}")
