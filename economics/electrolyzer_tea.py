"""
Electrolyzer Techno-Economic Analysis (TEA) Module

Detailed TEA for PEM, Alkaline, and SOEC electrolyzers matching SHARE model methodology.

Features:
- Stack-level CAPEX breakdown
- Degradation and stack replacement modeling
- Water and electricity consumption tracking
- LCOH calculation with multiple allocation methods
- Integration with renewable energy sources

References:
- SHARE Model v1 (inputs.py, SHARE_model_v1.py)
- IRENA Green Hydrogen Cost Reduction (2020)
- DOE H2A Model v3.2018
- NREL ATB 2024
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Literal
from enum import Enum
import numpy as np
import math


class ElectrolyzerTechnology(Enum):
    """Electrolyzer technology types"""
    PEM = "PEM"           # Proton Exchange Membrane
    ALKALINE = "Alkaline" # Alkaline Water Electrolysis
    SOEC = "SOEC"         # Solid Oxide Electrolysis Cell
    AEM = "AEM"           # Anion Exchange Membrane


@dataclass
class ElectrolyzerTechSpecs:
    """Technology-specific default parameters"""
    
    # PEM defaults
    PEM = {
        'specific_energy_kWh_kg': 50,      # kWh/kg H2
        'stack_lifetime_hours': 80000,
        'system_lifetime_years': 20,
        'min_load_fraction': 0.10,
        'max_load_fraction': 1.60,         # Can overload
        'ramp_rate_per_second': 0.10,      # 10%/s
        'cold_start_minutes': 5,
        'warm_start_minutes': 1,
        'operating_pressure_bar': 30,
        'operating_temp_C': 80,
        'water_consumption_L_kg': 9,
        'efficiency_LHV': 0.67,
        'capex_per_kW': 700,
        'stack_fraction_of_capex': 0.40,
    }
    
    # Alkaline defaults
    ALKALINE = {
        'specific_energy_kWh_kg': 52,
        'stack_lifetime_hours': 80000,
        'system_lifetime_years': 25,
        'min_load_fraction': 0.20,
        'max_load_fraction': 1.00,
        'ramp_rate_per_second': 0.002,     # 0.2%/s (slower)
        'cold_start_minutes': 30,
        'warm_start_minutes': 5,
        'operating_pressure_bar': 30,
        'operating_temp_C': 80,
        'water_consumption_L_kg': 9,
        'efficiency_LHV': 0.64,
        'capex_per_kW': 500,
        'stack_fraction_of_capex': 0.50,
    }
    
    # SOEC defaults
    SOEC = {
        'specific_energy_kWh_kg': 37,      # Best efficiency with heat integration
        'stack_lifetime_hours': 40000,     # Lower lifetime
        'system_lifetime_years': 20,
        'min_load_fraction': 0.30,
        'max_load_fraction': 1.00,
        'ramp_rate_per_second': 0.001,
        'cold_start_minutes': 300,         # 5 hours
        'warm_start_minutes': 60,
        'operating_pressure_bar': 1,
        'operating_temp_C': 800,
        'water_consumption_L_kg': 9,       # Steam input
        'efficiency_LHV': 0.90,            # With heat integration
        'capex_per_kW': 2000,              # Higher CAPEX
        'stack_fraction_of_capex': 0.60,
    }


@dataclass
class ElectrolyzerCapexInputs:
    """Detailed CAPEX breakdown for electrolyzer system"""
    
    # ════════════════════════════════════════════════════════════
    # STACK & CELL
    # ════════════════════════════════════════════════════════════
    
    # Stack cost ($/kW) - includes cells, MEA/electrodes, bipolar plates
    stack_cost_per_kW: float = 280
    
    # Stack balance ($/kW) - gaskets, endplates, compression hardware
    stack_balance_per_kW: float = 40
    
    # ════════════════════════════════════════════════════════════
    # POWER ELECTRONICS
    # ════════════════════════════════════════════════════════════
    
    # Rectifier/transformer ($/kW)
    rectifier_cost_per_kW: float = 80
    
    # DC-DC converter if needed ($/kW)
    dc_converter_per_kW: float = 30
    
    # Switchgear and protection ($/kW)
    switchgear_per_kW: float = 25
    
    # ════════════════════════════════════════════════════════════
    # BALANCE OF PLANT
    # ════════════════════════════════════════════════════════════
    
    # Water treatment - deionization, RO ($/kW)
    water_treatment_per_kW: float = 30
    
    # Thermal management - cooling, heat exchangers ($/kW)
    thermal_management_per_kW: float = 35
    
    # Gas processing - drying, purification ($/kW)
    gas_processing_per_kW: float = 40
    
    # Piping, valves, instrumentation ($/kW)
    piping_and_valves_per_kW: float = 25
    
    # Control system - PLC, HMI, SCADA ($/kW)
    control_system_per_kW: float = 20
    
    # Safety systems - ventilation, detection ($/kW)
    safety_systems_per_kW: float = 15
    
    # ════════════════════════════════════════════════════════════
    # H2 COMPRESSION (to storage pressure)
    # ════════════════════════════════════════════════════════════
    
    # Compressor ($/kW of electrolyzer)
    compressor_per_kW: float = 50
    
    # ════════════════════════════════════════════════════════════
    # INSTALLATION & SOFT COSTS
    # ════════════════════════════════════════════════════════════
    
    # Civil works, foundations ($/kW)
    civil_works_per_kW: float = 30
    
    # Electrical installation ($/kW)
    electrical_install_per_kW: float = 25
    
    # Mechanical installation ($/kW)
    mechanical_install_per_kW: float = 20
    
    # Commissioning and startup ($/kW)
    commissioning_per_kW: float = 15
    
    # Engineering and design (% of equipment)
    engineering_pct: float = 0.08
    
    # Project management (% of equipment)
    project_management_pct: float = 0.05
    
    # Contingency (% of direct costs)
    contingency_pct: float = 0.10
    
    # ════════════════════════════════════════════════════════════
    # SCALING
    # ════════════════════════════════════════════════════════════
    
    # Reference capacity for scaling
    reference_capacity_MW: float = 100
    
    # Scaling exponent
    scaling_exponent: float = 0.75
    
    # Learning rate (cost reduction per doubling)
    learning_rate: float = 0.18


@dataclass
class ElectrolyzerOpexInputs:
    """Detailed OPEX breakdown for electrolyzer system"""
    
    # ════════════════════════════════════════════════════════════
    # ELECTRICITY (Primary cost driver)
    # ════════════════════════════════════════════════════════════
    
    # Electricity price ($/MWh)
    electricity_price_per_MWh: float = 40
    
    # Price escalation (%/year)
    electricity_escalation: float = 0.02
    
    # Use PPA or grid
    use_ppa: bool = True
    ppa_price_per_MWh: float = 35
    
    # ════════════════════════════════════════════════════════════
    # WATER
    # ════════════════════════════════════════════════════════════
    
    # Feedwater consumption (L/kg H2)
    feedwater_consumption_L_kg: float = 9
    
    # Cooling water (L/kg H2) - for cooling loops
    cooling_water_L_kg: float = 0
    
    # Wastewater treatment (L/kg H2)
    wastewater_L_kg: float = 42
    
    # Water unit cost ($/m³)
    water_cost_per_m3: float = 1.36
    
    # Wastewater treatment energy (kWh/m³)
    wastewater_energy_kWh_m3: float = 2.2
    
    # ════════════════════════════════════════════════════════════
    # LABOR
    # ════════════════════════════════════════════════════════════
    
    # Operators per MW
    operators_per_MW: float = 0.05
    
    # Operator annual cost ($/year)
    operator_cost_per_year: float = 80000
    
    # Minimum operators (even for small plants)
    min_operators: int = 1
    
    # ════════════════════════════════════════════════════════════
    # MAINTENANCE
    # ════════════════════════════════════════════════════════════
    
    # Fixed O&M (% of CAPEX per year)
    fixed_om_pct_capex: float = 0.02
    
    # Variable O&M ($/kg H2)
    variable_om_per_kg: float = 0.05
    
    # Major maintenance interval (years)
    major_maintenance_years: int = 5
    
    # Major maintenance cost (% of equipment CAPEX)
    major_maintenance_pct: float = 0.03
    
    # ════════════════════════════════════════════════════════════
    # STACK REPLACEMENT
    # ════════════════════════════════════════════════════════════
    
    # Stack lifetime (hours)
    stack_lifetime_hours: float = 80000
    
    # Stack replacement cost (% of original stack cost)
    stack_replacement_pct: float = 0.50
    
    # Stack degradation rate (%/1000 hours)
    stack_degradation_per_1000h: float = 0.10
    
    # Minimum SOH before mandatory replacement
    min_soh: float = 0.90
    
    # ════════════════════════════════════════════════════════════
    # CONSUMABLES
    # ════════════════════════════════════════════════════════════
    
    # DI resin replacement ($/kg H2)
    di_resin_per_kg: float = 0.002
    
    # Desiccant replacement ($/kg H2)
    desiccant_per_kg: float = 0.001
    
    # KOH makeup for alkaline ($/kg H2) - 0 for PEM
    electrolyte_makeup_per_kg: float = 0.005
    
    # ════════════════════════════════════════════════════════════
    # INSURANCE & ADMIN
    # ════════════════════════════════════════════════════════════
    
    # Insurance (% of CAPEX)
    insurance_pct_capex: float = 0.005
    
    # Property tax (% of CAPEX)
    property_tax_pct_capex: float = 0.01
    
    # Administrative overhead ($/kW-year)
    admin_per_kW_year: float = 2


@dataclass
class ElectrolyzerPerformanceInputs:
    """Performance and operational parameters"""
    
    # ════════════════════════════════════════════════════════════
    # CAPACITY & UTILIZATION
    # ════════════════════════════════════════════════════════════
    
    # Nameplate capacity (MW)
    capacity_MW: float = 400
    
    # Target capacity factor (%)
    target_capacity_factor: float = 0.65
    
    # Availability (accounting for maintenance)
    availability: float = 0.875
    
    # Minimum load (fraction of capacity)
    min_load_fraction: float = 0.20
    
    # ════════════════════════════════════════════════════════════
    # EFFICIENCY
    # ════════════════════════════════════════════════════════════
    
    # Specific energy consumption (kWh/kg H2) at BOL
    specific_energy_bol: float = 52
    
    # System efficiency includes BOP losses
    bop_efficiency: float = 0.95
    
    # ════════════════════════════════════════════════════════════
    # OUTPUT
    # ════════════════════════════════════════════════════════════
    
    # Output pressure (bar)
    output_pressure_bar: float = 30
    
    # Output purity (%)
    output_purity: float = 0.9999
    
    # ════════════════════════════════════════════════════════════
    # DEGRADATION
    # ════════════════════════════════════════════════════════════
    
    # Annual degradation (efficiency loss)
    annual_degradation: float = 0.01
    
    # Degradation recovery after stack replacement
    degradation_recovery: float = 1.0  # Full recovery


@dataclass
class ElectrolyzerFinancialInputs:
    """Financial parameters for electrolyzer TEA"""
    
    # Project timeline
    project_lifetime_years: int = 30
    construction_months: int = 18
    
    # Discount rate / WACC
    discount_rate: float = 0.08
    
    # Inflation
    inflation_rate: float = 0.023
    
    # Tax
    tax_rate: float = 0.21
    depreciation_years: int = 20
    
    # Incentives
    itc_rate: float = 0.30  # Investment Tax Credit
    ptc_per_kg: float = 0.0  # Production Tax Credit ($/kg H2)
    ptc_years: int = 10
    
    # 45V Clean Hydrogen PTC (if applicable)
    clean_h2_ptc_per_kg: float = 3.00  # Up to $3/kg for cleanest H2
    clean_h2_ptc_years: int = 10
    
    # CAPEX deployment
    capex_deployment: Dict[int, float] = field(default_factory=lambda: {
        -2: 0.08,
        -1: 0.60,
        0: 0.32,
    })


@dataclass
class ElectrolyzerTEAOutputs:
    """Complete electrolyzer TEA results"""
    
    # ════════════════════════════════════════════════════════════
    # SYSTEM SIZING
    # ════════════════════════════════════════════════════════════
    capacity_MW: float = 0
    annual_operating_hours: float = 0
    capacity_factor: float = 0
    
    # Production
    annual_H2_kg: float = 0
    annual_H2_tonnes: float = 0
    lifetime_H2_tonnes: float = 0
    
    # Consumption
    annual_electricity_MWh: float = 0
    annual_water_m3: float = 0
    
    # ════════════════════════════════════════════════════════════
    # CAPEX BREAKDOWN
    # ════════════════════════════════════════════════════════════
    capex_stack: float = 0
    capex_stack_balance: float = 0
    capex_power_electronics: float = 0
    capex_water_treatment: float = 0
    capex_thermal_management: float = 0
    capex_gas_processing: float = 0
    capex_piping: float = 0
    capex_controls: float = 0
    capex_safety: float = 0
    capex_compressor: float = 0
    capex_civil: float = 0
    capex_electrical_install: float = 0
    capex_mechanical_install: float = 0
    capex_commissioning: float = 0
    
    capex_equipment_subtotal: float = 0
    capex_engineering: float = 0
    capex_project_management: float = 0
    capex_contingency: float = 0
    
    capex_total: float = 0
    capex_per_kW: float = 0
    capex_after_itc: float = 0
    
    # ════════════════════════════════════════════════════════════
    # OPEX BREAKDOWN (Year 1)
    # ════════════════════════════════════════════════════════════
    opex_electricity: float = 0
    opex_water: float = 0
    opex_labor: float = 0
    opex_fixed_om: float = 0
    opex_variable_om: float = 0
    opex_consumables: float = 0
    opex_insurance: float = 0
    opex_property_tax: float = 0
    opex_admin: float = 0
    opex_stack_reserve: float = 0
    
    opex_total_yr1: float = 0
    
    # ════════════════════════════════════════════════════════════
    # LEVELIZED COSTS
    # ════════════════════════════════════════════════════════════
    lcoh_capex: float = 0       # $/kg from CAPEX
    lcoh_electricity: float = 0 # $/kg from electricity
    lcoh_water: float = 0       # $/kg from water
    lcoh_fixed_opex: float = 0  # $/kg from fixed OPEX
    lcoh_variable_opex: float = 0  # $/kg from variable OPEX
    lcoh_stack_replacement: float = 0  # $/kg from stack replacement
    
    lcoh_total: float = 0       # $/kg total
    lcoh_with_ptc: float = 0    # $/kg after PTC
    
    # ════════════════════════════════════════════════════════════
    # FINANCIAL METRICS
    # ════════════════════════════════════════════════════════════
    npv: float = 0
    irr: float = 0
    payback_simple: float = 0
    payback_discounted: float = 0
    
    # ════════════════════════════════════════════════════════════
    # EMISSIONS
    # ════════════════════════════════════════════════════════════
    co2_intensity_kg_per_kg_h2: float = 0
    co2_source: str = ""  # "Grid", "Solar", "Wind", "Mixed"
    
    # ════════════════════════════════════════════════════════════
    # CASH FLOWS
    # ════════════════════════════════════════════════════════════
    years: List[int] = field(default_factory=list)
    cf_capex: List[float] = field(default_factory=list)
    cf_revenue: List[float] = field(default_factory=list)
    cf_opex: List[float] = field(default_factory=list)
    cf_ptc: List[float] = field(default_factory=list)
    cf_net: List[float] = field(default_factory=list)
    cf_cumulative: List[float] = field(default_factory=list)
    
    # Production by year
    production_kg_by_year: List[float] = field(default_factory=list)
    efficiency_by_year: List[float] = field(default_factory=list)


class ElectrolyzerTEA:
    """
    Detailed Techno-Economic Analysis for Electrolyzer Systems.
    
    Supports PEM, Alkaline, and SOEC technologies with:
    - Component-level CAPEX
    - Detailed OPEX including electricity, water, labor
    - Stack degradation and replacement
    - LCOH calculation with cost breakdown
    - 45V PTC modeling
    """
    
    def __init__(self,
                 technology: ElectrolyzerTechnology = ElectrolyzerTechnology.ALKALINE,
                 capex: Optional[ElectrolyzerCapexInputs] = None,
                 opex: Optional[ElectrolyzerOpexInputs] = None,
                 performance: Optional[ElectrolyzerPerformanceInputs] = None,
                 financial: Optional[ElectrolyzerFinancialInputs] = None):
        
        self.technology = technology
        self.capex = capex or ElectrolyzerCapexInputs()
        self.opex = opex or ElectrolyzerOpexInputs()
        self.performance = performance or ElectrolyzerPerformanceInputs()
        self.financial = financial or ElectrolyzerFinancialInputs()
        
        # Apply technology-specific defaults if not customized
        self._apply_tech_defaults()
        
        self._outputs: Optional[ElectrolyzerTEAOutputs] = None
        self._issues: List[str] = []
    
    def _apply_tech_defaults(self):
        """Apply technology-specific default values"""
        tech_specs = getattr(ElectrolyzerTechSpecs, self.technology.value, None)
        if tech_specs:
            # Only apply if using default specific energy
            if self.performance.specific_energy_bol == 52:  # Default alkaline
                self.performance.specific_energy_bol = tech_specs.get('specific_energy_kWh_kg', 52)
            if self.opex.stack_lifetime_hours == 80000:
                self.opex.stack_lifetime_hours = tech_specs.get('stack_lifetime_hours', 80000)
    
    @property
    def outputs(self) -> ElectrolyzerTEAOutputs:
        if self._outputs is None:
            raise ValueError("TEA not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> List[str]:
        return self._issues
    
    def calculate(self,
                  h2_selling_price: float = 6.0,
                  electricity_source: str = "Mixed",
                  grid_co2_intensity: float = 0.4) -> ElectrolyzerTEAOutputs:
        """
        Run complete electrolyzer TEA.
        
        Args:
            h2_selling_price: H2 sales price ($/kg)
            electricity_source: "Grid", "Solar", "Wind", "Mixed"
            grid_co2_intensity: kg CO2/kWh for grid electricity
        """
        self._issues = []
        out = ElectrolyzerTEAOutputs()
        
        perf = self.performance
        cx = self.capex
        ox = self.opex
        fin = self.financial
        
        # ════════════════════════════════════════════════════════════
        # SYSTEM SIZING
        # ════════════════════════════════════════════════════════════
        
        out.capacity_MW = perf.capacity_MW
        out.capacity_factor = perf.target_capacity_factor
        out.annual_operating_hours = 8760 * perf.target_capacity_factor * perf.availability
        
        # H2 production (Year 1)
        out.annual_H2_kg = (perf.capacity_MW * 1000 * out.annual_operating_hours / 
                           perf.specific_energy_bol)
        out.annual_H2_tonnes = out.annual_H2_kg / 1000
        
        # Electricity consumption
        out.annual_electricity_MWh = perf.capacity_MW * out.annual_operating_hours
        
        # Water consumption
        out.annual_water_m3 = (out.annual_H2_kg * ox.feedwater_consumption_L_kg / 1000 +
                              out.annual_H2_kg * ox.cooling_water_L_kg / 1000)
        
        # ════════════════════════════════════════════════════════════
        # CAPEX CALCULATION
        # ════════════════════════════════════════════════════════════
        
        capacity_kW = perf.capacity_MW * 1000
        
        # Apply scaling if different from reference
        if perf.capacity_MW != cx.reference_capacity_MW:
            scale_factor = (perf.capacity_MW / cx.reference_capacity_MW) ** (cx.scaling_exponent - 1)
        else:
            scale_factor = 1.0
        
        # Stack and cell
        out.capex_stack = capacity_kW * cx.stack_cost_per_kW * scale_factor
        out.capex_stack_balance = capacity_kW * cx.stack_balance_per_kW
        
        # Power electronics
        out.capex_power_electronics = capacity_kW * (
            cx.rectifier_cost_per_kW + cx.dc_converter_per_kW + cx.switchgear_per_kW
        )
        
        # Balance of plant
        out.capex_water_treatment = capacity_kW * cx.water_treatment_per_kW
        out.capex_thermal_management = capacity_kW * cx.thermal_management_per_kW
        out.capex_gas_processing = capacity_kW * cx.gas_processing_per_kW
        out.capex_piping = capacity_kW * cx.piping_and_valves_per_kW
        out.capex_controls = capacity_kW * cx.control_system_per_kW
        out.capex_safety = capacity_kW * cx.safety_systems_per_kW
        out.capex_compressor = capacity_kW * cx.compressor_per_kW
        
        # Installation
        out.capex_civil = capacity_kW * cx.civil_works_per_kW
        out.capex_electrical_install = capacity_kW * cx.electrical_install_per_kW
        out.capex_mechanical_install = capacity_kW * cx.mechanical_install_per_kW
        out.capex_commissioning = capacity_kW * cx.commissioning_per_kW
        
        # Equipment subtotal
        out.capex_equipment_subtotal = (
            out.capex_stack + out.capex_stack_balance + out.capex_power_electronics +
            out.capex_water_treatment + out.capex_thermal_management + 
            out.capex_gas_processing + out.capex_piping + out.capex_controls +
            out.capex_safety + out.capex_compressor + out.capex_civil +
            out.capex_electrical_install + out.capex_mechanical_install +
            out.capex_commissioning
        )
        
        # Soft costs
        out.capex_engineering = out.capex_equipment_subtotal * cx.engineering_pct
        out.capex_project_management = out.capex_equipment_subtotal * cx.project_management_pct
        
        direct_costs = out.capex_equipment_subtotal + out.capex_engineering + out.capex_project_management
        out.capex_contingency = direct_costs * cx.contingency_pct
        
        out.capex_total = direct_costs + out.capex_contingency
        out.capex_per_kW = out.capex_total / capacity_kW
        
        # ITC benefit
        itc_benefit = out.capex_total * fin.itc_rate
        out.capex_after_itc = out.capex_total - itc_benefit
        
        # ════════════════════════════════════════════════════════════
        # OPEX CALCULATION (Year 1)
        # ════════════════════════════════════════════════════════════
        
        # Electricity (largest cost)
        if ox.use_ppa:
            elec_price = ox.ppa_price_per_MWh
        else:
            elec_price = ox.electricity_price_per_MWh
        out.opex_electricity = out.annual_electricity_MWh * elec_price
        
        # Water
        total_water = out.annual_water_m3 + out.annual_H2_kg * ox.wastewater_L_kg / 1000
        out.opex_water = total_water * ox.water_cost_per_m3
        
        # Labor
        operators = max(ox.min_operators, perf.capacity_MW * ox.operators_per_MW)
        out.opex_labor = operators * ox.operator_cost_per_year
        
        # Fixed O&M
        out.opex_fixed_om = out.capex_total * ox.fixed_om_pct_capex
        
        # Variable O&M
        out.opex_variable_om = out.annual_H2_kg * ox.variable_om_per_kg
        
        # Consumables
        out.opex_consumables = out.annual_H2_kg * (
            ox.di_resin_per_kg + ox.desiccant_per_kg + ox.electrolyte_makeup_per_kg
        )
        
        # Insurance and admin
        out.opex_insurance = out.capex_total * ox.insurance_pct_capex
        out.opex_property_tax = out.capex_total * ox.property_tax_pct_capex
        out.opex_admin = capacity_kW * ox.admin_per_kW_year
        
        # Stack replacement reserve (annualized)
        stack_life_years = ox.stack_lifetime_hours / out.annual_operating_hours
        num_replacements = int(fin.project_lifetime_years / stack_life_years)
        stack_cost = out.capex_stack
        
        # NPV of stack replacements
        stack_repl_npv = 0
        for i in range(1, num_replacements + 1):
            repl_year = int(i * stack_life_years)
            if repl_year < fin.project_lifetime_years:
                repl_cost = stack_cost * ox.stack_replacement_pct
                stack_repl_npv += repl_cost / ((1 + fin.discount_rate) ** repl_year)
        
        # Annualize
        crf = self._crf(fin.discount_rate, fin.project_lifetime_years)
        out.opex_stack_reserve = stack_repl_npv * crf
        
        # Total OPEX
        out.opex_total_yr1 = (
            out.opex_electricity + out.opex_water + out.opex_labor +
            out.opex_fixed_om + out.opex_variable_om + out.opex_consumables +
            out.opex_insurance + out.opex_property_tax + out.opex_admin +
            out.opex_stack_reserve
        )
        
        # ════════════════════════════════════════════════════════════
        # LCOH CALCULATION (Cost Breakdown)
        # ════════════════════════════════════════════════════════════
        
        # Annualized CAPEX
        annualized_capex = out.capex_after_itc * crf
        
        # Lifetime H2 production (with degradation)
        lifetime_h2_npv = 0
        for year in range(1, fin.project_lifetime_years + 1):
            degradation = (1 - perf.annual_degradation) ** (year - 1)
            year_h2 = out.annual_H2_kg * degradation
            lifetime_h2_npv += year_h2 / ((1 + fin.discount_rate) ** year)
        
        out.lifetime_H2_tonnes = sum(
            out.annual_H2_kg * (1 - perf.annual_degradation) ** (y - 1) / 1000
            for y in range(1, fin.project_lifetime_years + 1)
        )
        
        # LCOH components
        out.lcoh_capex = annualized_capex / out.annual_H2_kg
        out.lcoh_electricity = out.opex_electricity / out.annual_H2_kg
        out.lcoh_water = out.opex_water / out.annual_H2_kg
        out.lcoh_fixed_opex = (out.opex_fixed_om + out.opex_labor + 
                              out.opex_insurance + out.opex_property_tax + 
                              out.opex_admin) / out.annual_H2_kg
        out.lcoh_variable_opex = (out.opex_variable_om + out.opex_consumables) / out.annual_H2_kg
        out.lcoh_stack_replacement = out.opex_stack_reserve / out.annual_H2_kg
        
        out.lcoh_total = (out.lcoh_capex + out.lcoh_electricity + out.lcoh_water +
                         out.lcoh_fixed_opex + out.lcoh_variable_opex + 
                         out.lcoh_stack_replacement)
        
        # LCOH with PTC
        if fin.clean_h2_ptc_per_kg > 0:
            # PTC applies for first N years
            ptc_value_npv = sum(
                out.annual_H2_kg * fin.clean_h2_ptc_per_kg / ((1 + fin.discount_rate) ** y)
                for y in range(1, min(fin.clean_h2_ptc_years + 1, fin.project_lifetime_years + 1))
            )
            ptc_per_kg = ptc_value_npv / lifetime_h2_npv
            out.lcoh_with_ptc = max(0, out.lcoh_total - ptc_per_kg)
        else:
            out.lcoh_with_ptc = out.lcoh_total
        
        # ════════════════════════════════════════════════════════════
        # CASH FLOW ANALYSIS
        # ════════════════════════════════════════════════════════════
        
        years = list(range(-2, fin.project_lifetime_years + 1))
        out.years = years
        
        cf_capex = []
        cf_revenue = []
        cf_opex = []
        cf_ptc = []
        cf_net = []
        production_by_year = []
        efficiency_by_year = []
        
        cumulative = 0
        
        for year in years:
            if year < 1:
                # Construction
                capex_frac = fin.capex_deployment.get(year, 0)
                capex_yr = -out.capex_after_itc * capex_frac
                cf_capex.append(capex_yr)
                cf_revenue.append(0)
                cf_opex.append(0)
                cf_ptc.append(0)
                cf_net.append(capex_yr)
                production_by_year.append(0)
                efficiency_by_year.append(perf.specific_energy_bol)
            else:
                # Operating
                cf_capex.append(0)
                
                # Degradation
                degradation = (1 - perf.annual_degradation) ** (year - 1)
                year_h2 = out.annual_H2_kg * degradation
                year_efficiency = perf.specific_energy_bol / degradation
                
                production_by_year.append(year_h2)
                efficiency_by_year.append(year_efficiency)
                
                # Revenue
                revenue = year_h2 * h2_selling_price
                cf_revenue.append(revenue)
                
                # OPEX with inflation
                inflation_factor = (1 + fin.inflation_rate) ** (year - 1)
                year_opex = out.opex_total_yr1 * inflation_factor
                
                # Stack replacement in this year?
                if stack_life_years > 0:
                    operating_hours = year * out.annual_operating_hours
                    if operating_hours % ox.stack_lifetime_hours < out.annual_operating_hours:
                        year_opex += stack_cost * ox.stack_replacement_pct * inflation_factor
                
                cf_opex.append(-year_opex)
                
                # PTC
                if year <= fin.clean_h2_ptc_years and fin.clean_h2_ptc_per_kg > 0:
                    ptc = year_h2 * fin.clean_h2_ptc_per_kg
                else:
                    ptc = 0
                cf_ptc.append(ptc)
                
                cf_net.append(revenue - year_opex + ptc)
            
            cumulative += cf_net[-1]
        
        out.cf_capex = cf_capex
        out.cf_revenue = cf_revenue
        out.cf_opex = cf_opex
        out.cf_ptc = cf_ptc
        out.cf_net = cf_net
        out.cf_cumulative = list(np.cumsum(cf_net))
        out.production_kg_by_year = production_by_year
        out.efficiency_by_year = efficiency_by_year
        
        # ════════════════════════════════════════════════════════════
        # FINANCIAL METRICS
        # ════════════════════════════════════════════════════════════
        
        out.npv = sum(cf / ((1 + fin.discount_rate) ** (i - years[0])) 
                     for i, cf in zip(years, cf_net) if i >= -2)
        out.irr = self._calculate_irr(cf_net)
        out.payback_simple = self._simple_payback(out.cf_cumulative)
        out.payback_discounted = self._discounted_payback(cf_net, years, fin.discount_rate)
        
        # ════════════════════════════════════════════════════════════
        # EMISSIONS
        # ════════════════════════════════════════════════════════════
        
        out.co2_source = electricity_source
        if electricity_source == "Grid":
            out.co2_intensity_kg_per_kg_h2 = (
                perf.specific_energy_bol * grid_co2_intensity
            )
        elif electricity_source in ["Solar", "Wind"]:
            out.co2_intensity_kg_per_kg_h2 = 0.5  # Only embodied/upstream
        else:  # Mixed
            out.co2_intensity_kg_per_kg_h2 = (
                perf.specific_energy_bol * grid_co2_intensity * 0.3  # Assume 30% grid
            )
        
        # ════════════════════════════════════════════════════════════
        # VALIDATION
        # ════════════════════════════════════════════════════════════
        
        if out.lcoh_total > 10:
            self._issues.append(f"LCOH of ${out.lcoh_total:.2f}/kg is very high. Check inputs.")
        
        if out.irr < fin.discount_rate:
            self._issues.append(f"IRR ({out.irr:.1%}) below discount rate ({fin.discount_rate:.1%}).")
        
        self._outputs = out
        return out
    
    def _crf(self, rate: float, years: int) -> float:
        """Capital Recovery Factor"""
        if rate == 0:
            return 1 / years
        return (rate * (1 + rate) ** years) / ((1 + rate) ** years - 1)
    
    def _calculate_irr(self, cashflows: List[float], max_iter: int = 1000) -> float:
        """Calculate IRR"""
        try:
            import numpy_financial as npf
            return npf.irr(cashflows)
        except:
            irr = 0.10
            for _ in range(max_iter):
                npv = sum(cf / ((1 + irr) ** i) for i, cf in enumerate(cashflows))
                dnpv = sum(-i * cf / ((1 + irr) ** (i + 1)) for i, cf in enumerate(cashflows) if i > 0)
                if abs(dnpv) < 1e-10:
                    break
                new_irr = irr - npv / dnpv
                if abs(new_irr - irr) < 1e-7:
                    return new_irr
                irr = max(-0.99, min(10, new_irr))
            return irr
    
    def _simple_payback(self, cumulative: List[float]) -> float:
        """Simple payback period"""
        for i, cf in enumerate(cumulative):
            if cf >= 0 and i > 0:
                prev = cumulative[i - 1]
                if cf != prev:
                    return (i - 1) + (-prev / (cf - prev))
        return float('inf')
    
    def _discounted_payback(self, cashflows: List[float], years: List[int], rate: float) -> float:
        """Discounted payback period"""
        cumulative = 0
        for i, (cf, year) in enumerate(zip(cashflows, years)):
            pv = cf / ((1 + rate) ** max(0, year + 2))
            prev = cumulative
            cumulative += pv
            if cumulative >= 0 and i > 0:
                if pv != 0:
                    return i - 1 + (-prev / pv)
        return float('inf')
    
    def summary(self) -> str:
        """Generate formatted summary"""
        if self._outputs is None:
            return "TEA not calculated. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"    ⚠ {issue}" for issue in self._issues) if self._issues else "    None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════════════════════════════╗
║                    ELECTROLYZER TEA SUMMARY ({self.technology.value})                              
╠══════════════════════════════════════════════════════════════════════════════════════════╣

  SYSTEM CONFIGURATION
  ────────────────────────────────────────────────────────────────────────────────────────
    Capacity:                      {o.capacity_MW:>12,.0f} MW
    Capacity Factor:               {o.capacity_factor:>12.1%}
    Operating Hours:               {o.annual_operating_hours:>12,.0f} hrs/yr
    Specific Energy:               {self.performance.specific_energy_bol:>12.1f} kWh/kg
    
    Annual Production:
      H2:                          {o.annual_H2_tonnes:>12,.0f} tonnes/yr
      H2:                          {o.annual_H2_kg:>12,.0f} kg/yr
    
    Annual Consumption:
      Electricity:                 {o.annual_electricity_MWh:>12,.0f} MWh/yr
      Water:                       {o.annual_water_m3:>12,.0f} m³/yr

  CAPEX BREAKDOWN
  ────────────────────────────────────────────────────────────────────────────────────────
    Stack:                         ${o.capex_stack:>14,.0f}
    Stack Balance:                 ${o.capex_stack_balance:>14,.0f}
    Power Electronics:             ${o.capex_power_electronics:>14,.0f}
    Water Treatment:               ${o.capex_water_treatment:>14,.0f}
    Thermal Management:            ${o.capex_thermal_management:>14,.0f}
    Gas Processing:                ${o.capex_gas_processing:>14,.0f}
    Piping & Valves:               ${o.capex_piping:>14,.0f}
    Controls:                      ${o.capex_controls:>14,.0f}
    Safety Systems:                ${o.capex_safety:>14,.0f}
    Compressor:                    ${o.capex_compressor:>14,.0f}
    Civil/Installation:            ${o.capex_civil + o.capex_electrical_install + o.capex_mechanical_install:>14,.0f}
    Commissioning:                 ${o.capex_commissioning:>14,.0f}
    Engineering/PM:                ${o.capex_engineering + o.capex_project_management:>14,.0f}
    Contingency:                   ${o.capex_contingency:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────────────
    TOTAL CAPEX:                   ${o.capex_total:>14,.0f}
    CAPEX per kW:                  ${o.capex_per_kW:>14,.0f}
    After ITC:                     ${o.capex_after_itc:>14,.0f}

  ANNUAL OPEX (Year 1)
  ────────────────────────────────────────────────────────────────────────────────────────
    Electricity:                   ${o.opex_electricity:>14,.0f}   ({o.opex_electricity/o.opex_total_yr1*100:>5.1f}%)
    Water:                         ${o.opex_water:>14,.0f}   ({o.opex_water/o.opex_total_yr1*100:>5.1f}%)
    Labor:                         ${o.opex_labor:>14,.0f}   ({o.opex_labor/o.opex_total_yr1*100:>5.1f}%)
    Fixed O&M:                     ${o.opex_fixed_om:>14,.0f}   ({o.opex_fixed_om/o.opex_total_yr1*100:>5.1f}%)
    Variable O&M:                  ${o.opex_variable_om:>14,.0f}   ({o.opex_variable_om/o.opex_total_yr1*100:>5.1f}%)
    Consumables:                   ${o.opex_consumables:>14,.0f}
    Insurance:                     ${o.opex_insurance:>14,.0f}
    Property Tax:                  ${o.opex_property_tax:>14,.0f}
    Stack Reserve:                 ${o.opex_stack_reserve:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────────────
    TOTAL OPEX:                    ${o.opex_total_yr1:>14,.0f}

  LCOH BREAKDOWN
  ────────────────────────────────────────────────────────────────────────────────────────
    CAPEX:                         ${o.lcoh_capex:>14.2f} /kg   ({o.lcoh_capex/o.lcoh_total*100:>5.1f}%)
    Electricity:                   ${o.lcoh_electricity:>14.2f} /kg   ({o.lcoh_electricity/o.lcoh_total*100:>5.1f}%)
    Water:                         ${o.lcoh_water:>14.2f} /kg
    Fixed OPEX:                    ${o.lcoh_fixed_opex:>14.2f} /kg
    Variable OPEX:                 ${o.lcoh_variable_opex:>14.2f} /kg
    Stack Replacement:             ${o.lcoh_stack_replacement:>14.2f} /kg
    ─────────────────────────────────────────────────────────────────────────────────
    TOTAL LCOH:                    ${o.lcoh_total:>14.2f} /kg
    LCOH with PTC:                 ${o.lcoh_with_ptc:>14.2f} /kg

  FINANCIAL METRICS
  ────────────────────────────────────────────────────────────────────────────────────────
    NPV:                           ${o.npv:>14,.0f}
    IRR:                           {o.irr:>14.1%}
    Simple Payback:                {o.payback_simple:>14.1f} years
    Discounted Payback:            {o.payback_discounted:>14.1f} years

  EMISSIONS
  ────────────────────────────────────────────────────────────────────────────────────────
    CO2 Intensity:                 {o.co2_intensity_kg_per_kg_h2:>14.2f} kg CO2/kg H2
    Electricity Source:            {o.co2_source:>14}

  ISSUES/WARNINGS
  ────────────────────────────────────────────────────────────────────────────────────────
{issues_str}

╚══════════════════════════════════════════════════════════════════════════════════════════╝
"""


# ============================================================
# EXAMPLE
# ============================================================

if __name__ == "__main__":
    print("=" * 90)
    print("ELECTROLYZER TEA - EXAMPLE (400 MW Alkaline)")
    print("=" * 90)
    
    tea = ElectrolyzerTEA(
        technology=ElectrolyzerTechnology.ALKALINE,
        performance=ElectrolyzerPerformanceInputs(
            capacity_MW=400,
            specific_energy_bol=52,
            target_capacity_factor=0.65,
            availability=0.875,
        ),
        opex=ElectrolyzerOpexInputs(
            electricity_price_per_MWh=40,
            use_ppa=True,
            ppa_price_per_MWh=35,
        ),
        financial=ElectrolyzerFinancialInputs(
            project_lifetime_years=30,
            discount_rate=0.08,
            itc_rate=0.30,
            clean_h2_ptc_per_kg=3.00,
        ),
    )
    
    results = tea.calculate(
        h2_selling_price=6.0,
        electricity_source="Mixed",
    )
    
    print(tea.summary())
    
    # Print first 10 years
    print("\nANNUAL CASH FLOWS:")
    print(f"{'Year':<6} {'Production':>12} {'Revenue':>14} {'OPEX':>14} {'PTC':>12} {'Net CF':>14}")
    print("-" * 80)
    for i in range(min(13, len(results.years))):
        y = results.years[i]
        prod = results.production_kg_by_year[i]
        rev = results.cf_revenue[i]
        opex = results.cf_opex[i]
        ptc = results.cf_ptc[i]
        net = results.cf_net[i]
        print(f"{y:<6} {prod:>12,.0f} ${rev:>13,.0f} ${opex:>13,.0f} ${ptc:>11,.0f} ${net:>13,.0f}")
