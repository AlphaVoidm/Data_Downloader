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
from source_registry import (
    ACQUISITION_MODES,
    get_all_registered_sources,
    get_source_capability_matrix,
)

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
    "📋 Selection & Download",
    "🚀 Acquisition & Status",
    "🧭 Availability Audit",
    "🔑 Credentials & Auth Check",
    "🧩 Source Capability Matrix",
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
if "selection_plan" not in st.session_state:
    st.session_state.selection_plan = None
if "selection_validated" not in st.session_state:
    st.session_state.selection_validated = False

# ============================================================================
# Tab 0: Selection & Download (NEW)
# ============================================================================
with tabs[0]:
    from selection_manager import (
        get_feature_groups, get_source_groups, get_available_countries,
        build_download_plan, validate_selection, render_plan_preview,
        MODE_AUTOMATIC, MODE_MANUAL,
    )
    from download_policy import (
        DownloadPolicy, validate_selection_policy, apply_download_policy,
        render_policy_report,
    )

    st.subheader("📋 Source & Feature Selection System")
    st.markdown(
        "Control exactly **what** to acquire, **from where**, and **when**. "
        "The selection manager validates your choices against the source registry "
        "before any download begins. Strict data validation prevents silent NULL datasets."
    )

    # --- Section 1: Countries ---
    st.markdown("### 1. Countries")
    col_sel1, col_sel2, col_sel3 = st.columns([2, 1, 1])

    with col_sel1:
        all_countries = get_available_countries()
        country_options = [f"{c['iso3']} — {c['name']}" for c in all_countries]

        # Quick presets
        preset_options = ["Custom"] + list(REGIONAL_PRESETS.keys())
        sel_preset = st.selectbox("Quick Preset", preset_options, key="sel_preset")

        if sel_preset != "Custom":
            preset_codes = get_preset_countries(sel_preset)
            default_idx = [i for i, c in enumerate(all_countries) if c["iso3"] in preset_codes]
        else:
            default_idx = []

        sel_countries = st.multiselect(
            "Select countries",
            options=country_options,
            default=[country_options[i] for i in default_idx[:10]] if default_idx else [],
            key="sel_countries_multi",
            help="Select one or more countries to download data for",
        )
        selected_iso3 = [c.split(" — ")[0] for c in sel_countries]

    with col_sel2:
        if st.button("Select All", key="sel_all"):
            st.session_state.sel_countries_multi = country_options
            st.rerun()
    with col_sel3:
        if st.button("Clear", key="sel_clear"):
            st.session_state.sel_countries_multi = []
            st.rerun()

    if selected_iso3:
        st.caption(f"✅ {len(selected_iso3)} countries selected: {', '.join(selected_iso3[:10])}" +
                   ("..." if len(selected_iso3) > 10 else ""))

    st.markdown("---")

    # --- Section 2: Features ---
    st.markdown("### 2. Features")
    feature_groups = get_feature_groups()

    selected_features: list[str] = []

    # Research Ready quick select
    col_fr1, col_fr2, col_fr3 = st.columns(3)
    with col_fr1:
        if st.button("Research Ready (Target + Core)", key="sel_research_ready"):
            from feature_registry import get_target_feature, get_core_exogenous
            target = get_target_feature()
            core = get_core_exogenous()
            st.session_state.sel_features_state = [target.concept] + [f.concept for f in core]
            st.rerun()
    with col_fr2:
        if st.button("Core Climate Only", key="sel_climate"):
            st.session_state.sel_features_state = [
                "electricity_demand", "temperature_2m", "solar_radiation",
                "wind_speed_10m", "precipitation"
            ]
            st.rerun()
    with col_fr3:
        if st.button("Clear Features", key="sel_feat_clear"):
            st.session_state.sel_features_state = []
            st.rerun()

    for group_key, group_info in feature_groups.items():
        with st.expander(f"**{group_info['label']}** — {group_info['description']}", expanded=(group_key == "TARGET")):
            for feat in group_info["features"]:
                default_checked = feat["concept"] in st.session_state.get("sel_features_state", [])
                # Always include target by default
                if feat.get("is_target"):
                    default_checked = True
                checked = st.checkbox(
                    f"{feat['name']} ({feat['frequency']}, {feat['unit']})",
                    value=default_checked,
                    key=f"feat_{feat['concept']}",
                    help=f"Domain: {feat['domain']} | Sources: {', '.join(feat['sources'])}",
                )
                if checked:
                    selected_features.append(feat["concept"])

    # Update session state for quick-select buttons
    if st.session_state.get("sel_features_state") is not None:
        # Re-run to apply the button selections
        pass

    st.caption(f"✅ {len(selected_features)} features selected")

    st.markdown("---")

    # --- Section 3: Source Mode ---
    st.markdown("### 3. Source Mode")
    source_mode = st.radio(
        "Source selection mode",
        [MODE_AUTOMATIC, MODE_MANUAL],
        format_func=lambda x: "🤖 Automatic (system picks best source per feature)" if x == MODE_AUTOMATIC else "✋ Manual (you pick the source for each feature)",
        horizontal=True,
        key="sel_source_mode",
    )

    source_overrides: dict[str, str] = {}
    if source_mode == MODE_MANUAL:
        st.info("In manual mode, you explicitly choose which source to use for each feature. "
                "If that source cannot provide the data, the result will be PARTIAL_SUCCESS rather than silently switching.")
        source_groups = get_source_groups()
        for domain, sources in source_groups.items():
            with st.expander(f"{domain}", expanded=False):
                for src in sources:
                    st.markdown(f"**{src['source_name']}** — {src['coverage']} | {src['frequency']} | {src['historical']}")
                    # For each feature this source provides, allow override
                    for feat_concept in src["features"]:
                        if feat_concept in selected_features:
                            override = st.checkbox(
                                f"Use {src['source_name']} for {feat_concept}",
                                key=f"override_{src['source_id']}_{feat_concept}",
                            )
                            if override:
                                source_overrides[feat_concept] = src["source_id"]

    st.markdown("---")

    # --- Section 4: Period ---
    st.markdown("### 4. Period")
    current_yr_sel = date.today().year
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        sel_start_year = st.number_input("Start Year", min_value=1950, max_value=current_yr_sel,
                                         value=2000, key="sel_start_year")
    with col_p2:
        sel_end_year = st.number_input("End Year", min_value=1950, max_value=2100,
                                       value=current_yr_sel, key="sel_end_year")

    st.markdown("---")

    # --- Section 5: Download Plan Preview ---
    st.markdown("### 5. Download Plan")

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        validate_btn = st.button("🔍 VALIDATE SELECTION", type="secondary", use_container_width=True, key="sel_validate")
    with col_btn2:
        download_btn = st.button("🚀 DOWNLOAD", type="primary", use_container_width=True, key="sel_download")

    if validate_btn or st.session_state.get("selection_plan"):
        if validate_btn or not st.session_state.get("selection_plan"):
            # Build and validate the plan
            creds = {}
            for k, v in entered_credentials.items():
                if v:
                    creds[k] = v
            # Also check env
            for env_key in ("EIA_API_KEY", "ENTSOE_API_TOKEN", "EMBER_API_KEY", "CDS_API_KEY"):
                env_val = os.getenv(env_key, "")
                if env_val and env_key not in creds:
                    creds[env_key] = env_val

            result = validate_selection(
                countries=selected_iso3,
                features=selected_features,
                start_year=int(sel_start_year),
                end_year=int(sel_end_year),
                source_mode=source_mode,
                source_overrides=source_overrides,
                credentials=creds if creds else None,
            )
            st.session_state.selection_plan = result

        plan_result = st.session_state.selection_plan
        plan = plan_result["plan"]
        summary = plan_result["summary"]

        # Summary metrics
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Countries", summary["countries"])
        m2.metric("Features", summary["features"])
        m3.metric("Sources", summary["sources"])
        m4.metric("Requests (supported)", summary["requests"])
        m5.metric("Auth issues", summary["auth_issues"])

        # Errors / Warnings
        if plan_result["errors"]:
            for err in plan_result["errors"]:
                st.error(f"❌ {err}")
        if plan_result["warnings"]:
            for warn in plan_result["warnings"]:
                st.warning(f"⚠️ {warn}")

        # Preview text
        st.markdown("#### Download Plan Preview")
        st.code(render_plan_preview(plan), language=None)

        # Detailed table
        if plan.countries:
            rows = []
            for cp in plan.countries:
                for sel in cp.selections:
                    status_icon = {
                        "SUPPORTED": "🟢", "AUTH_REQUIRED": "🔑",
                        "NOT_SUPPORTED": "⚪", "MAPPING_REQUIRED": "🔵",
                        "TEMPORARILY_UNAVAILABLE": "🟠", "UNKNOWN": "❓",
                    }.get(sel.coverage_status, "❓")
                    rows.append({
                        "Country": f"{cp.iso3} ({cp.country_name})",
                        "Feature": sel.feature_name,
                        "Source": sel.source_name or "(none)",
                        "Status": f"{status_icon} {sel.coverage_status}",
                        "Frequency": sel.frequency,
                        "Period Overlap (mo)": sel.period_overlap_months,
                        "Auth": "🔑✓" if sel.auth_satisfied else ("🔑✗ Required" if sel.auth_required else "—"),
                    })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Validation result
        if plan_result["valid"]:
            st.success("✅ Selection is valid — ready to download")
        else:
            st.error("❌ Selection has errors — fix before downloading")

    st.markdown("---")

    # --- Download Policy ---
    with st.expander("⚙️ Download Policy (validation rules)", expanded=False):
        st.markdown("Configure what happens when downloaded data fails validation checks.")
        col_dp1, col_dp2 = st.columns(2)
        with col_dp1:
            st.checkbox("Reject invalid schema", value=True, key="dp_schema", disabled=True)
            st.checkbox("Reject wrong country", value=True, key="dp_country", disabled=True)
            st.checkbox("Reject wrong date range", value=True, key="dp_date")
            st.checkbox("Reject unexpected units", value=False, key="dp_units")
            st.checkbox("Reject completely empty response", value=True, key="dp_empty", disabled=True)
        with col_dp2:
            st.checkbox("Reject malformed response", value=True, key="dp_malformed", disabled=True)
            st.checkbox("Reject duplicate records", value=False, key="dp_dup")
            st.checkbox("Warn on missing periods", value=True, key="dp_warn_missing")
            st.checkbox("Warn on partial coverage", value=True, key="dp_warn_partial")
            st.checkbox("Never silently fill missing target values", value=True, key="dp_never_fill", disabled=True)
            st.checkbox("Never silently switch source", value=True, key="dp_never_switch", disabled=True)

        policy = DownloadPolicy(
            reject_wrong_date_range=st.session_state.get("dp_date", True),
            reject_unexpected_units=st.session_state.get("dp_units", False),
            reject_duplicate_records=st.session_state.get("dp_dup", False),
            warn_missing_periods=st.session_state.get("dp_warn_missing", True),
            warn_partial_coverage=st.session_state.get("dp_warn_partial", True),
        )

    # --- Handle Download ---
    if download_btn:
        if not selected_iso3:
            st.error("❌ No countries selected")
        elif not selected_features:
            st.error("❌ No features selected")
        elif sel_end_year < sel_start_year:
            st.error("❌ End year must be ≥ start year")
        else:
            creds = {}
            for k, v in entered_credentials.items():
                if v:
                    creds[k] = v
            for env_key in ("EIA_API_KEY", "ENTSOE_API_TOKEN", "EMBER_API_KEY", "CDS_API_KEY"):
                env_val = os.getenv(env_key, "")
                if env_val and env_key not in creds:
                    creds[env_key] = env_val

            # Pre-download validation
            result = validate_selection(
                countries=selected_iso3,
                features=selected_features,
                start_year=int(sel_start_year),
                end_year=int(sel_end_year),
                source_mode=source_mode,
                source_overrides=source_overrides,
                credentials=creds if creds else None,
            )

            if not result["valid"]:
                st.error("❌ Cannot download — fix validation errors first")
                for err in result["errors"]:
                    st.error(f"  • {err}")
            else:
                # Check policy
                policy_result = validate_selection_policy(result["summary"], policy)
                if not policy_result.proceed:
                    st.error("❌ Download policy blocked:")
                    for err in policy_result.errors:
                        st.error(f"  • {err}")
                else:
                    if policy_result.warnings:
                        for w in policy_result.warnings:
                            st.warning(f"⚠️ {w}")

                    # Determine execution mode based on features
                    exec_mode = "long-term"  # default for multi-feature

                    # Run the pipeline
                    status_box = st.status("Downloading with strict validation...", expanded=True)

                    def progress_cb(msg):
                        status_box.write(f"📥 {msg}")

                    results = run_pipeline(
                        countries=selected_iso3,
                        mode=exec_mode,
                        raw_dir=raw_dir,
                        start=int(sel_start_year),
                        end=int(sel_end_year),
                        progress=progress_cb,
                        credentials=creds if creds else None,
                    )

                    status_box.update(
                        label="✅ Download complete with validation!",
                        state="complete",
                        expanded=False,
                    )

                    st.session_state.pipeline_results = results
                    st.session_state.run_mode = exec_mode
                    st.session_state.output_root = raw_dir
                    st.session_state.countries = selected_iso3
                    st.session_state.start_yr = int(sel_start_year)
                    st.session_state.end_yr = int(sel_end_year)

                    # Show results summary
                    st.success(f"✅ Downloaded {len(results)} datasets for {len(selected_iso3)} countries")
                    ok_count = sum(1 for r in results if r.status == "SUCCESS")
                    partial_count = sum(1 for r in results if r.status == "PARTIAL_SUCCESS")
                    fail_count = sum(1 for r in results if r.status not in ("SUCCESS", "PARTIAL_SUCCESS"))
                    c1, c2, c3 = st.columns(3)
                    c1.metric("🟢 SUCCESS", ok_count)
                    c2.metric("🟡 PARTIAL", partial_count)
                    c3.metric("🔴 Other", fail_count)

                    st.info(f"📁 Data stored in `{raw_dir}`. Switch to the **🚀 Acquisition & Status** tab for details.")



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
        with tabs[1]:
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
with tabs[1]:
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

        # Actionable per-item diagnosis — never a vague "request failed".
        _ACTION_HINTS = {
            "ACCESS_RESTRICTED": "Supply/rotate the credential (see 🔑 Credentials & Auth Check).",
            "AUTH_FAILED": "Credential rejected — rotate the key/token.",
            "MAPPING_MISSING": "Add the provider area code to config/source_area_mapping.csv.",
            "SOURCE_NOT_COVERED": "Source does not publish this country — skip, not a failure.",
            "NO_DATA_AVAILABLE": "HTTP 200 with zero records — no data for this combo, not a failure.",
            "API_ERROR": "Endpoint returned an error/rate-limit — retry later.",
            "DOWNLOAD_ERROR": "Network/download problem — retry (retry/backoff applied upstream).",
            "INVALID_RESPONSE": "HTTP 200 but not real data (portal/HTML) — check endpoint config.",
        }
        problem_rows = df_res[df_res["status"] != "SUCCESS"]
        if not problem_rows.empty:
            with st.expander("🔍 Per-source diagnosis (non-success items)", expanded=True):
                for _, row in problem_rows.iterrows():
                    hint = _ACTION_HINTS.get(row["status"], "Review the detail message.")
                    st.markdown(
                        f"**{row['source']}** · {row['country_name']} · `{row['status']}`\n\n"
                        f"{row['message']}\n\n→ **Action:** {hint}"
                    )
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
with tabs[2]:
    st.subheader("🧭 HGT-QF Global Data Availability Audit")
    st.markdown(
        "Deterministic **country × feature × source × period** audit computed from the "
        "registries — **no downloads and no API calls**. Three independent concepts: "
        "**TARGET_READY** (electricity demand only), **FEATURE_COVERAGE** "
        "(core / extended / optional), and **RESEARCH_READY** (target + configurable "
        "core-coverage threshold). Optional features — prices, EV, AC/heat-pump, "
        "sectoral demand, holidays — never disqualify a country."
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

    from research_config import build_research_config

    col_a, col_b = st.columns([1, 1])
    with col_a:
        max_per_region = st.number_input("Max countries per region", min_value=1, max_value=50, value=6)
    with col_b:
        min_demand_history = st.number_input(
            "Min consecutive demand months (TARGET rule)",
            min_value=12, max_value=600, value=120, step=12,
        )
    col_c, col_d = st.columns([1, 1])
    with col_c:
        min_core_coverage = st.slider("Min core feature coverage", 0.0, 1.0, 0.8, 0.05,
                                      format="%.0f%%")
    with col_d:
        require_optional = st.checkbox("Require ALL optional features", value=False)
    run_audit_btn = st.button("🧭 Run Availability Audit", type="primary", use_container_width=True)

    st.caption(
        f"Auditing {len(audit_countries)} countries ({int(start_year)}–{int(end_year)}). "
        "Discovery-only: no downloads, no API calls. "
        "🔑 AUTH_REQUIRED = data exists but a credential is missing."
    )

    if run_audit_btn:
        config = build_research_config(
            min_history_months=int(min_demand_history),
            min_consecutive_months=int(min_demand_history),
            min_core_coverage=float(min_core_coverage),
            require_optional_features=require_optional,
        )
        with st.spinner("Running deterministic coverage + readiness audit…"):
            audit = run_availability_audit(
                countries=audit_countries,
                start_year=int(start_year),
                end_year=int(end_year),
                output_dir=str(Path.cwd() / "hgt_qf_audit"),
                max_per_region=int(max_per_region),
                config=config,
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
        report_c = Path.cwd() / "hgt_qf_audit" / "metadata" / "report_C_readiness.csv"
        report_a = Path.cwd() / "hgt_qf_audit" / "metadata" / "report_A_source_coverage.csv"
        if report_c.exists():
            with st.expander("📊 REPORT C — three-tier readiness (TARGET / FEATURE / RESEARCH)", expanded=False):
                st.dataframe(pd.read_csv(report_c), use_container_width=True, hide_index=True)
        if report_a.exists():
            with st.expander("📊 REPORT A — source coverage + provenance registry (country × feature)", expanded=False):
                st.dataframe(pd.read_csv(report_a), use_container_width=True, hide_index=True)

# ============================================================================
# Tab 3: Credentials & Auth Check
# ============================================================================
with tabs[3]:
    st.subheader("🔑 Credential Propagation & Source Auth Check")
    st.markdown(
        "Tests each credential-protected source with a **tiny** request **before** any "
        "25-year download. Credentials flow: **sidebar input → credential manager → "
        "connector → API**. Real keys are never displayed, logged, or written to reports."
    )

    from credential_manager import load_credentials as _load_creds, masked as _masked, is_supplied as _supplied
    from auth_check import run_auth_checks as _run_auth_checks

    norm_creds = _load_creds(entered_credentials)

    col_btn, col_note = st.columns([1, 2])
    with col_btn:
        run_auth_btn = st.button("🔑 Run Auth Check", type="primary", use_container_width=True)
    with col_note:
        st.caption(
            "In this sandbox outbound network is blocked, so live probes will report "
            "ENDPOINT_UNAVAILABLE / NETWORK_ERROR. Locally they perform the real "
            "Ember / ENTSO-E / EIA probes and the CDS client check."
        )

    # Per-source credential status (masked).
    with st.expander("Credential status (masked)", expanded=True):
        rows = []
        for src_id in ("ember", "entsoe", "eia", "era5"):
            from credential_manager import format_ok as _fmt_ok
            ok, note = _fmt_ok(src_id, norm_creds)
            rows.append({
                "Source": src_id.upper(),
                "Supplied": "✓" if _supplied(src_id, norm_creds) else "—",
                "Format": "VALID" if ok else ("INVALID" if _supplied(src_id, norm_creds) else "—"),
                "Note": note,
                "Masked": _masked(src_id, norm_creds) or "—",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if run_auth_btn:
        with st.spinner("Running tiny per-source auth probes…"):
            auth_results = _run_auth_checks(norm_creds)
        st.session_state.auth_check_results = auth_results
    else:
        auth_results = st.session_state.get("auth_check_results")

    if auth_results:
        for r in auth_results:
            icon = "✅" if r.endpoint_available else ("🔑" if r.status in ("AUTH_FAILED", "CONFIGURATION_ERROR") else "⚠️")
            with st.container(border=True):
                st.markdown(f"**{icon} {r.source_name}**  (`{r.source_id}`)")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Credential supplied", "YES" if r.credential_supplied else "NO")
                c2.metric("Credential format", "VALID" if r.credential_format_ok else "INVALID")
                c3.metric("API response", f"HTTP {r.http_status}" if r.http_status is not None else "—")
                c4.metric("Result", r.status)
                if r.endpoint_available:
                    st.success(f"Endpoint available — {r.message}")
                elif r.status in ("AUTH_FAILED", "CONFIGURATION_ERROR"):
                    st.error(f"{r.message} — Action: fix credential propagation (supply/rotate the key).")
                else:
                    st.warning(r.message)
                st.caption(f"Masked key: {r.masked_credential or '(none)'}")

# ============================================================================
# Tab 4: Source Capability Matrix
# ============================================================================
with tabs[4]:
    st.subheader("🧩 Source Capability Matrix")
    st.markdown(
        "How each source is **supposed** to be acquired — mode, role, coverage, auth, "
        "resolution, and expected response. Three acquisition modes: "
        "**API/country query**, **bulk/targeted job**, and **restricted**."
    )
    df_matrix = pd.DataFrame(get_source_capability_matrix())
    st.dataframe(
        df_matrix[[
            "source", "role", "acquisition_mode", "country_coverage",
            "temporal_resolution", "authentication", "historical_coverage",
            "rate_limit", "expected_response",
        ]].rename(columns={
            "source": "Source",
            "role": "Role",
            "acquisition_mode": "Acquisition Mode",
            "country_coverage": "Coverage",
            "temporal_resolution": "Temporal",
            "authentication": "Auth",
            "historical_coverage": "History",
            "rate_limit": "Rate Limit",
            "expected_response": "Expected Response",
        }),
        use_container_width=True,
        hide_index=True,
    )
    st.markdown("**Acquisition modes:**")
    for mode, label in ACQUISITION_MODES.items():
        st.markdown(f"- `{mode}` — {label}")
    st.info(
        "CMIP6 is registered as `future_scenario` only — it is **never** part of the "
        "historical 2000–2024 pipeline (NASA POWER primary → ERA5 fallback)."
    )

# ============================================================================
# Tab 5: Source Area Mappings
# ============================================================================
with tabs[5]:
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
# Tab 6: Country Coverage Inventory
# ============================================================================
with tabs[6]:
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
# Tab 7: Feature Inventory (25 Variables)
# ============================================================================
with tabs[7]:
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
# Tab 8: Historical Coverage & L=120 Feasibility
# ============================================================================
with tabs[8]:
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
# Tab 9: Dataset Manifests & Hashes
# ============================================================================
with tabs[9]:
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
# Tab 10: Source Registry & Licenses
# ============================================================================
with tabs[10]:
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
