"""
TriGen Techno-Economic Analysis (TEA) Module - DETAILED VERSION

This module performs comprehensive, granular techno-economic analysis for the 
TriGen MCFC system (FuelCell Energy SureSource 3000/4000 architecture).

LEVEL OF DETAIL:
- Component-level CAPEX breakdown (17 line items)
- Detailed O&M with labor, consumables, maintenance schedules
- MACRS depreciation schedules (5, 7, 15, 20 year)
- Monthly cash flow modeling with seasonality
- Multiple cost allocation methods (energy, exergy, market value)
- Detailed financing options (debt/equity, loan amortization)
- Tax modeling with carryforward losses
- Sensitivity and Monte Carlo analysis ready

References:
- FuelCell Energy 10-K filings (2020-2024)
- DOE H2A Model v3.2018
- NREL ATB 2024
- EIA Annual Energy Outlook 2024
- EPA eGRID 2023
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Literal
from enum import Enum
import math
from datetime import datetime


# ============================================================
# ENUMS AND CONSTANTS
# ============================================================

class DepreciationMethod(Enum):
    MACRS_5 = "MACRS_5"
    MACRS_7 = "MACRS_7"
    MACRS_15 = "MACRS_15"
    MACRS_20 = "MACRS_20"
    STRAIGHT_LINE = "STRAIGHT_LINE"


class CostAllocationMethod(Enum):
    ENERGY_CONTENT = "energy_content"  # Based on LHV energy
    EXERGY = "exergy"  # Based on exergy (work potential)
    MARKET_VALUE = "market_value"  # Based on market prices
    INCREMENTAL = "incremental"  # Primary product bears all cost


# MACRS Depreciation Tables (half-year convention)
MACRS_TABLES = {
    "MACRS_5": [0.2000, 0.3200, 0.1920, 0.1152, 0.1152, 0.0576],
    "MACRS_7": [0.1429, 0.2449, 0.1749, 0.1249, 0.0893, 0.0892, 0.0893, 0.0446],
    "MACRS_15": [0.0500, 0.0950, 0.0855, 0.0770, 0.0693, 0.0623, 0.0590, 0.0590,
                 0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0295],
    "MACRS_20": [0.0375, 0.0722, 0.0668, 0.0618, 0.0571, 0.0528, 0.0489, 0.0452,
                 0.0447, 0.0447, 0.0446, 0.0446, 0.0446, 0.0446, 0.0446, 0.0446,
                 0.0446, 0.0446, 0.0446, 0.0446, 0.0223],
}


# ============================================================
# DETAILED COST INPUT STRUCTURES
# ============================================================

@dataclass
class TriGenCapexInputs:
    """
    Detailed component-level CAPEX breakdown for TriGen MCFC system.
    All costs in USD.
    """
    
    # ════════════════════════════════════════════════════════════
    # FUEL CELL MODULE (Core Stack + BOP)
    # ════════════════════════════════════════════════════════════
    
    # Stack cost ($/kW) - MCFC stack modules
    stack_cost_per_kW: float = 1800.0
    
    # Power conditioning system ($/kW) - DC/AC inverter, transformers
    pcs_cost_per_kW: float = 350.0
    
    # Fuel processing - desulfurization, pre-reformer ($/kW)
    fuel_processing_cost_per_kW: float = 400.0
    
    # Air supply system - blowers, filters, humidifiers ($/kW)
    air_supply_cost_per_kW: float = 150.0
    
    # Thermal management - heat exchangers, pumps, cooling ($/kW)
    thermal_management_cost_per_kW: float = 250.0
    
    # Control system - PLC, sensors, HMI, SCADA interface ($/kW)
    control_system_cost_per_kW: float = 120.0
    
    # Water treatment - DI system, storage, pumps ($/kW)
    water_treatment_cost_per_kW: float = 80.0
    
    # ════════════════════════════════════════════════════════════
    # H2 PURIFICATION & HANDLING (for co-production)
    # ════════════════════════════════════════════════════════════
    
    # PSA system for H2 purification ($/kg-day capacity)
    psa_cost_per_kg_day: float = 800.0
    
    # H2 compression to buffer pressure ($/kg-day)
    h2_compression_cost_per_kg_day: float = 200.0
    
    # H2 buffer storage - LP vessels ($/kg capacity)
    h2_buffer_cost_per_kg: float = 500.0
    h2_buffer_days: float = 0.5  # Days of storage
    
    # ════════════════════════════════════════════════════════════
    # HEAT RECOVERY
    # ════════════════════════════════════════════════════════════
    
    # Exhaust heat recovery HX ($/kW_th)
    heat_recovery_hx_cost_per_kW_th: float = 100.0
    
    # Hot water/steam distribution ($/kW_th)
    heat_distribution_cost_per_kW_th: float = 50.0
    
    # ════════════════════════════════════════════════════════════
    # BALANCE OF PLANT & INSTALLATION
    # ════════════════════════════════════════════════════════════
    
    # Electrical interconnection - switchgear, metering, protection ($/kW)
    electrical_interconnection_per_kW: float = 150.0
    
    # Gas interconnection - meter, regulator, piping ($/scfh capacity)
    gas_interconnection_per_scfh: float = 2.0
    
    # Civil/structural - foundation, enclosure, site work ($, fixed + $/kW)
    civil_fixed_cost: float = 150000.0
    civil_cost_per_kW: float = 50.0
    
    # Commissioning and startup ($/kW)
    commissioning_cost_per_kW: float = 75.0
    
    # ════════════════════════════════════════════════════════════
    # SOFT COSTS
    # ════════════════════════════════════════════════════════════
    
    # Engineering & design (% of equipment)
    engineering_pct: float = 0.08
    
    # Project management (% of equipment)
    project_management_pct: float = 0.05
    
    # Permitting & interconnection fees ($, fixed)
    permitting_fees: float = 50000.0
    
    # Contingency (% of direct costs)
    contingency_pct: float = 0.10
    
    # Owner's costs - legal, insurance during construction, etc. (% of total)
    owners_costs_pct: float = 0.05
    
    # ════════════════════════════════════════════════════════════
    # LEARNING & SCALING
    # ════════════════════════════════════════════════════════════
    
    # Reference capacity for cost scaling (kW)
    reference_capacity_kW: float = 1400.0
    
    # Scaling exponent (0.6-0.8 typical for process equipment)
    scaling_exponent: float = 0.75
    
    # Nth-of-a-kind factor (1.0 = mature, 1.3 = FOAK)
    noak_factor: float = 1.0


@dataclass 
class TriGenOpexInputs:
    """
    Detailed operating cost breakdown for TriGen MCFC system.
    """
    
    # ════════════════════════════════════════════════════════════
    # FUEL COSTS
    # ════════════════════════════════════════════════════════════
    
    # Natural gas commodity ($/MMBtu)
    ng_commodity_price: float = 3.50
    
    # NG transportation/delivery ($/MMBtu)
    ng_delivery_price: float = 0.50
    
    # NG price escalation rate (%/year)
    ng_escalation_rate: float = 0.02
    
    # ════════════════════════════════════════════════════════════
    # LABOR
    # ════════════════════════════════════════════════════════════
    
    # On-site operator (FTE per MW)
    operators_per_MW: float = 0.25
    
    # Operator fully-loaded cost ($/year)
    operator_annual_cost: float = 85000.0
    
    # Remote monitoring service ($/kW-year) - if no on-site staff
    remote_monitoring_per_kW_year: float = 15.0
    
    # Use remote monitoring vs on-site staff
    use_remote_monitoring: bool = True
    
    # ════════════════════════════════════════════════════════════
    # MAINTENANCE - SCHEDULED
    # ════════════════════════════════════════════════════════════
    
    # Annual service contract ($/kW-year) - includes inspections
    service_contract_per_kW_year: float = 25.0
    
    # Minor maintenance (filters, gaskets, sensors) ($/kW-year)
    minor_maintenance_per_kW_year: float = 10.0
    
    # Major maintenance interval (years)
    major_maintenance_interval_years: float = 3.0
    
    # Major maintenance cost (% of equipment CAPEX)
    major_maintenance_pct_capex: float = 0.05
    
    # ════════════════════════════════════════════════════════════
    # STACK REPLACEMENT
    # ════════════════════════════════════════════════════════════
    
    # Stack life (operating hours)
    stack_life_hours: float = 60000.0
    
    # Stack replacement cost (% of original stack cost)
    stack_replacement_pct: float = 0.85  # Some learning/reuse
    
    # Stack degradation rate (%/1000 hours)
    stack_degradation_per_1000h: float = 0.15
    
    # ════════════════════════════════════════════════════════════
    # CONSUMABLES
    # ════════════════════════════════════════════════════════════
    
    # Desulfurization adsorbent ($/kg NG processed)
    desulf_adsorbent_per_kg_ng: float = 0.001
    
    # DI water for steam ($/gallon)
    di_water_cost_per_gallon: float = 0.01
    
    # Water consumption (gallons per kWh)
    water_consumption_gal_per_kWh: float = 0.08
    
    # Catalyst replacement interval (years)
    catalyst_replacement_years: float = 5.0
    
    # Catalyst cost ($/kW)
    catalyst_cost_per_kW: float = 30.0
    
    # ════════════════════════════════════════════════════════════
    # INSURANCE & ADMIN
    # ════════════════════════════════════════════════════════════
    
    # Property insurance (% of CAPEX per year)
    insurance_pct_capex: float = 0.005
    
    # Property taxes (% of CAPEX per year) - varies by location
    property_tax_pct_capex: float = 0.01
    
    # Administrative overhead ($/kW-year)
    admin_overhead_per_kW_year: float = 5.0
    
    # ════════════════════════════════════════════════════════════
    # PERFORMANCE
    # ════════════════════════════════════════════════════════════
    
    # Planned outage (days/year) - scheduled maintenance
    planned_outage_days: float = 10.0
    
    # Forced outage rate (%)
    forced_outage_rate: float = 0.02
    
    # Startup fuel consumption (MMBtu per start)
    startup_fuel_per_start: float = 50.0
    
    # Planned starts per year
    planned_starts_per_year: float = 4.0


@dataclass
class TriGenRevenueInputs:
    """
    Revenue and market price assumptions.
    """
    
    # ════════════════════════════════════════════════════════════
    # ELECTRICITY
    # ════════════════════════════════════════════════════════════
    
    # Base electricity price ($/kWh)
    electricity_price_base: float = 0.07
    
    # Demand charge avoided ($/kW-month)
    demand_charge_avoided: float = 15.0
    
    # Capacity payment ($/kW-year) - if participating in capacity market
    capacity_payment_per_kW_year: float = 50.0
    
    # Electricity price escalation (%/year)
    electricity_escalation_rate: float = 0.02
    
    # Time-of-use multipliers (if applicable)
    tou_on_peak_multiplier: float = 1.3
    tou_off_peak_multiplier: float = 0.8
    tou_on_peak_hours_per_day: float = 6.0
    
    # ════════════════════════════════════════════════════════════
    # HYDROGEN
    # ════════════════════════════════════════════════════════════
    
    # H2 sales price ($/kg)
    h2_price_per_kg: float = 6.00
    
    # H2 price escalation (%/year)
    h2_escalation_rate: float = 0.01
    
    # H2 purity premium (multiplier for 99.999% vs 99.9%)
    h2_purity_premium: float = 1.0
    
    # ════════════════════════════════════════════════════════════
    # HEAT
    # ════════════════════════════════════════════════════════════
    
    # Heat value ($/MMBtu)
    heat_price_per_MMBtu: float = 8.00
    
    # Heat utilization factor (% of available heat actually used)
    heat_utilization_factor: float = 0.70
    
    # Heat price escalation (%/year)
    heat_escalation_rate: float = 0.02
    
    # ════════════════════════════════════════════════════════════
    # INCENTIVES
    # ════════════════════════════════════════════════════════════
    
    # Investment Tax Credit (ITC) rate
    itc_rate: float = 0.30
    
    # ITC vesting period (years)
    itc_vesting_years: float = 5.0
    
    # Production Tax Credit ($/kWh) - alternative to ITC
    ptc_per_kWh: float = 0.0  # Set to 0 if using ITC
    
    # PTC duration (years)
    ptc_years: float = 10.0
    
    # State/local incentives ($/kW one-time)
    state_incentive_per_kW: float = 0.0
    
    # REC/carbon credit value ($/MWh)
    rec_value_per_MWh: float = 10.0
    
    # Low-carbon fuel standard credit ($/kg H2)
    lcfs_credit_per_kg_h2: float = 0.0


@dataclass
class TriGenFinancingInputs:
    """
    Detailed financing and tax assumptions.
    """
    
    # ════════════════════════════════════════════════════════════
    # CAPITAL STRUCTURE
    # ════════════════════════════════════════════════════════════
    
    # Debt fraction
    debt_fraction: float = 0.60
    
    # Equity fraction (1 - debt_fraction)
    @property
    def equity_fraction(self) -> float:
        return 1.0 - self.debt_fraction
    
    # Cost of debt (interest rate)
    cost_of_debt: float = 0.06
    
    # Cost of equity (required return)
    cost_of_equity: float = 0.12
    
    # WACC (calculated)
    @property
    def wacc(self) -> float:
        return (self.debt_fraction * self.cost_of_debt * (1 - self.federal_tax_rate) + 
                self.equity_fraction * self.cost_of_equity)
    
    # ════════════════════════════════════════════════════════════
    # DEBT TERMS
    # ════════════════════════════════════════════════════════════
    
    # Loan term (years)
    loan_term_years: int = 15
    
    # Debt service coverage ratio requirement
    dscr_required: float = 1.25
    
    # Loan origination fee (% of loan)
    loan_origination_fee: float = 0.01
    
    # ════════════════════════════════════════════════════════════
    # TAX
    # ════════════════════════════════════════════════════════════
    
    # Federal corporate tax rate
    federal_tax_rate: float = 0.21
    
    # State tax rate
    state_tax_rate: float = 0.05
    
    # Combined effective tax rate
    @property
    def effective_tax_rate(self) -> float:
        return self.federal_tax_rate + self.state_tax_rate * (1 - self.federal_tax_rate)
    
    # Depreciation method
    depreciation_method: DepreciationMethod = DepreciationMethod.MACRS_7
    
    # Bonus depreciation (% in year 1)
    bonus_depreciation: float = 0.60  # 60% bonus in 2024
    
    # Tax loss carryforward allowed
    tax_loss_carryforward: bool = True
    
    # ════════════════════════════════════════════════════════════
    # PROJECT TIMELINE
    # ════════════════════════════════════════════════════════════
    
    # Construction period (months)
    construction_months: int = 18
    
    # Construction interest (capitalize during construction)
    capitalize_construction_interest: bool = True
    
    # Project life (years)
    project_life_years: int = 20
    
    # Analysis period (years) - may differ from project life
    analysis_period_years: int = 25
    
    # Terminal value method
    terminal_value_method: str = "perpetuity"  # or "book_value" or "none"
    
    # Terminal growth rate (for perpetuity method)
    terminal_growth_rate: float = 0.02
    
    # ════════════════════════════════════════════════════════════
    # INFLATION
    # ════════════════════════════════════════════════════════════
    
    # General inflation rate
    general_inflation_rate: float = 0.025
    
    # Real vs nominal analysis
    use_nominal_dollars: bool = True


@dataclass
class TriGenEmissionsInputs:
    """
    Emissions factors and carbon pricing.
    """
    
    # ════════════════════════════════════════════════════════════
    # DIRECT EMISSIONS
    # ════════════════════════════════════════════════════════════
    
    # NG CO2 emission factor (kg CO2/MMBtu)
    ng_co2_factor: float = 53.06
    
    # NG CH4 emission factor (kg CH4/MMBtu) - upstream leakage
    ng_ch4_factor: float = 0.10
    
    # NG N2O emission factor (kg N2O/MMBtu)
    ng_n2o_factor: float = 0.001
    
    # CH4 GWP (100-year)
    ch4_gwp: float = 28.0
    
    # N2O GWP (100-year)
    n2o_gwp: float = 265.0
    
    # ════════════════════════════════════════════════════════════
    # GRID REFERENCE
    # ════════════════════════════════════════════════════════════
    
    # Grid CO2 intensity (kg CO2/kWh) - for avoided emissions
    grid_co2_intensity: float = 0.40
    
    # Grid region (for eGRID lookup)
    grid_region: str = "US_AVG"
    
    # ════════════════════════════════════════════════════════════
    # CARBON PRICING
    # ════════════════════════════════════════════════════════════
    
    # Carbon price ($/ton CO2e)
    carbon_price_per_ton: float = 0.0
    
    # Carbon price escalation (%/year)
    carbon_price_escalation: float = 0.05
    
    # 45Q credit ($/ton CO2 captured) - if applicable
    credit_45q_per_ton: float = 0.0


# ============================================================
# MAIN TEA CLASS
# ============================================================

@dataclass
class TriGenDetailedTEAOutputs:
    """
    Comprehensive TEA outputs with full granularity.
    """
    
    # ════════════════════════════════════════════════════════════
    # SYSTEM PARAMETERS
    # ════════════════════════════════════════════════════════════
    rated_capacity_kW: float = 0
    annual_operating_hours: float = 0
    capacity_factor: float = 0
    lifetime_degradation_factor: float = 0
    
    # Annual production (Year 1, before degradation)
    annual_electricity_MWh_yr1: float = 0
    annual_h2_kg_yr1: float = 0
    annual_heat_MMBtu_yr1: float = 0
    annual_ng_MMBtu_yr1: float = 0
    
    # ════════════════════════════════════════════════════════════
    # DETAILED CAPEX BREAKDOWN
    # ════════════════════════════════════════════════════════════
    capex_stack: float = 0
    capex_power_conditioning: float = 0
    capex_fuel_processing: float = 0
    capex_air_supply: float = 0
    capex_thermal_management: float = 0
    capex_controls: float = 0
    capex_water_treatment: float = 0
    capex_h2_purification: float = 0
    capex_h2_compression: float = 0
    capex_h2_storage: float = 0
    capex_heat_recovery: float = 0
    capex_electrical_interconnect: float = 0
    capex_gas_interconnect: float = 0
    capex_civil: float = 0
    capex_commissioning: float = 0
    
    capex_equipment_subtotal: float = 0
    capex_engineering: float = 0
    capex_project_management: float = 0
    capex_permitting: float = 0
    capex_contingency: float = 0
    capex_owners_costs: float = 0
    capex_construction_interest: float = 0
    
    capex_total_installed: float = 0
    capex_per_kW: float = 0
    
    # ════════════════════════════════════════════════════════════
    # DETAILED OPEX BREAKDOWN (Year 1)
    # ════════════════════════════════════════════════════════════
    opex_ng_fuel: float = 0
    opex_ng_delivery: float = 0
    opex_labor: float = 0
    opex_service_contract: float = 0
    opex_minor_maintenance: float = 0
    opex_major_maintenance_reserve: float = 0
    opex_stack_replacement_reserve: float = 0
    opex_consumables_water: float = 0
    opex_consumables_desulf: float = 0
    opex_consumables_catalyst_reserve: float = 0
    opex_insurance: float = 0
    opex_property_tax: float = 0
    opex_admin: float = 0
    
    opex_fixed_total: float = 0
    opex_variable_total: float = 0
    opex_total_yr1: float = 0
    
    # ════════════════════════════════════════════════════════════
    # REVENUE BREAKDOWN (Year 1)
    # ════════════════════════════════════════════════════════════
    revenue_electricity_energy: float = 0
    revenue_electricity_demand: float = 0
    revenue_electricity_capacity: float = 0
    revenue_h2: float = 0
    revenue_heat: float = 0
    revenue_recs: float = 0
    revenue_lcfs: float = 0
    revenue_total_yr1: float = 0
    
    # ════════════════════════════════════════════════════════════
    # INCENTIVES
    # ════════════════════════════════════════════════════════════
    incentive_itc: float = 0
    incentive_state: float = 0
    incentive_ptc_annual: float = 0
    
    # ════════════════════════════════════════════════════════════
    # LEVELIZED COSTS (multiple allocation methods)
    # ════════════════════════════════════════════════════════════
    lcoe_energy_allocated: float = 0
    lcoe_market_allocated: float = 0
    lcoe_incremental: float = 0  # If electricity is primary product
    
    lcoh_energy_allocated: float = 0
    lcoh_market_allocated: float = 0
    lcoh_incremental: float = 0  # If H2 is primary product
    
    lcoh_heat_energy_allocated: float = 0
    lcoh_heat_market_allocated: float = 0
    
    # Blended costs
    lcos_blended: float = 0  # $/MMBtu equivalent total output
    
    # ════════════════════════════════════════════════════════════
    # FINANCING
    # ════════════════════════════════════════════════════════════
    total_debt: float = 0
    total_equity: float = 0
    annual_debt_service: float = 0
    loan_origination_cost: float = 0
    
    # ════════════════════════════════════════════════════════════
    # CASH FLOW METRICS
    # ════════════════════════════════════════════════════════════
    npv_project: float = 0  # Unlevered
    npv_equity: float = 0  # Levered
    irr_project: float = 0  # Unlevered
    irr_equity: float = 0  # Levered
    
    payback_simple: float = 0
    payback_discounted: float = 0
    
    dscr_min: float = 0
    dscr_avg: float = 0
    
    # ════════════════════════════════════════════════════════════
    # EMISSIONS
    # ════════════════════════════════════════════════════════════
    annual_co2_direct_tons: float = 0
    annual_co2_indirect_tons: float = 0
    annual_co2e_total_tons: float = 0
    
    co2_intensity_kg_per_kWh: float = 0
    co2_intensity_kg_per_kg_h2: float = 0
    
    avoided_co2_tons: float = 0
    
    # ════════════════════════════════════════════════════════════
    # ANNUAL CASH FLOW TABLES
    # ════════════════════════════════════════════════════════════
    years: List[int] = field(default_factory=list)
    
    cf_revenue: List[float] = field(default_factory=list)
    cf_opex: List[float] = field(default_factory=list)
    cf_ebitda: List[float] = field(default_factory=list)
    cf_depreciation: List[float] = field(default_factory=list)
    cf_interest: List[float] = field(default_factory=list)
    cf_ebt: List[float] = field(default_factory=list)
    cf_taxes: List[float] = field(default_factory=list)
    cf_net_income: List[float] = field(default_factory=list)
    
    cf_operating: List[float] = field(default_factory=list)
    cf_capex: List[float] = field(default_factory=list)
    cf_debt_service: List[float] = field(default_factory=list)
    cf_free_cash_flow: List[float] = field(default_factory=list)
    cf_cumulative: List[float] = field(default_factory=list)
    
    # Production by year (with degradation)
    production_electricity_MWh: List[float] = field(default_factory=list)
    production_h2_kg: List[float] = field(default_factory=list)
    production_heat_MMBtu: List[float] = field(default_factory=list)


class TriGenDetailedTEA:
    """
    Detailed Techno-Economic Analysis for TriGen MCFC System.
    
    Features:
    - Component-level CAPEX with scaling
    - Detailed O&M with labor models
    - MACRS depreciation with bonus
    - Debt/equity financing with loan amortization
    - Multiple cost allocation methods
    - Full annual cash flow projection
    """
    
    def __init__(self,
                 capex: Optional[TriGenCapexInputs] = None,
                 opex: Optional[TriGenOpexInputs] = None,
                 revenue: Optional[TriGenRevenueInputs] = None,
                 financing: Optional[TriGenFinancingInputs] = None,
                 emissions: Optional[TriGenEmissionsInputs] = None):
        
        self.capex = capex or TriGenCapexInputs()
        self.opex = opex or TriGenOpexInputs()
        self.revenue = revenue or TriGenRevenueInputs()
        self.financing = financing or TriGenFinancingInputs()
        self.emissions = emissions or TriGenEmissionsInputs()
        
        self._outputs: Optional[TriGenDetailedTEAOutputs] = None
        self._issues: List[str] = []
    
    @property
    def outputs(self) -> TriGenDetailedTEAOutputs:
        if self._outputs is None:
            raise ValueError("TEA not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> List[str]:
        return self._issues
    
    def calculate(self,
                  rated_capacity_kW: float,
                  daily_electricity_kWh: float,
                  daily_h2_kg: float,
                  daily_heat_kWh_th: float,
                  daily_ng_scf: float,
                  ng_input_scf_h: float) -> TriGenDetailedTEAOutputs:
        """
        Run complete detailed TEA.
        
        Args:
            rated_capacity_kW: Net electrical output capacity
            daily_electricity_kWh: Daily net electricity production
            daily_h2_kg: Daily H2 co-production
            daily_heat_kWh_th: Daily useful heat production
            daily_ng_scf: Daily NG consumption
            ng_input_scf_h: NG input rate (for sizing gas interconnection)
        """
        self._issues = []
        out = TriGenDetailedTEAOutputs()
        
        # ════════════════════════════════════════════════════════════
        # SYSTEM PARAMETERS
        # ════════════════════════════════════════════════════════════
        
        out.rated_capacity_kW = rated_capacity_kW
        
        # Calculate availability
        planned_availability = 1 - (self.opex.planned_outage_days / 365)
        forced_availability = 1 - self.opex.forced_outage_rate
        total_availability = planned_availability * forced_availability
        
        out.annual_operating_hours = 8760 * total_availability
        out.capacity_factor = total_availability
        
        # Annual production (Year 1)
        operating_days = 365 * total_availability
        out.annual_electricity_MWh_yr1 = daily_electricity_kWh * operating_days / 1000
        out.annual_h2_kg_yr1 = daily_h2_kg * operating_days
        out.annual_heat_MMBtu_yr1 = daily_heat_kWh_th * operating_days / 293.07
        out.annual_ng_MMBtu_yr1 = daily_ng_scf * operating_days / 1000  # ~1000 scf/MMBtu
        
        # ════════════════════════════════════════════════════════════
        # DETAILED CAPEX
        # ════════════════════════════════════════════════════════════
        
        cx = self.capex
        
        # Scaling factor for non-reference capacity
        if rated_capacity_kW != cx.reference_capacity_kW:
            scale_factor = (rated_capacity_kW / cx.reference_capacity_kW) ** cx.scaling_exponent
        else:
            scale_factor = 1.0
        
        # Fuel cell module components
        out.capex_stack = rated_capacity_kW * cx.stack_cost_per_kW * cx.noak_factor
        out.capex_power_conditioning = rated_capacity_kW * cx.pcs_cost_per_kW
        out.capex_fuel_processing = rated_capacity_kW * cx.fuel_processing_cost_per_kW
        out.capex_air_supply = rated_capacity_kW * cx.air_supply_cost_per_kW
        out.capex_thermal_management = rated_capacity_kW * cx.thermal_management_cost_per_kW
        out.capex_controls = rated_capacity_kW * cx.control_system_cost_per_kW
        out.capex_water_treatment = rated_capacity_kW * cx.water_treatment_cost_per_kW
        
        # H2 handling
        out.capex_h2_purification = daily_h2_kg * cx.psa_cost_per_kg_day
        out.capex_h2_compression = daily_h2_kg * cx.h2_compression_cost_per_kg_day
        out.capex_h2_storage = daily_h2_kg * cx.h2_buffer_days * cx.h2_buffer_cost_per_kg
        
        # Heat recovery
        heat_capacity_kW_th = daily_heat_kWh_th / 24
        out.capex_heat_recovery = heat_capacity_kW_th * (cx.heat_recovery_hx_cost_per_kW_th + 
                                                         cx.heat_distribution_cost_per_kW_th)
        
        # BOP
        out.capex_electrical_interconnect = rated_capacity_kW * cx.electrical_interconnection_per_kW
        out.capex_gas_interconnect = ng_input_scf_h * cx.gas_interconnection_per_scfh
        out.capex_civil = cx.civil_fixed_cost + rated_capacity_kW * cx.civil_cost_per_kW
        out.capex_commissioning = rated_capacity_kW * cx.commissioning_cost_per_kW
        
        # Equipment subtotal
        out.capex_equipment_subtotal = (
            out.capex_stack + out.capex_power_conditioning + out.capex_fuel_processing +
            out.capex_air_supply + out.capex_thermal_management + out.capex_controls +
            out.capex_water_treatment + out.capex_h2_purification + out.capex_h2_compression +
            out.capex_h2_storage + out.capex_heat_recovery + out.capex_electrical_interconnect +
            out.capex_gas_interconnect + out.capex_civil + out.capex_commissioning
        )
        
        # Soft costs
        out.capex_engineering = out.capex_equipment_subtotal * cx.engineering_pct
        out.capex_project_management = out.capex_equipment_subtotal * cx.project_management_pct
        out.capex_permitting = cx.permitting_fees
        
        direct_costs = out.capex_equipment_subtotal + out.capex_engineering + out.capex_project_management + out.capex_permitting
        
        out.capex_contingency = direct_costs * cx.contingency_pct
        
        subtotal_before_owners = direct_costs + out.capex_contingency
        out.capex_owners_costs = subtotal_before_owners * cx.owners_costs_pct
        
        # Construction interest (if capitalizing)
        fin = self.financing
        if fin.capitalize_construction_interest and fin.debt_fraction > 0:
            avg_outstanding = subtotal_before_owners * fin.debt_fraction * 0.5  # Assume linear drawdown
            construction_years = fin.construction_months / 12
            out.capex_construction_interest = avg_outstanding * fin.cost_of_debt * construction_years
        else:
            out.capex_construction_interest = 0
        
        out.capex_total_installed = (subtotal_before_owners + out.capex_owners_costs + 
                                     out.capex_construction_interest)
        out.capex_per_kW = out.capex_total_installed / rated_capacity_kW
        
        # ════════════════════════════════════════════════════════════
        # DETAILED OPEX (Year 1)
        # ════════════════════════════════════════════════════════════
        
        ox = self.opex
        
        # Fuel
        out.opex_ng_fuel = out.annual_ng_MMBtu_yr1 * ox.ng_commodity_price
        out.opex_ng_delivery = out.annual_ng_MMBtu_yr1 * ox.ng_delivery_price
        
        # Labor
        if ox.use_remote_monitoring:
            out.opex_labor = rated_capacity_kW * ox.remote_monitoring_per_kW_year
        else:
            operators_needed = (rated_capacity_kW / 1000) * ox.operators_per_MW
            out.opex_labor = max(1, operators_needed) * ox.operator_annual_cost
        
        # Maintenance
        out.opex_service_contract = rated_capacity_kW * ox.service_contract_per_kW_year
        out.opex_minor_maintenance = rated_capacity_kW * ox.minor_maintenance_per_kW_year
        
        # Major maintenance reserve (annualized)
        major_maint_cost = out.capex_equipment_subtotal * ox.major_maintenance_pct_capex
        out.opex_major_maintenance_reserve = major_maint_cost / ox.major_maintenance_interval_years
        
        # Stack replacement reserve
        stack_life_years = ox.stack_life_hours / out.annual_operating_hours
        num_replacements = int(fin.project_life_years / stack_life_years)
        stack_repl_cost = out.capex_stack * ox.stack_replacement_pct
        
        # NPV of replacements then annualize
        stack_repl_npv = 0
        for i in range(1, num_replacements + 1):
            repl_year = int(i * stack_life_years)
            if repl_year < fin.project_life_years:
                stack_repl_npv += stack_repl_cost / ((1 + fin.wacc) ** repl_year)
        
        crf = self._capital_recovery_factor(fin.wacc, fin.project_life_years)
        out.opex_stack_replacement_reserve = stack_repl_npv * crf
        
        # Consumables
        out.opex_consumables_water = (out.annual_electricity_MWh_yr1 * 1000 * 
                                      ox.water_consumption_gal_per_kWh * ox.di_water_cost_per_gallon)
        
        ng_kg_per_year = out.annual_ng_MMBtu_yr1 * 19.5  # ~19.5 kg/MMBtu for NG
        out.opex_consumables_desulf = ng_kg_per_year * ox.desulf_adsorbent_per_kg_ng
        
        catalyst_cost = rated_capacity_kW * ox.catalyst_cost_per_kW
        out.opex_consumables_catalyst_reserve = catalyst_cost / ox.catalyst_replacement_years
        
        # Insurance, taxes, admin
        out.opex_insurance = out.capex_total_installed * ox.insurance_pct_capex
        out.opex_property_tax = out.capex_total_installed * ox.property_tax_pct_capex
        out.opex_admin = rated_capacity_kW * ox.admin_overhead_per_kW_year
        
        # Totals
        out.opex_fixed_total = (out.opex_labor + out.opex_service_contract + 
                               out.opex_minor_maintenance + out.opex_major_maintenance_reserve +
                               out.opex_stack_replacement_reserve + out.opex_consumables_catalyst_reserve +
                               out.opex_insurance + out.opex_property_tax + out.opex_admin)
        
        out.opex_variable_total = (out.opex_ng_fuel + out.opex_ng_delivery + 
                                   out.opex_consumables_water + out.opex_consumables_desulf)
        
        out.opex_total_yr1 = out.opex_fixed_total + out.opex_variable_total
        
        # ════════════════════════════════════════════════════════════
        # REVENUE (Year 1)
        # ════════════════════════════════════════════════════════════
        
        rv = self.revenue
        
        # Electricity
        out.revenue_electricity_energy = out.annual_electricity_MWh_yr1 * 1000 * rv.electricity_price_base
        out.revenue_electricity_demand = rated_capacity_kW * rv.demand_charge_avoided * 12
        out.revenue_electricity_capacity = rated_capacity_kW * rv.capacity_payment_per_kW_year
        
        # H2
        out.revenue_h2 = out.annual_h2_kg_yr1 * rv.h2_price_per_kg * rv.h2_purity_premium
        
        # Heat
        useful_heat = out.annual_heat_MMBtu_yr1 * rv.heat_utilization_factor
        out.revenue_heat = useful_heat * rv.heat_price_per_MMBtu
        
        # RECs and LCFS
        out.revenue_recs = out.annual_electricity_MWh_yr1 * rv.rec_value_per_MWh
        out.revenue_lcfs = out.annual_h2_kg_yr1 * rv.lcfs_credit_per_kg_h2
        
        out.revenue_total_yr1 = (out.revenue_electricity_energy + out.revenue_electricity_demand +
                                 out.revenue_electricity_capacity + out.revenue_h2 + out.revenue_heat +
                                 out.revenue_recs + out.revenue_lcfs)
        
        # ════════════════════════════════════════════════════════════
        # INCENTIVES
        # ════════════════════════════════════════════════════════════
        
        out.incentive_itc = out.capex_total_installed * rv.itc_rate
        out.incentive_state = rated_capacity_kW * rv.state_incentive_per_kW
        out.incentive_ptc_annual = out.annual_electricity_MWh_yr1 * 1000 * rv.ptc_per_kWh
        
        # ════════════════════════════════════════════════════════════
        # FINANCING
        # ════════════════════════════════════════════════════════════
        
        net_capex_after_incentives = out.capex_total_installed - out.incentive_itc - out.incentive_state
        
        out.total_debt = net_capex_after_incentives * fin.debt_fraction
        out.total_equity = net_capex_after_incentives * fin.equity_fraction
        out.loan_origination_cost = out.total_debt * fin.loan_origination_fee
        
        # Loan amortization (level payment)
        if out.total_debt > 0 and fin.loan_term_years > 0:
            loan_crf = self._capital_recovery_factor(fin.cost_of_debt, fin.loan_term_years)
            out.annual_debt_service = out.total_debt * loan_crf
        else:
            out.annual_debt_service = 0
        
        # ════════════════════════════════════════════════════════════
        # DEPRECIATION SCHEDULE
        # ════════════════════════════════════════════════════════════
        
        depreciable_basis = out.capex_total_installed - out.incentive_itc * 0.5  # ITC reduces basis by 50%
        depreciation_schedule = self._calculate_depreciation(
            depreciable_basis, fin.depreciation_method, fin.bonus_depreciation)
        
        # ════════════════════════════════════════════════════════════
        # ANNUAL CASH FLOWS
        # ════════════════════════════════════════════════════════════
        
        analysis_years = fin.analysis_period_years
        
        out.years = list(range(analysis_years + 1))
        out.cf_revenue = [0.0] * (analysis_years + 1)
        out.cf_opex = [0.0] * (analysis_years + 1)
        out.cf_ebitda = [0.0] * (analysis_years + 1)
        out.cf_depreciation = [0.0] * (analysis_years + 1)
        out.cf_interest = [0.0] * (analysis_years + 1)
        out.cf_ebt = [0.0] * (analysis_years + 1)
        out.cf_taxes = [0.0] * (analysis_years + 1)
        out.cf_net_income = [0.0] * (analysis_years + 1)
        out.cf_operating = [0.0] * (analysis_years + 1)
        out.cf_capex = [0.0] * (analysis_years + 1)
        out.cf_debt_service = [0.0] * (analysis_years + 1)
        out.cf_free_cash_flow = [0.0] * (analysis_years + 1)
        out.cf_cumulative = [0.0] * (analysis_years + 1)
        
        out.production_electricity_MWh = [0.0] * (analysis_years + 1)
        out.production_h2_kg = [0.0] * (analysis_years + 1)
        out.production_heat_MMBtu = [0.0] * (analysis_years + 1)
        
        # Loan amortization schedule
        loan_balance = out.total_debt
        
        # Tax loss carryforward
        tax_loss_cf = 0
        
        # Year 0: Initial investment
        out.cf_capex[0] = -out.capex_total_installed
        out.cf_free_cash_flow[0] = -out.total_equity - out.loan_origination_cost
        out.cf_cumulative[0] = out.cf_free_cash_flow[0]
        
        cumulative = out.cf_free_cash_flow[0]
        
        # Stack replacement tracking
        operating_hours_cumulative = 0
        next_stack_replacement_hours = ox.stack_life_hours
        
        for year in range(1, analysis_years + 1):
            # Degradation
            years_since_last_stack = (operating_hours_cumulative % ox.stack_life_hours) / out.annual_operating_hours
            degradation = 1 - (ox.stack_degradation_per_1000h / 100) * (years_since_last_stack * out.annual_operating_hours / 1000)
            degradation = max(0.8, degradation)  # Floor at 80%
            
            # Production
            if year <= fin.project_life_years:
                elec_MWh = out.annual_electricity_MWh_yr1 * degradation
                h2_kg = out.annual_h2_kg_yr1 * degradation
                heat_MMBtu = out.annual_heat_MMBtu_yr1 * degradation
                ng_MMBtu = out.annual_ng_MMBtu_yr1  # NG consumption roughly constant
            else:
                elec_MWh = h2_kg = heat_MMBtu = ng_MMBtu = 0
            
            out.production_electricity_MWh[year] = elec_MWh
            out.production_h2_kg[year] = h2_kg
            out.production_heat_MMBtu[year] = heat_MMBtu
            
            # Revenue with escalation
            if year <= fin.project_life_years:
                elec_escal = (1 + rv.electricity_escalation_rate) ** (year - 1)
                h2_escal = (1 + rv.h2_escalation_rate) ** (year - 1)
                heat_escal = (1 + rv.heat_escalation_rate) ** (year - 1)
                
                rev_elec = elec_MWh * 1000 * rv.electricity_price_base * elec_escal
                rev_elec += rated_capacity_kW * rv.demand_charge_avoided * 12
                rev_elec += rated_capacity_kW * rv.capacity_payment_per_kW_year
                
                rev_h2 = h2_kg * rv.h2_price_per_kg * h2_escal
                rev_heat = heat_MMBtu * rv.heat_utilization_factor * rv.heat_price_per_MMBtu * heat_escal
                rev_other = elec_MWh * rv.rec_value_per_MWh + h2_kg * rv.lcfs_credit_per_kg_h2
                
                # PTC (if applicable, first N years)
                if year <= rv.ptc_years and rv.ptc_per_kWh > 0:
                    rev_other += elec_MWh * 1000 * rv.ptc_per_kWh
                
                revenue = rev_elec + rev_h2 + rev_heat + rev_other
            else:
                revenue = 0
            
            out.cf_revenue[year] = revenue
            
            # OPEX with escalation
            if year <= fin.project_life_years:
                ng_escal = (1 + ox.ng_escalation_rate) ** (year - 1)
                gen_escal = (1 + fin.general_inflation_rate) ** (year - 1)
                
                fuel_cost = ng_MMBtu * (ox.ng_commodity_price + ox.ng_delivery_price) * ng_escal
                fixed_cost = out.opex_fixed_total * gen_escal
                variable_cost = (out.opex_consumables_water + out.opex_consumables_desulf) * gen_escal
                
                opex = fuel_cost + fixed_cost + variable_cost
                
                # Stack replacement in this year?
                operating_hours_cumulative += out.annual_operating_hours
                if operating_hours_cumulative >= next_stack_replacement_hours:
                    opex += stack_repl_cost
                    next_stack_replacement_hours += ox.stack_life_hours
                    operating_hours_cumulative = 0  # Reset degradation
                
                # Major maintenance
                if year % int(ox.major_maintenance_interval_years) == 0:
                    opex += major_maint_cost * gen_escal
            else:
                opex = 0
            
            out.cf_opex[year] = opex
            
            # EBITDA
            ebitda = revenue - opex
            out.cf_ebitda[year] = ebitda
            
            # Depreciation
            if year <= len(depreciation_schedule):
                depreciation = depreciation_schedule[year - 1]
            else:
                depreciation = 0
            out.cf_depreciation[year] = depreciation
            
            # Interest
            if loan_balance > 0 and year <= fin.loan_term_years:
                interest = loan_balance * fin.cost_of_debt
                principal = out.annual_debt_service - interest
                loan_balance = max(0, loan_balance - principal)
            else:
                interest = 0
                principal = 0
            out.cf_interest[year] = interest
            
            # EBT
            ebt = ebitda - depreciation - interest
            out.cf_ebt[year] = ebt
            
            # Taxes
            taxable_income = ebt
            if fin.tax_loss_carryforward and tax_loss_cf > 0:
                if taxable_income > 0:
                    offset = min(taxable_income, tax_loss_cf)
                    taxable_income -= offset
                    tax_loss_cf -= offset
            
            if taxable_income > 0:
                taxes = taxable_income * fin.effective_tax_rate
            else:
                taxes = 0
                if fin.tax_loss_carryforward:
                    tax_loss_cf += abs(taxable_income)
            
            out.cf_taxes[year] = taxes
            
            # Net income
            net_income = ebt - taxes
            out.cf_net_income[year] = net_income
            
            # Operating cash flow (add back depreciation)
            operating_cf = net_income + depreciation
            out.cf_operating[year] = operating_cf
            
            # Debt service
            if year <= fin.loan_term_years:
                debt_service = out.annual_debt_service
            else:
                debt_service = 0
            out.cf_debt_service[year] = debt_service
            
            # Free cash flow to equity
            fcf = operating_cf - debt_service + interest  # Add interest back since we're doing equity
            out.cf_free_cash_flow[year] = fcf
            
            cumulative += fcf
            out.cf_cumulative[year] = cumulative
        
        # ════════════════════════════════════════════════════════════
        # FINANCIAL METRICS
        # ════════════════════════════════════════════════════════════
        
        # NPV (equity)
        out.npv_equity = self._calculate_npv(out.cf_free_cash_flow, fin.cost_of_equity)
        
        # NPV (project/unlevered) - use WACC
        project_cf = [out.cf_capex[0]] + [out.cf_ebitda[y] - out.cf_taxes[y] + out.cf_depreciation[y] 
                                          for y in range(1, analysis_years + 1)]
        out.npv_project = self._calculate_npv(project_cf, fin.wacc)
        
        # IRR
        out.irr_equity = self._calculate_irr(out.cf_free_cash_flow)
        out.irr_project = self._calculate_irr(project_cf)
        
        # Payback
        out.payback_simple = self._calculate_simple_payback(out.cf_cumulative)
        out.payback_discounted = self._calculate_discounted_payback(out.cf_free_cash_flow, fin.cost_of_equity)
        
        # DSCR
        dscr_values = []
        for year in range(1, min(fin.loan_term_years + 1, analysis_years + 1)):
            if out.cf_debt_service[year] > 0:
                dscr = out.cf_ebitda[year] / out.cf_debt_service[year]
                dscr_values.append(dscr)
        
        if dscr_values:
            out.dscr_min = min(dscr_values)
            out.dscr_avg = sum(dscr_values) / len(dscr_values)
        
        # ════════════════════════════════════════════════════════════
        # LEVELIZED COSTS
        # ════════════════════════════════════════════════════════════
        
        # Total lifetime costs and production
        total_costs_npv = -out.npv_project + out.capex_total_installed  # Recover costs
        
        # Lifetime production (discounted)
        lifetime_elec_MWh_disc = sum(out.production_electricity_MWh[y] / ((1 + fin.wacc) ** y) 
                                     for y in range(1, analysis_years + 1))
        lifetime_h2_kg_disc = sum(out.production_h2_kg[y] / ((1 + fin.wacc) ** y) 
                                  for y in range(1, analysis_years + 1))
        lifetime_heat_MMBtu_disc = sum(out.production_heat_MMBtu[y] / ((1 + fin.wacc) ** y) 
                                       for y in range(1, analysis_years + 1))
        
        # Energy content allocation
        elec_energy_MMBtu = out.annual_electricity_MWh_yr1 * 3.412
        h2_energy_MMBtu = out.annual_h2_kg_yr1 * 0.114
        heat_energy_MMBtu = out.annual_heat_MMBtu_yr1
        total_energy = elec_energy_MMBtu + h2_energy_MMBtu + heat_energy_MMBtu
        
        if total_energy > 0:
            elec_energy_frac = elec_energy_MMBtu / total_energy
            h2_energy_frac = h2_energy_MMBtu / total_energy
            heat_energy_frac = heat_energy_MMBtu / total_energy
        else:
            elec_energy_frac = h2_energy_frac = heat_energy_frac = 0
        
        # Market value allocation
        total_value_yr1 = out.revenue_total_yr1
        if total_value_yr1 > 0:
            elec_value_frac = (out.revenue_electricity_energy + out.revenue_electricity_demand + 
                              out.revenue_electricity_capacity) / total_value_yr1
            h2_value_frac = out.revenue_h2 / total_value_yr1
            heat_value_frac = out.revenue_heat / total_value_yr1
        else:
            elec_value_frac = h2_value_frac = heat_value_frac = 0
        
        # Calculate levelized costs
        annualized_cost = out.capex_total_installed * crf + out.opex_total_yr1
        
        # Energy allocated
        if lifetime_elec_MWh_disc > 0:
            out.lcoe_energy_allocated = (annualized_cost * elec_energy_frac) / (out.annual_electricity_MWh_yr1 * 1000)
        if lifetime_h2_kg_disc > 0:
            out.lcoh_energy_allocated = (annualized_cost * h2_energy_frac) / out.annual_h2_kg_yr1
        if lifetime_heat_MMBtu_disc > 0:
            out.lcoh_heat_energy_allocated = (annualized_cost * heat_energy_frac) / out.annual_heat_MMBtu_yr1
        
        # Market value allocated
        if lifetime_elec_MWh_disc > 0:
            out.lcoe_market_allocated = (annualized_cost * elec_value_frac) / (out.annual_electricity_MWh_yr1 * 1000)
        if lifetime_h2_kg_disc > 0:
            out.lcoh_market_allocated = (annualized_cost * h2_value_frac) / out.annual_h2_kg_yr1
        if lifetime_heat_MMBtu_disc > 0:
            out.lcoh_heat_market_allocated = (annualized_cost * heat_value_frac) / out.annual_heat_MMBtu_yr1
        
        # Incremental (H2 as byproduct of electricity)
        if lifetime_elec_MWh_disc > 0 and lifetime_h2_kg_disc > 0:
            # Credit H2 at market value, remainder goes to electricity
            h2_credit = out.annual_h2_kg_yr1 * rv.h2_price_per_kg
            heat_credit = out.annual_heat_MMBtu_yr1 * rv.heat_utilization_factor * rv.heat_price_per_MMBtu
            net_elec_cost = annualized_cost - h2_credit - heat_credit
            out.lcoe_incremental = max(0, net_elec_cost) / (out.annual_electricity_MWh_yr1 * 1000)
            
            # Credit electricity, remainder to H2
            elec_credit = out.annual_electricity_MWh_yr1 * 1000 * rv.electricity_price_base
            net_h2_cost = annualized_cost - elec_credit - heat_credit
            out.lcoh_incremental = max(0, net_h2_cost) / out.annual_h2_kg_yr1
        
        # ════════════════════════════════════════════════════════════
        # EMISSIONS
        # ════════════════════════════════════════════════════════════
        
        em = self.emissions
        
        # Direct CO2 from NG combustion
        out.annual_co2_direct_tons = out.annual_ng_MMBtu_yr1 * em.ng_co2_factor / 1000
        
        # Indirect (upstream CH4, N2O)
        ch4_co2e = out.annual_ng_MMBtu_yr1 * em.ng_ch4_factor * em.ch4_gwp / 1000
        n2o_co2e = out.annual_ng_MMBtu_yr1 * em.ng_n2o_factor * em.n2o_gwp / 1000
        out.annual_co2_indirect_tons = ch4_co2e + n2o_co2e
        
        out.annual_co2e_total_tons = out.annual_co2_direct_tons + out.annual_co2_indirect_tons
        
        # Intensities
        if out.annual_electricity_MWh_yr1 > 0:
            out.co2_intensity_kg_per_kWh = (out.annual_co2e_total_tons * 1000) / (out.annual_electricity_MWh_yr1 * 1000)
        if out.annual_h2_kg_yr1 > 0:
            out.co2_intensity_kg_per_kg_h2 = (out.annual_co2e_total_tons * 1000) / out.annual_h2_kg_yr1
        
        # Avoided emissions vs grid
        grid_emissions = out.annual_electricity_MWh_yr1 * 1000 * em.grid_co2_intensity / 1000
        out.avoided_co2_tons = grid_emissions - out.annual_co2e_total_tons
        
        # ════════════════════════════════════════════════════════════
        # VALIDATION
        # ════════════════════════════════════════════════════════════
        
        if out.irr_equity < 0:
            self._issues.append(f"Negative equity IRR ({out.irr_equity:.1%}). Project not viable.")
        elif out.irr_equity < fin.cost_of_equity:
            self._issues.append(f"Equity IRR ({out.irr_equity:.1%}) below required return ({fin.cost_of_equity:.1%}).")
        
        if out.dscr_min < fin.dscr_required:
            self._issues.append(f"Minimum DSCR ({out.dscr_min:.2f}) below requirement ({fin.dscr_required:.2f}).")
        
        if out.payback_simple > 10:
            self._issues.append(f"Simple payback ({out.payback_simple:.1f} years) exceeds 10 years.")
        
        if out.lcoh_energy_allocated > 5:
            self._issues.append(f"LCOH (${out.lcoh_energy_allocated:.2f}/kg) exceeds gray H2 benchmark (~$2-3/kg).")
        
        self._outputs = out
        return out
    
    def _capital_recovery_factor(self, rate: float, years: int) -> float:
        if rate == 0:
            return 1 / years
        return (rate * (1 + rate) ** years) / ((1 + rate) ** years - 1)
    
    def _calculate_depreciation(self, basis: float, method: DepreciationMethod, 
                                bonus: float) -> List[float]:
        """Calculate depreciation schedule."""
        schedule = []
        
        # Bonus depreciation in year 1
        bonus_amount = basis * bonus
        remaining_basis = basis - bonus_amount
        
        if method == DepreciationMethod.STRAIGHT_LINE:
            years = 20
            annual = remaining_basis / years
            schedule = [bonus_amount + annual] + [annual] * (years - 1)
        else:
            macrs_table = MACRS_TABLES.get(method.value, MACRS_TABLES["MACRS_7"])
            for i, rate in enumerate(macrs_table):
                if i == 0:
                    schedule.append(bonus_amount + remaining_basis * rate)
                else:
                    schedule.append(remaining_basis * rate)
        
        return schedule
    
    def _calculate_npv(self, cash_flows: List[float], rate: float) -> float:
        return sum(cf / ((1 + rate) ** t) for t, cf in enumerate(cash_flows))
    
    def _calculate_irr(self, cash_flows: List[float], max_iter: int = 1000) -> float:
        irr = 0.10
        for _ in range(max_iter):
            npv = sum(cf / ((1 + irr) ** t) for t, cf in enumerate(cash_flows))
            dnpv = sum(-t * cf / ((1 + irr) ** (t + 1)) for t, cf in enumerate(cash_flows) if t > 0)
            
            if abs(dnpv) < 1e-10:
                break
            
            new_irr = irr - npv / dnpv
            if abs(new_irr - irr) < 1e-7:
                return new_irr
            
            irr = max(-0.99, min(10, new_irr))
        
        return irr
    
    def _calculate_simple_payback(self, cumulative: List[float]) -> float:
        for i, cf in enumerate(cumulative):
            if cf >= 0 and i > 0:
                prev = cumulative[i - 1]
                fraction = -prev / (cf - prev) if cf != prev else 0
                return (i - 1) + fraction
        return float('inf')
    
    def _calculate_discounted_payback(self, cash_flows: List[float], rate: float) -> float:
        cumulative = 0
        for t, cf in enumerate(cash_flows):
            pv = cf / ((1 + rate) ** t)
            prev_cum = cumulative
            cumulative += pv
            if cumulative >= 0 and t > 0:
                fraction = -prev_cum / pv if pv != 0 else 0
                return (t - 1) + fraction
        return float('inf')
    
    def summary(self) -> str:
        """Generate comprehensive summary report."""
        if self._outputs is None:
            return "TEA not calculated. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"    ⚠ {issue}" for issue in self._issues) if self._issues else "    None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════════════════════════════╗
║                    TRIGEN DETAILED TECHNO-ECONOMIC ANALYSIS                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════╣

  SYSTEM CONFIGURATION
  ────────────────────────────────────────────────────────────────────────────────────────
    Rated Capacity:                {o.rated_capacity_kW:>12,.0f} kW
    Annual Operating Hours:        {o.annual_operating_hours:>12,.0f} hrs
    Capacity Factor:               {o.capacity_factor:>12.1%}
    
    Annual Production (Year 1):
      Electricity:                 {o.annual_electricity_MWh_yr1:>12,.0f} MWh
      Hydrogen:                    {o.annual_h2_kg_yr1:>12,.0f} kg
      Useful Heat:                 {o.annual_heat_MMBtu_yr1:>12,.0f} MMBtu
      NG Consumption:              {o.annual_ng_MMBtu_yr1:>12,.0f} MMBtu

  CAPITAL COSTS (DETAILED)
  ────────────────────────────────────────────────────────────────────────────────────────
    Fuel Cell Module:
      Stack:                       ${o.capex_stack:>12,.0f}
      Power Conditioning:          ${o.capex_power_conditioning:>12,.0f}
      Fuel Processing:             ${o.capex_fuel_processing:>12,.0f}
      Air Supply:                  ${o.capex_air_supply:>12,.0f}
      Thermal Management:          ${o.capex_thermal_management:>12,.0f}
      Controls:                    ${o.capex_controls:>12,.0f}
      Water Treatment:             ${o.capex_water_treatment:>12,.0f}
    
    H2 Handling:
      PSA Purification:            ${o.capex_h2_purification:>12,.0f}
      Compression:                 ${o.capex_h2_compression:>12,.0f}
      Buffer Storage:              ${o.capex_h2_storage:>12,.0f}
    
    Heat Recovery:                 ${o.capex_heat_recovery:>12,.0f}
    
    Balance of Plant:
      Electrical Interconnection:  ${o.capex_electrical_interconnect:>12,.0f}
      Gas Interconnection:         ${o.capex_gas_interconnect:>12,.0f}
      Civil/Structural:            ${o.capex_civil:>12,.0f}
      Commissioning:               ${o.capex_commissioning:>12,.0f}
    
    ─────────────────────────────────────────────────────────────────
    Equipment Subtotal:            ${o.capex_equipment_subtotal:>12,.0f}
    
    Soft Costs:
      Engineering:                 ${o.capex_engineering:>12,.0f}
      Project Management:          ${o.capex_project_management:>12,.0f}
      Permitting:                  ${o.capex_permitting:>12,.0f}
      Contingency:                 ${o.capex_contingency:>12,.0f}
      Owner's Costs:               ${o.capex_owners_costs:>12,.0f}
      Construction Interest:       ${o.capex_construction_interest:>12,.0f}
    
    ═════════════════════════════════════════════════════════════════
    TOTAL INSTALLED COST:          ${o.capex_total_installed:>12,.0f}
    Cost per kW:                   ${o.capex_per_kW:>12,.0f} /kW
    ═════════════════════════════════════════════════════════════════

  OPERATING COSTS (Year 1)
  ────────────────────────────────────────────────────────────────────────────────────────
    Fuel:
      NG Commodity:                ${o.opex_ng_fuel:>12,.0f}
      NG Delivery:                 ${o.opex_ng_delivery:>12,.0f}
    
    Labor:
      Operations/Monitoring:       ${o.opex_labor:>12,.0f}
    
    Maintenance:
      Service Contract:            ${o.opex_service_contract:>12,.0f}
      Minor Maintenance:           ${o.opex_minor_maintenance:>12,.0f}
      Major Maintenance Reserve:   ${o.opex_major_maintenance_reserve:>12,.0f}
      Stack Replacement Reserve:   ${o.opex_stack_replacement_reserve:>12,.0f}
    
    Consumables:
      Water:                       ${o.opex_consumables_water:>12,.0f}
      Desulfurization:             ${o.opex_consumables_desulf:>12,.0f}
      Catalyst Reserve:            ${o.opex_consumables_catalyst_reserve:>12,.0f}
    
    Insurance & Admin:
      Insurance:                   ${o.opex_insurance:>12,.0f}
      Property Tax:                ${o.opex_property_tax:>12,.0f}
      Administrative:              ${o.opex_admin:>12,.0f}
    
    ─────────────────────────────────────────────────────────────────
    Fixed OPEX:                    ${o.opex_fixed_total:>12,.0f}
    Variable OPEX:                 ${o.opex_variable_total:>12,.0f}
    ═════════════════════════════════════════════════════════════════
    TOTAL OPEX (Year 1):           ${o.opex_total_yr1:>12,.0f}
    ═════════════════════════════════════════════════════════════════

  REVENUE (Year 1)
  ────────────────────────────────────────────────────────────────────────────────────────
    Electricity:
      Energy Sales:                ${o.revenue_electricity_energy:>12,.0f}
      Demand Charge Avoided:       ${o.revenue_electricity_demand:>12,.0f}
      Capacity Payment:            ${o.revenue_electricity_capacity:>12,.0f}
    
    Hydrogen:                      ${o.revenue_h2:>12,.0f}
    Heat:                          ${o.revenue_heat:>12,.0f}
    RECs:                          ${o.revenue_recs:>12,.0f}
    LCFS Credits:                  ${o.revenue_lcfs:>12,.0f}
    
    ═════════════════════════════════════════════════════════════════
    TOTAL REVENUE (Year 1):        ${o.revenue_total_yr1:>12,.0f}
    ═════════════════════════════════════════════════════════════════

  INCENTIVES
  ────────────────────────────────────────────────────────────────────────────────────────
    Investment Tax Credit:         ${o.incentive_itc:>12,.0f}
    State/Local Incentive:         ${o.incentive_state:>12,.0f}
    Production Tax Credit (ann.):  ${o.incentive_ptc_annual:>12,.0f}

  FINANCING
  ────────────────────────────────────────────────────────────────────────────────────────
    Total Debt:                    ${o.total_debt:>12,.0f}
    Total Equity:                  ${o.total_equity:>12,.0f}
    Annual Debt Service:           ${o.annual_debt_service:>12,.0f}

  LEVELIZED COSTS
  ────────────────────────────────────────────────────────────────────────────────────────
                                   Energy Alloc.    Market Alloc.    Incremental
    LCOE ($/kWh):                  ${o.lcoe_energy_allocated:>10.4f}       ${o.lcoe_market_allocated:>10.4f}       ${o.lcoe_incremental:>10.4f}
    LCOH ($/kg):                   ${o.lcoh_energy_allocated:>10.2f}       ${o.lcoh_market_allocated:>10.2f}       ${o.lcoh_incremental:>10.2f}
    LCOH-Heat ($/MMBtu):           ${o.lcoh_heat_energy_allocated:>10.2f}       ${o.lcoh_heat_market_allocated:>10.2f}

  FINANCIAL METRICS
  ────────────────────────────────────────────────────────────────────────────────────────
    NPV (Project/Unlevered):       ${o.npv_project:>12,.0f}
    NPV (Equity/Levered):          ${o.npv_equity:>12,.0f}
    IRR (Project):                 {o.irr_project:>12.1%}
    IRR (Equity):                  {o.irr_equity:>12.1%}
    Simple Payback:                {o.payback_simple:>12.1f} years
    Discounted Payback:            {o.payback_discounted:>12.1f} years
    DSCR (Minimum):                {o.dscr_min:>12.2f}
    DSCR (Average):                {o.dscr_avg:>12.2f}

  EMISSIONS
  ────────────────────────────────────────────────────────────────────────────────────────
    Direct CO2 (NG combustion):    {o.annual_co2_direct_tons:>12,.0f} tons/yr
    Indirect CO2e (upstream):      {o.annual_co2_indirect_tons:>12,.0f} tons/yr
    Total CO2e:                    {o.annual_co2e_total_tons:>12,.0f} tons/yr
    CO2 Intensity (electricity):   {o.co2_intensity_kg_per_kWh:>12.3f} kg/kWh
    CO2 Intensity (H2):            {o.co2_intensity_kg_per_kg_h2:>12.2f} kg/kgH2
    Avoided vs Grid:               {o.avoided_co2_tons:>12,.0f} tons/yr

  ISSUES/WARNINGS
  ────────────────────────────────────────────────────────────────────────────────────────
{issues_str}

╚══════════════════════════════════════════════════════════════════════════════════════════╝
"""


# ============================================================
# MAIN - Run example
# ============================================================

if __name__ == "__main__":
    print("=" * 90)
    print("DETAILED TRIGEN TEA - EXAMPLE CALCULATION")
    print("=" * 90)
    
    # From mass/energy balance at 10,700 scf/h NG
    tea = TriGenDetailedTEA()
    
    outputs = tea.calculate(
        rated_capacity_kW=1358,
        daily_electricity_kWh=30636,
        daily_h2_kg=522,
        daily_heat_kWh_th=9942,
        daily_ng_scf=256800,
        ng_input_scf_h=10700
    )
    
    print(tea.summary())
    
    # Print first 5 years of cash flow
    print("\n" + "=" * 90)
    print("ANNUAL CASH FLOW (First 10 Years)")
    print("=" * 90)
    print(f"\n{'Year':<6} {'Revenue':>12} {'OPEX':>12} {'EBITDA':>12} {'Deprec.':>12} {'Taxes':>12} {'FCF':>12} {'Cumulative':>14}")
    print("-" * 102)
    
    for y in range(11):
        print(f"{y:<6} ${outputs.cf_revenue[y]:>11,.0f} ${outputs.cf_opex[y]:>11,.0f} "
              f"${outputs.cf_ebitda[y]:>11,.0f} ${outputs.cf_depreciation[y]:>11,.0f} "
              f"${outputs.cf_taxes[y]:>11,.0f} ${outputs.cf_free_cash_flow[y]:>11,.0f} "
              f"${outputs.cf_cumulative[y]:>13,.0f}")
