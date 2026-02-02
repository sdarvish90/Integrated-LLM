"""
Solar PV Techno-Economic Analysis (TEA) Module

Detailed TEA for utility-scale and distributed solar PV systems.

Features:
- Component-level CAPEX (modules, inverters, BOS, soft costs)
- DC/AC ratio optimization
- Degradation modeling (0.5-0.7%/year)
- Fixed and single-axis tracking configurations
- LCOE calculation with multiple methods
- PPA vs merchant pricing
- ITC/PTC incentive modeling

References:
- NREL ATB 2024
- SHARE Model inputs.py
- Lazard LCOE Analysis
- Wood Mackenzie Solar Market Insight
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from enum import Enum
import numpy as np


class SolarConfiguration(Enum):
    """Solar mounting configuration"""
    FIXED_TILT = "fixed_tilt"
    SINGLE_AXIS_TRACKER = "single_axis_tracker"
    DUAL_AXIS_TRACKER = "dual_axis_tracker"
    ROOFTOP = "rooftop"


class SolarModuleType(Enum):
    """Solar module technology"""
    MONO_PERC = "mono_perc"           # Mono PERC (mainstream)
    MONO_TOPCON = "mono_topcon"       # TOPCon (higher efficiency)
    MONO_HJT = "mono_hjt"             # Heterojunction
    BIFACIAL = "bifacial"             # Bifacial modules
    THIN_FILM = "thin_film"           # CdTe or CIGS


@dataclass
class SolarCapexInputs:
    """Detailed CAPEX breakdown for solar PV"""
    
    # ════════════════════════════════════════════════════════════
    # MODULES
    # ════════════════════════════════════════════════════════════
    
    # Module cost ($/Wdc)
    module_cost_per_Wdc: float = 0.25
    
    # Module type affects efficiency and bifacial gain
    module_type: SolarModuleType = SolarModuleType.MONO_PERC
    
    # Bifacial gain (additional energy, not cost)
    bifacial_gain: float = 0.0  # Set to 0.05-0.10 for bifacial
    
    # ════════════════════════════════════════════════════════════
    # INVERTERS
    # ════════════════════════════════════════════════════════════
    
    # Inverter cost ($/Wac)
    inverter_cost_per_Wac: float = 0.05
    
    # DC/AC ratio (typically 1.2-1.4 for trackers)
    dc_ac_ratio: float = 1.15
    
    # ════════════════════════════════════════════════════════════
    # BALANCE OF SYSTEM (BOS)
    # ════════════════════════════════════════════════════════════
    
    # Mounting/racking ($/Wdc)
    racking_cost_per_Wdc: float = 0.08  # Fixed tilt
    tracker_premium_per_Wdc: float = 0.06  # Additional for trackers
    
    # Electrical BOS - wiring, combiners, switchgear ($/Wdc)
    electrical_bos_per_Wdc: float = 0.06
    
    # Structural BOS - foundations, grading ($/Wdc)
    structural_bos_per_Wdc: float = 0.03
    
    # ════════════════════════════════════════════════════════════
    # INSTALLATION
    # ════════════════════════════════════════════════════════════
    
    # Installation labor ($/Wdc)
    installation_labor_per_Wdc: float = 0.10
    
    # Grid interconnection ($/Wac)
    interconnection_per_Wac: float = 0.03
    
    # ════════════════════════════════════════════════════════════
    # SOFT COSTS
    # ════════════════════════════════════════════════════════════
    
    # Engineering & design (% of hardware)
    engineering_pct: float = 0.03
    
    # Permitting & inspection ($/Wdc)
    permitting_per_Wdc: float = 0.01
    
    # Project management (% of hardware)
    project_management_pct: float = 0.02
    
    # Developer margin/overhead (% of total)
    developer_margin_pct: float = 0.05
    
    # Contingency (% of direct costs)
    contingency_pct: float = 0.05
    
    # ════════════════════════════════════════════════════════════
    # LAND
    # ════════════════════════════════════════════════════════════
    
    # Land cost or lease capitalized ($/Wdc)
    land_cost_per_Wdc: float = 0.02
    
    # ════════════════════════════════════════════════════════════
    # SCALING
    # ════════════════════════════════════════════════════════════
    
    # Reference size for scaling (MWdc)
    reference_capacity_MWdc: float = 100
    
    # Scaling exponent (solar has weak economies of scale)
    scaling_exponent: float = 0.95


@dataclass
class SolarOpexInputs:
    """Operating costs for solar PV"""
    
    # ════════════════════════════════════════════════════════════
    # FIXED O&M
    # ════════════════════════════════════════════════════════════
    
    # Fixed O&M ($/kWdc-year) - typical range $10-20
    fixed_om_per_kWdc_year: float = 15
    
    # O&M escalation (%/year)
    om_escalation_rate: float = 0.02
    
    # ════════════════════════════════════════════════════════════
    # VARIABLE O&M
    # ════════════════════════════════════════════════════════════
    
    # Variable O&M ($/MWh) - panel cleaning, minor repairs
    variable_om_per_MWh: float = 0.0  # Often included in fixed
    
    # ════════════════════════════════════════════════════════════
    # COMPONENT REPLACEMENT
    # ════════════════════════════════════════════════════════════
    
    # Inverter replacement (% of inverter CAPEX)
    inverter_replacement_pct: float = 1.0  # Full replacement
    
    # Inverter lifetime (years)
    inverter_lifetime_years: int = 15
    
    # ════════════════════════════════════════════════════════════
    # INSURANCE & OTHER
    # ════════════════════════════════════════════════════════════
    
    # Insurance (% of CAPEX)
    insurance_pct: float = 0.004
    
    # Property tax (% of CAPEX, varies by jurisdiction)
    property_tax_pct: float = 0.01
    
    # Land lease ($/acre-year) if not owned
    land_lease_per_acre_year: float = 500
    
    # Acres per MWdc (5-7 for fixed, 7-10 for trackers)
    acres_per_MWdc: float = 6


@dataclass
class SolarPerformanceInputs:
    """Performance parameters for solar PV"""
    
    # ════════════════════════════════════════════════════════════
    # CAPACITY
    # ════════════════════════════════════════════════════════════
    
    # DC capacity (MWdc)
    capacity_MWdc: float = 100
    
    # AC capacity (calculated from DC/AC ratio)
    @property
    def capacity_MWac(self) -> float:
        return self.capacity_MWdc / self.dc_ac_ratio
    
    dc_ac_ratio: float = 1.15
    
    # ════════════════════════════════════════════════════════════
    # RESOURCE & CONFIGURATION
    # ════════════════════════════════════════════════════════════
    
    # Configuration
    configuration: SolarConfiguration = SolarConfiguration.SINGLE_AXIS_TRACKER
    
    # Capacity factor (location-dependent)
    # Fixed tilt: 15-22%, Single-axis: 22-32%
    capacity_factor: float = 0.26
    
    # Global Horizontal Irradiance (kWh/m²/day) - for reference
    ghi_kWh_m2_day: float = 5.5
    
    # ════════════════════════════════════════════════════════════
    # LOSSES
    # ════════════════════════════════════════════════════════════
    
    # System losses
    soiling_loss: float = 0.02
    shading_loss: float = 0.01
    mismatch_loss: float = 0.02
    wiring_loss: float = 0.02
    inverter_efficiency: float = 0.98
    transformer_loss: float = 0.01
    availability: float = 0.99
    
    @property
    def total_system_loss(self) -> float:
        return 1 - ((1 - self.soiling_loss) * (1 - self.shading_loss) * 
                   (1 - self.mismatch_loss) * (1 - self.wiring_loss) *
                   self.inverter_efficiency * (1 - self.transformer_loss) *
                   self.availability)
    
    # ════════════════════════════════════════════════════════════
    # DEGRADATION
    # ════════════════════════════════════════════════════════════
    
    # Annual degradation rate
    annual_degradation: float = 0.005  # 0.5%/year
    
    # First-year degradation (LID - Light Induced Degradation)
    first_year_degradation: float = 0.02  # 2% in year 1


@dataclass
class SolarFinancialInputs:
    """Financial parameters for solar TEA"""
    
    # Project timeline
    project_lifetime_years: int = 30
    construction_months: int = 12
    
    # Discount rate / WACC
    discount_rate: float = 0.07
    
    # Inflation
    inflation_rate: float = 0.025
    
    # Tax
    tax_rate: float = 0.21
    depreciation_years: int = 5  # MACRS for solar
    
    # ════════════════════════════════════════════════════════════
    # INCENTIVES
    # ════════════════════════════════════════════════════════════
    
    # Investment Tax Credit (ITC)
    itc_rate: float = 0.30
    
    # Production Tax Credit (PTC) - alternative to ITC
    # $/kWh for first 10 years
    ptc_per_kWh: float = 0.0  # Use 0.026 if electing PTC over ITC
    ptc_years: int = 10
    
    # State/local incentives
    state_rebate_per_Wdc: float = 0.0
    
    # ════════════════════════════════════════════════════════════
    # REVENUE
    # ════════════════════════════════════════════════════════════
    
    # PPA price ($/MWh) - if contracted
    ppa_price_per_MWh: float = 40.0
    ppa_escalation_rate: float = 0.01
    
    # Merchant price ($/MWh) - if selling to market
    merchant_price_per_MWh: float = 35.0
    merchant_escalation_rate: float = 0.02
    
    # Use PPA or merchant
    use_ppa: bool = True
    
    # REC value ($/MWh)
    rec_price_per_MWh: float = 5.0
    rec_escalation_rate: float = -0.02  # RECs often decline
    
    # CAPEX deployment
    capex_deployment: Dict[int, float] = field(default_factory=lambda: {
        -1: 0.30,
        0: 0.70,
    })


@dataclass
class SolarTEAOutputs:
    """Complete solar TEA results"""
    
    # ════════════════════════════════════════════════════════════
    # SYSTEM
    # ════════════════════════════════════════════════════════════
    capacity_MWdc: float = 0
    capacity_MWac: float = 0
    dc_ac_ratio: float = 0
    configuration: str = ""
    capacity_factor: float = 0
    
    # ════════════════════════════════════════════════════════════
    # PRODUCTION
    # ════════════════════════════════════════════════════════════
    annual_generation_MWh_yr1: float = 0
    lifetime_generation_MWh: float = 0
    
    # ════════════════════════════════════════════════════════════
    # CAPEX
    # ════════════════════════════════════════════════════════════
    capex_modules: float = 0
    capex_inverters: float = 0
    capex_racking: float = 0
    capex_electrical_bos: float = 0
    capex_structural_bos: float = 0
    capex_installation: float = 0
    capex_interconnection: float = 0
    capex_engineering: float = 0
    capex_permitting: float = 0
    capex_land: float = 0
    capex_contingency: float = 0
    capex_developer: float = 0
    
    capex_total: float = 0
    capex_per_Wdc: float = 0
    capex_per_Wac: float = 0
    capex_after_incentives: float = 0
    
    # ════════════════════════════════════════════════════════════
    # OPEX (Year 1)
    # ════════════════════════════════════════════════════════════
    opex_fixed_om: float = 0
    opex_variable_om: float = 0
    opex_insurance: float = 0
    opex_property_tax: float = 0
    opex_land_lease: float = 0
    opex_inverter_reserve: float = 0
    
    opex_total_yr1: float = 0
    opex_per_kWdc_yr1: float = 0
    
    # ════════════════════════════════════════════════════════════
    # REVENUE (Year 1)
    # ════════════════════════════════════════════════════════════
    revenue_energy: float = 0
    revenue_rec: float = 0
    revenue_total_yr1: float = 0
    
    # ════════════════════════════════════════════════════════════
    # LEVELIZED COST
    # ════════════════════════════════════════════════════════════
    lcoe_nominal: float = 0      # $/MWh nominal
    lcoe_real: float = 0         # $/MWh real
    lcoe_with_itc: float = 0     # $/MWh after ITC
    
    # LCOE breakdown
    lcoe_capex: float = 0
    lcoe_opex: float = 0
    
    # ════════════════════════════════════════════════════════════
    # FINANCIAL
    # ════════════════════════════════════════════════════════════
    npv: float = 0
    irr: float = 0
    payback_simple: float = 0
    payback_discounted: float = 0
    
    # ════════════════════════════════════════════════════════════
    # CASH FLOWS
    # ════════════════════════════════════════════════════════════
    years: List[int] = field(default_factory=list)
    generation_by_year: List[float] = field(default_factory=list)
    cf_revenue: List[float] = field(default_factory=list)
    cf_opex: List[float] = field(default_factory=list)
    cf_net: List[float] = field(default_factory=list)
    cf_cumulative: List[float] = field(default_factory=list)


class SolarTEA:
    """
    Detailed Techno-Economic Analysis for Solar PV Systems.
    
    Supports utility-scale and distributed systems with:
    - Component-level CAPEX
    - Fixed tilt and tracking configurations
    - Degradation and inverter replacement
    - ITC/PTC incentive modeling
    - LCOE calculation
    """
    
    def __init__(self,
                 capex: Optional[SolarCapexInputs] = None,
                 opex: Optional[SolarOpexInputs] = None,
                 performance: Optional[SolarPerformanceInputs] = None,
                 financial: Optional[SolarFinancialInputs] = None):
        
        self.capex = capex or SolarCapexInputs()
        self.opex = opex or SolarOpexInputs()
        self.performance = performance or SolarPerformanceInputs()
        self.financial = financial or SolarFinancialInputs()
        
        self._outputs: Optional[SolarTEAOutputs] = None
        self._issues: List[str] = []
    
    @property
    def outputs(self) -> SolarTEAOutputs:
        if self._outputs is None:
            raise ValueError("Call calculate() first")
        return self._outputs
    
    def calculate(self) -> SolarTEAOutputs:
        """Run complete solar TEA calculation"""
        out = SolarTEAOutputs()
        cx = self.capex
        ox = self.opex
        perf = self.performance
        fin = self.financial
        
        # ════════════════════════════════════════════════════════════
        # SYSTEM SIZING
        # ════════════════════════════════════════════════════════════
        
        out.capacity_MWdc = perf.capacity_MWdc
        out.dc_ac_ratio = perf.dc_ac_ratio
        out.capacity_MWac = perf.capacity_MWdc / perf.dc_ac_ratio
        out.configuration = perf.configuration.value
        out.capacity_factor = perf.capacity_factor
        
        # Year 1 generation (with first-year degradation)
        gross_generation = perf.capacity_MWac * 8760 * perf.capacity_factor
        out.annual_generation_MWh_yr1 = gross_generation * (1 - perf.first_year_degradation)
        
        # Add bifacial gain if applicable
        if cx.bifacial_gain > 0:
            out.annual_generation_MWh_yr1 *= (1 + cx.bifacial_gain)
        
        # ════════════════════════════════════════════════════════════
        # CAPEX CALCULATION
        # ════════════════════════════════════════════════════════════
        
        capacity_Wdc = perf.capacity_MWdc * 1_000_000
        capacity_Wac = out.capacity_MWac * 1_000_000
        
        # Modules
        out.capex_modules = capacity_Wdc * cx.module_cost_per_Wdc
        
        # Inverters
        out.capex_inverters = capacity_Wac * cx.inverter_cost_per_Wac
        
        # Racking/mounting
        racking_cost = cx.racking_cost_per_Wdc
        if perf.configuration in [SolarConfiguration.SINGLE_AXIS_TRACKER, 
                                   SolarConfiguration.DUAL_AXIS_TRACKER]:
            racking_cost += cx.tracker_premium_per_Wdc
        out.capex_racking = capacity_Wdc * racking_cost
        
        # BOS
        out.capex_electrical_bos = capacity_Wdc * cx.electrical_bos_per_Wdc
        out.capex_structural_bos = capacity_Wdc * cx.structural_bos_per_Wdc
        
        # Installation
        out.capex_installation = capacity_Wdc * cx.installation_labor_per_Wdc
        out.capex_interconnection = capacity_Wac * cx.interconnection_per_Wac
        
        # Hardware subtotal
        hardware = (out.capex_modules + out.capex_inverters + out.capex_racking +
                   out.capex_electrical_bos + out.capex_structural_bos +
                   out.capex_installation + out.capex_interconnection)
        
        # Soft costs
        out.capex_engineering = hardware * cx.engineering_pct
        out.capex_permitting = capacity_Wdc * cx.permitting_per_Wdc
        out.capex_land = capacity_Wdc * cx.land_cost_per_Wdc
        
        # Subtotal before margin and contingency
        subtotal = hardware + out.capex_engineering + out.capex_permitting + out.capex_land
        
        out.capex_contingency = subtotal * cx.contingency_pct
        direct_total = subtotal + out.capex_contingency
        
        out.capex_developer = direct_total * cx.developer_margin_pct
        
        out.capex_total = direct_total + out.capex_developer
        out.capex_per_Wdc = out.capex_total / capacity_Wdc
        out.capex_per_Wac = out.capex_total / capacity_Wac
        
        # Apply incentives
        itc_benefit = out.capex_total * fin.itc_rate
        state_benefit = capacity_Wdc * fin.state_rebate_per_Wdc
        out.capex_after_incentives = out.capex_total - itc_benefit - state_benefit
        
        # ════════════════════════════════════════════════════════════
        # OPEX CALCULATION
        # ════════════════════════════════════════════════════════════
        
        capacity_kWdc = perf.capacity_MWdc * 1000
        
        out.opex_fixed_om = capacity_kWdc * ox.fixed_om_per_kWdc_year
        out.opex_variable_om = out.annual_generation_MWh_yr1 * ox.variable_om_per_MWh
        out.opex_insurance = out.capex_total * ox.insurance_pct
        out.opex_property_tax = out.capex_total * ox.property_tax_pct
        
        # Land lease
        acres = perf.capacity_MWdc * ox.acres_per_MWdc
        out.opex_land_lease = acres * ox.land_lease_per_acre_year
        
        # Inverter replacement reserve (annualized)
        if ox.inverter_lifetime_years > 0:
            num_replacements = fin.project_lifetime_years // ox.inverter_lifetime_years
            inverter_repl_npv = sum(
                out.capex_inverters * ox.inverter_replacement_pct / 
                ((1 + fin.discount_rate) ** (i * ox.inverter_lifetime_years))
                for i in range(1, num_replacements + 1)
                if i * ox.inverter_lifetime_years < fin.project_lifetime_years
            )
            crf = self._crf(fin.discount_rate, fin.project_lifetime_years)
            out.opex_inverter_reserve = inverter_repl_npv * crf
        
        out.opex_total_yr1 = (out.opex_fixed_om + out.opex_variable_om + 
                              out.opex_insurance + out.opex_property_tax +
                              out.opex_land_lease + out.opex_inverter_reserve)
        out.opex_per_kWdc_yr1 = out.opex_total_yr1 / capacity_kWdc
        
        # ════════════════════════════════════════════════════════════
        # REVENUE (Year 1)
        # ════════════════════════════════════════════════════════════
        
        if fin.use_ppa:
            energy_price = fin.ppa_price_per_MWh
        else:
            energy_price = fin.merchant_price_per_MWh
        
        out.revenue_energy = out.annual_generation_MWh_yr1 * energy_price
        out.revenue_rec = out.annual_generation_MWh_yr1 * fin.rec_price_per_MWh
        out.revenue_total_yr1 = out.revenue_energy + out.revenue_rec
        
        # ════════════════════════════════════════════════════════════
        # LCOE CALCULATION
        # ════════════════════════════════════════════════════════════
        
        crf = self._crf(fin.discount_rate, fin.project_lifetime_years)
        
        # Lifetime generation (with degradation)
        lifetime_gen_npv = 0
        total_gen = 0
        for year in range(1, fin.project_lifetime_years + 1):
            if year == 1:
                year_gen = out.annual_generation_MWh_yr1
            else:
                # Apply annual degradation after year 1
                year_gen = out.annual_generation_MWh_yr1 * (1 - perf.annual_degradation) ** (year - 1)
            
            lifetime_gen_npv += year_gen / ((1 + fin.discount_rate) ** year)
            total_gen += year_gen
        
        out.lifetime_generation_MWh = total_gen
        
        # Lifetime OPEX NPV
        lifetime_opex_npv = sum(
            out.opex_total_yr1 * (1 + ox.om_escalation_rate) ** (y - 1) /
            ((1 + fin.discount_rate) ** y)
            for y in range(1, fin.project_lifetime_years + 1)
        )
        
        # LCOE
        out.lcoe_nominal = (out.capex_total + lifetime_opex_npv) / lifetime_gen_npv
        out.lcoe_with_itc = (out.capex_after_incentives + lifetime_opex_npv) / lifetime_gen_npv
        
        # Real LCOE (inflation-adjusted)
        real_discount = (1 + fin.discount_rate) / (1 + fin.inflation_rate) - 1
        lifetime_gen_real = sum(
            (out.annual_generation_MWh_yr1 * (1 - perf.annual_degradation) ** (y - 1)) /
            ((1 + real_discount) ** y)
            for y in range(1, fin.project_lifetime_years + 1)
        )
        out.lcoe_real = (out.capex_after_incentives + lifetime_opex_npv) / lifetime_gen_real
        
        # LCOE breakdown
        out.lcoe_capex = (out.capex_after_incentives * crf) / out.annual_generation_MWh_yr1
        out.lcoe_opex = out.opex_total_yr1 / out.annual_generation_MWh_yr1
        
        # ════════════════════════════════════════════════════════════
        # CASH FLOW
        # ════════════════════════════════════════════════════════════
        
        years = list(range(-1, fin.project_lifetime_years + 1))
        out.years = years
        
        cf_revenue = []
        cf_opex = []
        cf_net = []
        generation_by_year = []
        
        for year in years:
            if year < 1:
                # Construction
                capex_frac = fin.capex_deployment.get(year, 0)
                cf_revenue.append(0)
                cf_opex.append(0)
                cf_net.append(-out.capex_after_incentives * capex_frac)
                generation_by_year.append(0)
            else:
                # Operating
                if year == 1:
                    year_gen = out.annual_generation_MWh_yr1
                else:
                    year_gen = out.annual_generation_MWh_yr1 * (1 - perf.annual_degradation) ** (year - 1)
                
                generation_by_year.append(year_gen)
                
                # Revenue with escalation
                if fin.use_ppa:
                    price_esc = (1 + fin.ppa_escalation_rate) ** (year - 1)
                else:
                    price_esc = (1 + fin.merchant_escalation_rate) ** (year - 1)
                
                rec_esc = (1 + fin.rec_escalation_rate) ** (year - 1)
                
                year_revenue = (year_gen * energy_price * price_esc +
                               year_gen * fin.rec_price_per_MWh * rec_esc)
                
                # Add PTC if elected
                if fin.ptc_per_kWh > 0 and year <= fin.ptc_years:
                    year_revenue += year_gen * 1000 * fin.ptc_per_kWh
                
                cf_revenue.append(year_revenue)
                
                # OPEX with escalation
                year_opex = out.opex_total_yr1 * (1 + ox.om_escalation_rate) ** (year - 1)
                cf_opex.append(-year_opex)
                
                cf_net.append(year_revenue - year_opex)
        
        out.cf_revenue = cf_revenue
        out.cf_opex = cf_opex
        out.cf_net = cf_net
        out.cf_cumulative = list(np.cumsum(cf_net))
        out.generation_by_year = generation_by_year
        
        # ════════════════════════════════════════════════════════════
        # FINANCIAL METRICS
        # ════════════════════════════════════════════════════════════
        
        out.npv = sum(cf / ((1 + fin.discount_rate) ** (i + 1)) 
                     for i, cf in enumerate(cf_net))
        out.irr = self._calculate_irr(cf_net)
        out.payback_simple = self._simple_payback(out.cf_cumulative)
        out.payback_discounted = self._discounted_payback(cf_net, fin.discount_rate)
        
        self._outputs = out
        return out
    
    def _crf(self, rate: float, years: int) -> float:
        if rate == 0:
            return 1 / years
        return (rate * (1 + rate) ** years) / ((1 + rate) ** years - 1)
    
    def _calculate_irr(self, cashflows: List[float]) -> float:
        try:
            import numpy_financial as npf
            return npf.irr(cashflows)
        except:
            return 0.10
    
    def _simple_payback(self, cumulative: List[float]) -> float:
        for i, c in enumerate(cumulative):
            if c >= 0 and i > 0:
                return i - 1 + (-cumulative[i-1] / (c - cumulative[i-1]))
        return float('inf')
    
    def _discounted_payback(self, cashflows: List[float], rate: float) -> float:
        cumulative = 0
        for i, cf in enumerate(cashflows):
            pv = cf / ((1 + rate) ** i)
            prev = cumulative
            cumulative += pv
            if cumulative >= 0 and i > 0:
                return i - 1 + (-prev / pv) if pv != 0 else float('inf')
        return float('inf')
    
    def summary(self) -> str:
        if self._outputs is None:
            return "Call calculate() first"
        
        o = self._outputs
        return f"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                         SOLAR PV TEA SUMMARY                                  ║
╠═══════════════════════════════════════════════════════════════════════════════╣

  SYSTEM CONFIGURATION
  ─────────────────────────────────────────────────────────────────────────────
    Capacity (DC):                 {o.capacity_MWdc:>12,.1f} MWdc
    Capacity (AC):                 {o.capacity_MWac:>12,.1f} MWac
    DC/AC Ratio:                   {o.dc_ac_ratio:>12.2f}
    Configuration:                 {o.configuration:>12}
    Capacity Factor:               {o.capacity_factor:>12.1%}
    
    Year 1 Generation:             {o.annual_generation_MWh_yr1:>12,.0f} MWh
    Lifetime Generation:           {o.lifetime_generation_MWh:>12,.0f} MWh

  CAPEX BREAKDOWN
  ─────────────────────────────────────────────────────────────────────────────
    Modules:                       ${o.capex_modules:>14,.0f}  ({o.capex_modules/o.capex_total*100:>5.1f}%)
    Inverters:                     ${o.capex_inverters:>14,.0f}  ({o.capex_inverters/o.capex_total*100:>5.1f}%)
    Racking/Tracking:              ${o.capex_racking:>14,.0f}  ({o.capex_racking/o.capex_total*100:>5.1f}%)
    Electrical BOS:                ${o.capex_electrical_bos:>14,.0f}
    Structural BOS:                ${o.capex_structural_bos:>14,.0f}
    Installation:                  ${o.capex_installation:>14,.0f}
    Interconnection:               ${o.capex_interconnection:>14,.0f}
    Engineering:                   ${o.capex_engineering:>14,.0f}
    Permitting:                    ${o.capex_permitting:>14,.0f}
    Land:                          ${o.capex_land:>14,.0f}
    Contingency:                   ${o.capex_contingency:>14,.0f}
    Developer Margin:              ${o.capex_developer:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL CAPEX:                   ${o.capex_total:>14,.0f}
    $/Wdc:                         ${o.capex_per_Wdc:>14.2f}
    $/Wac:                         ${o.capex_per_Wac:>14.2f}
    After Incentives:              ${o.capex_after_incentives:>14,.0f}

  ANNUAL OPEX (Year 1)
  ─────────────────────────────────────────────────────────────────────────────
    Fixed O&M:                     ${o.opex_fixed_om:>14,.0f}
    Variable O&M:                  ${o.opex_variable_om:>14,.0f}
    Insurance:                     ${o.opex_insurance:>14,.0f}
    Property Tax:                  ${o.opex_property_tax:>14,.0f}
    Land Lease:                    ${o.opex_land_lease:>14,.0f}
    Inverter Reserve:              ${o.opex_inverter_reserve:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL OPEX:                    ${o.opex_total_yr1:>14,.0f}
    $/kWdc-year:                   ${o.opex_per_kWdc_yr1:>14.2f}

  LEVELIZED COST OF ENERGY
  ─────────────────────────────────────────────────────────────────────────────
    LCOE (nominal):                ${o.lcoe_nominal:>14.2f}/MWh
    LCOE (with ITC):               ${o.lcoe_with_itc:>14.2f}/MWh
    LCOE (real):                   ${o.lcoe_real:>14.2f}/MWh
    
    Breakdown:
      CAPEX component:             ${o.lcoe_capex:>14.2f}/MWh
      OPEX component:              ${o.lcoe_opex:>14.2f}/MWh

  FINANCIAL METRICS
  ─────────────────────────────────────────────────────────────────────────────
    NPV:                           ${o.npv:>14,.0f}
    IRR:                           {o.irr:>14.1%}
    Simple Payback:                {o.payback_simple:>14.1f} years
    Discounted Payback:            {o.payback_discounted:>14.1f} years

╚═══════════════════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 80)
    print("SOLAR PV TEA - EXAMPLE (100 MWdc Single-Axis Tracker)")
    print("=" * 80)
    
    tea = SolarTEA(
        performance=SolarPerformanceInputs(
            capacity_MWdc=100,
            dc_ac_ratio=1.3,
            configuration=SolarConfiguration.SINGLE_AXIS_TRACKER,
            capacity_factor=0.28,
        ),
        financial=SolarFinancialInputs(
            ppa_price_per_MWh=35,
            itc_rate=0.30,
        ),
    )
    
    results = tea.calculate()
    print(tea.summary())
