"""
Eco Decarb TEA Tool
Streamlit-based UI for configuring and running the green hydrogen / ammonia TEA model.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
INPUTS_PY = BASE_DIR / "inputs.py"
CASES_PY = BASE_DIR / "cases.py"
OUTPUTS_JSON = BASE_DIR / "outputs.json"
SUMMARY_DIR = BASE_DIR / "Summary_output"

# Ensure project dir is on sys.path so location_profiles can be imported
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Streamlit page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Eco Decarb TEA Tool",
    page_icon="H",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "location_fetched": False,
    "location_result": None,
    "model_running": False,
    "model_outputs": None,
    # Auto-populated values (overridable)
    "auto_discount_rate": 0.07,
    "auto_tax": 0.15,
    "auto_depreciation": 15,
    "auto_inflation": 0.015,
    "auto_water_cost": 0.8,
    "auto_land_cost": 2.0,
    "auto_green_nh3": 650.0,
    "auto_grey_nh3": 280.0,
    "auto_green_h2": 3500.0,
    "auto_grey_h2": 800.0,
    "auto_country_code": "",
    "auto_state_code": "",
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ---------------------------------------------------------------------------
# Helper: patch inputs.py variable
# ---------------------------------------------------------------------------
def patch_inputs_variable(var_name: str, new_value, comment: str = None):
    """Regex-replace a variable assignment in inputs.py."""
    text = INPUTS_PY.read_text()
    pattern = re.compile(
        rf'^({re.escape(var_name)}\s*=\s*)([^\n#]+)(#[^\n]*)?$',
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return False
    if isinstance(new_value, str):
        new_val_str = f'"{new_value}" '
    elif isinstance(new_value, float):
        new_val_str = f"{new_value} "
    else:
        new_val_str = f"{new_value} "
    cmt = f"#{comment}" if comment else (m.group(3) or "")
    replacement = f"{m.group(1)}{new_val_str}{cmt}"
    text = text[:m.start()] + replacement + text[m.end():]
    INPUTS_PY.write_text(text)
    return True


# ---------------------------------------------------------------------------
# Helper: patch cases.py CASES dict literal values
# ---------------------------------------------------------------------------
def patch_cases_value(key: str, new_value):
    """Replace a literal value in the CASES list inside cases.py."""
    text = CASES_PY.read_text()
    # Match "key": <number or variable>, at the start of the value
    pattern = re.compile(
        rf'("{re.escape(key)}"\s*:\s*)([^,\n]+)',
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return False
    if isinstance(new_value, float):
        val_str = str(new_value)
    else:
        val_str = str(new_value)
    text = text[:m.start()] + f'{m.group(1)}{val_str}' + text[m.end():]
    CASES_PY.write_text(text)
    return True


# ---------------------------------------------------------------------------
# Helper: load JSON database (cached)
# ---------------------------------------------------------------------------
@st.cache_data
def load_json_db(filename: str) -> dict:
    path = BASE_DIR / filename
    if path.exists():
        return json.loads(path.read_text())
    return {}


# ---------------------------------------------------------------------------
# Helper: run the model as subprocess
# ---------------------------------------------------------------------------
def run_model():
    """Run SHARE_Model_main_v1.py and return (success, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "SHARE_Model_main_v1.py")],
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        timeout=600,
    )
    return result.returncode == 0, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Helper: fetch location data
# ---------------------------------------------------------------------------
def fetch_location(location_str, lat, lon, use_latlon, state_code,
                   pv_mw, wind_mw, elec_mode, selling_market):
    """Call location_profiles.update_profiles() and return the result dict."""
    from location_profiles import update_profiles

    kwargs = dict(
        year=2019,
        wind_capacity_mw=wind_mw,
        pv_capacity_mw=pv_mw,
        output_dir=str(BASE_DIR),
        elec_price_mode=elec_mode,
        selling_market=selling_market,
    )
    if use_latlon:
        kwargs["lat"] = lat
        kwargs["lon"] = lon
    else:
        kwargs["location"] = location_str

    if state_code:
        kwargs["state_code"] = state_code

    return update_profiles(**kwargs)


# ===================================================================
# DASHBOARD UI
# ===================================================================

st.title("Eco Decarb - TEA Tool")
st.caption("Green Hydrogen & Ammonia Techno-Economic Analysis")

# -------------------------------------------------------------------
# Section 1: Location & Region
# -------------------------------------------------------------------
st.header("1. Location & Region")

col_loc1, col_loc2 = st.columns([2, 1])

with col_loc1:
    use_latlon = st.checkbox("Use latitude/longitude instead of city name", value=False)
    if use_latlon:
        sub1, sub2 = st.columns(2)
        with sub1:
            lat_input = st.number_input("Latitude", value=23.0, format="%.4f")
        with sub2:
            lon_input = st.number_input("Longitude", value=57.0, format="%.4f")
        location_input = ""
    else:
        location_input = st.text_input("Location", value="Duqm, Oman",
                                       placeholder="e.g. Duqm, Oman")
        lat_input = 0.0
        lon_input = 0.0

with col_loc2:
    state_code_input = st.text_input("US State Code (if applicable)", value="",
                                     placeholder="e.g. TX, CA")
    selling_market_loc = st.selectbox("Market orientation", ["export", "domestic"],
                                     index=0, key="selling_market_sel")

# Quick capacity inputs needed for profile fetching
col_cap1, col_cap2 = st.columns(2)
with col_cap1:
    pv_mw_input = st.number_input("PV Capacity (MW AC)", value=400.0, min_value=0.0,
                                  step=50.0, key="pv_mw")
with col_cap2:
    wind_mw_input = st.number_input("Wind Capacity (MW)", value=400.0, min_value=0.0,
                                    step=50.0, key="wind_mw")

elec_mode_input = st.selectbox("Electricity pricing mode",
                               ["auto", "eu", "us", "flat", "ppa"],
                               index=0, key="elec_mode_sel")

fetch_btn = st.button("Fetch Location Data", type="primary")

if fetch_btn:
    with st.spinner("Fetching wind/solar profiles and location data..."):
        try:
            result = fetch_location(
                location_str=location_input,
                lat=lat_input, lon=lon_input,
                use_latlon=use_latlon,
                state_code=state_code_input,
                pv_mw=pv_mw_input, wind_mw=wind_mw_input,
                elec_mode=elec_mode_input,
                selling_market=selling_market_loc,
            )
            st.session_state.location_fetched = True
            st.session_state.location_result = result

            # Extract auto-populated values
            if result.get("tax_info"):
                ti = result["tax_info"]
                st.session_state.auto_discount_rate = ti.get("discount_rate", 0.07)
                st.session_state.auto_tax = ti.get("combined_corporate_tax", 0.15)
                st.session_state.auto_depreciation = ti.get("depreciation_years", 15)
                st.session_state.auto_inflation = ti.get("inflation_rate", 0.015)
            if result.get("water_cost_usd_m3") is not None:
                st.session_state.auto_water_cost = result["water_cost_usd_m3"]
            if result.get("land_cost_usd_acre_yr") is not None:
                st.session_state.auto_land_cost = result["land_cost_usd_acre_yr"]
            if result.get("selling_prices"):
                sp = result["selling_prices"]
                st.session_state.auto_green_nh3 = float(sp.get("green_nh3_usd_t", 650))
                st.session_state.auto_grey_nh3 = float(sp.get("grey_nh3_usd_t", 280))
                st.session_state.auto_green_h2 = float(sp.get("green_h2_usd_t", 3500))
                st.session_state.auto_grey_h2 = float(sp.get("grey_h2_usd_t", 800))
            st.session_state.auto_country_code = result.get("country_code", "")
            st.session_state.auto_state_code = state_code_input

            loc_local = result.get('location', 'unknown')
            loc_en = result.get('location_en', '')
            if loc_en and loc_en != loc_local:
                st.success(f"Location data fetched: {loc_local}  ({loc_en})")
            else:
                st.success(f"Location data fetched: {loc_local}")
        except Exception as e:
            st.error(f"Error fetching location data: {e}")

# Show location results if available
if st.session_state.location_fetched and st.session_state.location_result:
    lr = st.session_state.location_result
    st.subheader("Location Summary")
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Latitude", f"{lr.get('lat', 0):.2f}")
    mc2.metric("Longitude", f"{lr.get('lon', 0):.2f}")
    mc3.metric("Wind CF", f"{lr.get('wind_mean_cf', 0):.1%}")
    mc4.metric("Solar CF", f"{lr.get('pv_mean_cf', 0):.1%}")

    loc_display = lr.get('location', '') or ''
    loc_en_display = lr.get('location_en', '') or ''
    if loc_en_display and loc_en_display != loc_display:
        location_label = f"{loc_display}  ({loc_en_display})"
    else:
        location_label = loc_display or lr.get('country_code', '?')

    st.info(
        f"**Location:** {location_label} | "
        f"**Country:** {lr.get('country_code', '?')} | "
        f"**WACC:** {st.session_state.auto_discount_rate:.1%} | "
        f"**Tax:** {st.session_state.auto_tax:.1%} | "
        f"**Water:** ${st.session_state.auto_water_cost:.2f}/m3 | "
        f"**Land:** ${st.session_state.auto_land_cost:.1f}/acre/yr | "
        f"**Green NH3:** ${st.session_state.auto_green_nh3:.0f}/t | "
        f"**Grey NH3:** ${st.session_state.auto_grey_nh3:.0f}/t | "
        f"**Green H2:** ${st.session_state.auto_green_h2:.0f}/t | "
        f"**Grey H2:** ${st.session_state.auto_grey_h2:.0f}/t"
    )

st.divider()

# -------------------------------------------------------------------
# Section 2: Plant Configuration
# -------------------------------------------------------------------
st.header("2. Plant Configuration")

final_product = st.selectbox(
    "Final Product",
    ["Ammonia (NH3)", "Hydrogen (H2)", "Hydrogen + Ammonia (H2+NH3)"],
    index=0,
    help=(
        "**Ammonia:** Full plant with Haber-Bosch — all H2 converted to NH3. "
        "**Hydrogen:** H2 production only — no HB plant or NH3 storage costs. "
        "**H2+NH3:** Both products — revenue from H2 and NH3 sales."
    ),
)
# Map display label to internal code
_product_map = {
    "Ammonia (NH3)": "NH3",
    "Hydrogen (H2)": "H2",
    "Hydrogen + Ammonia (H2+NH3)": "H2+NH3",
}
final_product_code = _product_map[final_product]

col_p1, col_p2, col_p3 = st.columns(3)
with col_p1:
    elec_tech = st.selectbox("Electrolyzer Technology", ["Alkaline", "PEM"], index=0)
    elec_capacity = st.number_input("Electrolyzer Capacity (MW)", value=400.0,
                                    min_value=10.0, step=50.0)
with col_p2:
    if final_product_code == "NH3":
        st.info("NH3 Plant Ratio locked to **1.0** (all H2 → NH3)")
        nh3_ratio = 1.0
    elif final_product_code == "H2":
        st.info("NH3 Plant Ratio **N/A** in H2-only mode (HB costs excluded)")
        nh3_ratio = 0.01  # small non-zero to keep simulation numerically stable
    else:
        nh3_ratio = st.slider("NH3 Plant Capacity Ratio", min_value=0.3, max_value=1.0,
                              value=0.8, step=0.05,
                              help="Sizes the Haber-Bosch plant relative to the electrolyser. "
                                   "1.0 = all H2 to NH3; lower values leave surplus H2 for sale.")
with col_p3:
    include_bess = st.checkbox("Include Battery Storage", value=False)
    if include_bess:
        bess_power = st.number_input("BESS Power (MW)", value=50.0, min_value=0.0, step=10.0)
        bess_duration = st.number_input("BESS Duration (hours)", value=4.0,
                                        min_value=0.0, step=1.0)
    else:
        bess_power = 0.0
        bess_duration = 0.0

col_g1, col_g2 = st.columns(2)
with col_g1:
    include_grid_import = st.checkbox("Grid Import", value=False)
    if include_grid_import:
        grid_import_mw = st.number_input("Grid Import Capacity (MW)", value=100.0,
                                         min_value=0.0, step=10.0)
    else:
        grid_import_mw = 0.0
with col_g2:
    include_grid_export = st.checkbox("Grid Export", value=False)
    if include_grid_export:
        grid_export_mw = st.number_input("Grid Export Capacity (MW)", value=40.0,
                                         min_value=0.0, step=10.0)
    else:
        grid_export_mw = 0.0

st.divider()

# -------------------------------------------------------------------
# Section 3: Electricity Pricing
# -------------------------------------------------------------------
st.header("3. Electricity Pricing")

col_e1, col_e2, col_e3 = st.columns(3)
with col_e1:
    if elec_mode_input == "flat":
        flat_tariff = st.number_input("Flat Tariff (USD/MWh)", value=50.0,
                                      min_value=0.0, step=5.0)
    elif elec_mode_input == "ppa":
        ppa_tech = st.selectbox("PPA Technology", ["blend", "solar", "wind"])
    else:
        st.write(f"Mode: **{elec_mode_input}** (set in Section 1)")
with col_e2:
    escalation_rate = st.number_input("Annual Escalation Rate (%)", value=2.0,
                                      min_value=0.0, max_value=10.0, step=0.5) / 100.0
with col_e3:
    export_revenue = st.number_input("Export Revenue (USD/MWh)", value=0.0,
                                     min_value=0.0, step=5.0)

st.divider()

# -------------------------------------------------------------------
# Section 4: Financial Parameters
# -------------------------------------------------------------------
st.header("4. Financial Parameters")

col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    discount_rate = st.number_input("Discount Rate / WACC (%)",
                                    value=float(st.session_state.auto_discount_rate * 100),
                                    min_value=0.0, max_value=20.0, step=0.5) / 100.0
    project_lifetime = st.number_input("Project Lifetime (years)", value=30,
                                       min_value=10, max_value=50, step=5)
with col_f2:
    project_overhead = st.number_input("Project Overhead (%)", value=9.2,
                                       min_value=0.0, max_value=30.0, step=0.5) / 100.0
    tax_rate = st.number_input("Combined Tax Rate (%)",
                               value=float(st.session_state.auto_tax * 100),
                               min_value=0.0, max_value=50.0, step=0.5) / 100.0
with col_f3:
    depreciation_yrs = st.number_input("Depreciation Period (years)",
                                       value=int(st.session_state.auto_depreciation),
                                       min_value=5, max_value=40, step=1)
    inflation_rate = st.number_input("Inflation Rate (%)",
                                     value=float(st.session_state.auto_inflation * 100),
                                     min_value=0.0, max_value=15.0, step=0.1) / 100.0

st.divider()

# -------------------------------------------------------------------
# Section 5: Cost Overrides
# -------------------------------------------------------------------
st.header("5. Location Costs (editable)")

col_c1, col_c2 = st.columns(2)
with col_c1:
    water_cost = st.number_input("Water Cost (USD/m3)",
                                 value=float(st.session_state.auto_water_cost),
                                 min_value=0.0, step=0.1, format="%.2f")
with col_c2:
    land_cost = st.number_input("Land Cost (USD/acre/yr)",
                                value=float(st.session_state.auto_land_cost),
                                min_value=0.0, step=1.0, format="%.1f")

st.subheader("Selling Prices")
if final_product_code in ("NH3", "H2+NH3"):
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        green_nh3_price = st.number_input("Green NH3 (USD/t)",
                                          value=float(st.session_state.auto_green_nh3),
                                          min_value=0.0, step=50.0, format="%.0f")
    with col_s2:
        grey_nh3_price = st.number_input("Grey NH3 (USD/t)",
                                         value=float(st.session_state.auto_grey_nh3),
                                         min_value=0.0, step=50.0, format="%.0f")
else:
    green_nh3_price = 0.0
    grey_nh3_price = 0.0

if final_product_code in ("H2", "H2+NH3"):
    col_s3, col_s4 = st.columns(2)
    with col_s3:
        green_h2_price = st.number_input("Green H2 (USD/t)",
                                         value=float(st.session_state.auto_green_h2),
                                         min_value=0.0, step=100.0, format="%.0f")
    with col_s4:
        grey_h2_price = st.number_input("Grey H2 (USD/t)",
                                        value=float(st.session_state.auto_grey_h2),
                                        min_value=0.0, step=100.0, format="%.0f")
else:
    green_h2_price = 0.0
    grey_h2_price = 0.0

st.divider()

# -------------------------------------------------------------------
# Section 6: Run Model & Results
# -------------------------------------------------------------------
st.header("6. Run Model & Results")

if not st.session_state.location_fetched:
    st.warning("Please fetch location data first (Section 1) before running the model.")

run_btn = st.button("Run Model", type="primary",
                    disabled=not st.session_state.location_fetched)

if run_btn:
    with st.spinner("Applying parameter overrides..."):
        # Patch inputs.py with user overrides
        patch_inputs_variable("discount_rate", discount_rate)
        patch_inputs_variable("project_lifetime", project_lifetime)
        patch_inputs_variable("project_overhead", project_overhead)
        patch_inputs_variable("taxes", tax_rate)
        patch_inputs_variable("depreciation", depreciation_yrs)
        patch_inputs_variable("inflation", inflation_rate)
        patch_inputs_variable("water_unit_cost_per_m3", water_cost)
        patch_inputs_variable("land_cost", land_cost)
        patch_inputs_variable("green_NH3_selling_price", int(green_nh3_price))
        patch_inputs_variable("grey_NH3_selling_price", int(grey_nh3_price))
        patch_inputs_variable("green_H2_selling_price", int(green_h2_price))
        patch_inputs_variable("grey_H2_selling_price", int(grey_h2_price))
        patch_inputs_variable("final_product", final_product_code)
        patch_inputs_variable("elec_tech", elec_tech)
        patch_inputs_variable("Electrolyzer_capacity_MW", int(elec_capacity))
        patch_inputs_variable("wind_capacity_ref", int(wind_mw_input))
        patch_inputs_variable("PV_capacity_ref_AC", int(pv_mw_input))
        patch_inputs_variable("BESS_capacity_MW", int(bess_power))
        patch_inputs_variable("BESS_duration", int(bess_duration))
        patch_inputs_variable("Ratio_NH3_Plant_capacity", nh3_ratio)
        patch_inputs_variable("export_revenue", export_revenue)

        # Patch cases.py with capacity values
        patch_cases_value("PV_capacity_MW", int(pv_mw_input))
        patch_cases_value("Wind_MW", int(wind_mw_input))
        patch_cases_value("BESS_Power_MW", int(bess_power))
        patch_cases_value("BESS_Duration", int(bess_duration))
        patch_cases_value("BESS_energy_MWh", int(bess_power * bess_duration))
        patch_cases_value("Electrolyzer_capacity_MW", int(elec_capacity))
        patch_cases_value("Ratio_NH3_Plant_capacity", nh3_ratio)
        patch_cases_value("Grid_import_capacity_MW", int(grid_import_mw))
        patch_cases_value("Grid_export_capacity_MW", int(grid_export_mw))

    with st.spinner("Running Eco Decarb model (this may take a while)..."):
        try:
            success, stdout, stderr = run_model()
            if success:
                st.success("Model run completed successfully!")
                # Load results
                if OUTPUTS_JSON.exists():
                    outputs = json.loads(OUTPUTS_JSON.read_text())
                    st.session_state.model_outputs = outputs
                else:
                    st.error("outputs.json not found after model run")
            else:
                st.error("Model run failed!")
                with st.expander("Error details"):
                    st.code(stderr or stdout)
        except subprocess.TimeoutExpired:
            st.error("Model run timed out (>10 minutes)")
        except Exception as e:
            st.error(f"Error running model: {e}")

# Show results if available
if st.session_state.model_outputs:
    out = st.session_state.model_outputs
    st.subheader("Key Results")

    # --- Metric cards ---
    r1, r2, r3, r4 = st.columns(4)
    lcoh = out.get("Total_LCOH_USD_per_kg")
    lcoa = out.get("Total_LCOA_USD_per_kg")
    lcoe = out.get("Total_LCOE_USD_per_MWh")
    irr = out.get("IRR_percent")

    result_product = out.get("final_product", "NH3")
    _product_labels = {"NH3": "Ammonia (NH3)", "H2": "Hydrogen (H2)", "H2+NH3": "Hydrogen + Ammonia"}
    st.info(f"**Final Product:** {_product_labels.get(result_product, result_product)}")

    r1.metric("LCOH", f"${lcoh:.2f}/kg" if lcoh else "N/A")
    if result_product != "H2":
        r2.metric("LCOA", f"${lcoa:.2f}/kg" if lcoa else "N/A")
    else:
        r2.metric("LCOA", "N/A (H2 mode)")
    r3.metric("LCOE", f"${lcoe:.1f}/MWh" if lcoe else "N/A")
    r4.metric("IRR", f"{irr:.1f}%" if irr else "N/A")

    r5, r6, r7, r8 = st.columns(4)
    npv = out.get("NPV_USD_M")
    capex_total = out.get("Total_CAPEX_USD_M")
    nh3_tpa = out.get("NH3_prod_TPA_tonnes_per_year")
    h2_tpa = out.get("H2_prod_TPA_tonnes_per_year")

    r5.metric("NPV", f"${npv:.1f}M" if npv else "N/A")
    r6.metric("Total CAPEX", f"${capex_total:.0f}M" if capex_total else "N/A")
    if result_product != "H2":
        r7.metric("NH3 Production", f"{nh3_tpa:,.0f} t/yr" if nh3_tpa else "N/A")
    else:
        r7.metric("NH3 Production", "N/A (H2 mode)")
    r8.metric("H2 Production", f"{h2_tpa:,.0f} t/yr" if h2_tpa else "N/A")

    # --- Gauge charts for IRR and capacity factors ---
    gauge_cols = st.columns(3)
    if irr is not None:
        fig_irr = go.Figure(go.Indicator(
            mode="gauge+number",
            value=irr * 100,
            number={"suffix": "%"},
            title={"text": "IRR"},
            gauge={
                "axis": {"range": [0, 30]},
                "bar": {"color": "#2ecc71" if irr > 0.08 else "#e74c3c"},
                "steps": [
                    {"range": [0, 8], "color": "#fadbd8"},
                    {"range": [8, 15], "color": "#fdebd0"},
                    {"range": [15, 30], "color": "#d5f5e3"},
                ],
                "threshold": {"line": {"color": "black", "width": 2}, "value": 10},
            },
        ))
        fig_irr.update_layout(height=250, margin=dict(t=40, b=0, l=30, r=30))
        gauge_cols[0].plotly_chart(fig_irr, use_container_width=True)

    lr = st.session_state.location_result
    if lr and lr.get("wind_mean_cf"):
        fig_wcf = go.Figure(go.Indicator(
            mode="gauge+number",
            value=lr["wind_mean_cf"] * 100,
            number={"suffix": "%"},
            title={"text": "Wind Capacity Factor"},
            gauge={
                "axis": {"range": [0, 60]},
                "bar": {"color": "#3498db"},
                "steps": [
                    {"range": [0, 20], "color": "#ebf5fb"},
                    {"range": [20, 35], "color": "#d6eaf8"},
                    {"range": [35, 60], "color": "#aed6f1"},
                ],
            },
        ))
        fig_wcf.update_layout(height=250, margin=dict(t=40, b=0, l=30, r=30))
        gauge_cols[1].plotly_chart(fig_wcf, use_container_width=True)

    if lr and lr.get("pv_mean_cf"):
        fig_pcf = go.Figure(go.Indicator(
            mode="gauge+number",
            value=lr["pv_mean_cf"] * 100,
            number={"suffix": "%"},
            title={"text": "Solar Capacity Factor"},
            gauge={
                "axis": {"range": [0, 40]},
                "bar": {"color": "#f39c12"},
                "steps": [
                    {"range": [0, 12], "color": "#fef9e7"},
                    {"range": [12, 22], "color": "#fdebd0"},
                    {"range": [22, 40], "color": "#f9e79f"},
                ],
            },
        ))
        fig_pcf.update_layout(height=250, margin=dict(t=40, b=0, l=30, r=30))
        gauge_cols[2].plotly_chart(fig_pcf, use_container_width=True)

    # --- CAPEX waterfall chart from summary CSV ---
    try:
        import glob
        summary_files = glob.glob(str(SUMMARY_DIR / "*Project_Summary.csv"))
        if summary_files:
            latest = max(summary_files, key=os.path.getmtime)
            summary_df = pd.read_csv(latest)
            capex_cols = [c for c in summary_df.columns if "CAPEX" in c and "Total" not in c]
            if capex_cols:
                capex_data = summary_df[capex_cols].iloc[0]
                capex_data = capex_data[capex_data > 0].sort_values(ascending=False)

                # Clean up column names for display
                labels = [c.replace("_CAPEX_kUSD", "").replace("_kUSD", "")
                          .replace("_", " ") for c in capex_data.index]
                values = (capex_data.values / 1000).tolist()  # kUSD -> MUSD

                chart_left, chart_right = st.columns(2)

                # Waterfall chart
                with chart_left:
                    st.subheader("CAPEX Breakdown (Waterfall)")
                    fig_wf = go.Figure(go.Waterfall(
                        orientation="v",
                        x=labels,
                        y=values,
                        measure=["relative"] * len(values),
                        text=[f"${v:.1f}M" for v in values],
                        textposition="outside",
                        connector={"line": {"color": "#ccc"}},
                        increasing={"marker": {"color": "#3498db"}},
                        totals={"marker": {"color": "#2c3e50"}},
                    ))
                    # Add total bar
                    fig_wf.add_trace(go.Waterfall(
                        x=["Total"],
                        y=[sum(values)],
                        measure=["total"],
                        text=[f"${sum(values):.0f}M"],
                        textposition="outside",
                        connector={"visible": False},
                        totals={"marker": {"color": "#2c3e50"}},
                    ))
                    fig_wf.update_layout(
                        showlegend=False,
                        yaxis_title="Cost ($M)",
                        height=420,
                        margin=dict(t=20, b=80),
                    )
                    fig_wf.update_xaxes(tickangle=-40)
                    st.plotly_chart(fig_wf, use_container_width=True)

                # Donut chart
                with chart_right:
                    st.subheader("CAPEX Composition")
                    fig_donut = go.Figure(go.Pie(
                        labels=labels,
                        values=values,
                        hole=0.45,
                        textinfo="label+percent",
                        textposition="outside",
                        marker={"colors": [
                            "#3498db", "#2ecc71", "#f39c12", "#e74c3c",
                            "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
                            "#16a085", "#c0392b", "#7f8c8d", "#2980b9",
                        ]},
                    ))
                    fig_donut.update_layout(
                        height=420,
                        margin=dict(t=20, b=20),
                        annotations=[{
                            "text": f"${sum(values):.0f}M",
                            "x": 0.5, "y": 0.5,
                            "font_size": 18, "showarrow": False,
                        }],
                    )
                    st.plotly_chart(fig_donut, use_container_width=True)
    except Exception:
        pass

# -------------------------------------------------------------------
# Footer
# -------------------------------------------------------------------
st.divider()
st.caption("Eco Decarb TEA Tool | By Shadi Darvish, PhD")
