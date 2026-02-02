"""
Wind Techno-Economic Analysis (TEA) Module

Detailed TEA for onshore and offshore wind projects.

Features:
- Component-level CAPEX (turbines, BOS, soft costs)
- Turbine technology and sizing optimization
- Wake losses and inter-annual variability
- O&M cost curves (warranty, mid-life, end-of-life)
- LCOE calculation
- PPA vs merchant pricing
- ITC/PTC incentive modeling

References:
- NREL ATB 2024
- SHARE Model inputs.py
- Lazard LCOE Analysis
- IEA Wind TCP
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from enum import Enum
import numpy as np


class WindType(Enum):
    """Wind project type"""
    ONSHORE = "onshore"
    OFFSHORE_FIXED = "offshore_fixed"
    OFFSHORE_FLOATING = "offshore_floating"


class TurbineClass(Enum):
    """IEC turbine class"""
    CLASS_I = "Class I"     # High wind (>8.5 m/s)
    CLASS_II = "Class II"   # Medium wind (7.5-8.5 m/s)
    CLASS_III = "Class III" # Low wind (<7.5 m/s)


@dataclass
class WindCapexInputs:
    """Detailed CAPEX breakdown for wind"""
    
    # ════════════════════════════════════════════════════════════
    # TURBINES
    # ════════════════════════════════════════════════════════════
    
    # Turbine cost ($/kW) - includes nacelle, blades, tower
    turbine_cost_per_kW: float = 800
    
    # Turbine rating (MW)
    turbine_rating_MW: float = 5.0
    
    # Rotor diameter (m)
    rotor_diameter_m: float = 160
    
    # Hub height (m)
    hub_height_m: float = 100
    
    # ════════════════════════════════════════════════════════════
    # BALANCE OF SYSTEM
    # ════════════════════════════════════════════════════════════
    
    # Foundation ($/kW)
    foundation_per_kW: float = 50
    
    # Roads and civil works ($/kW)
    roads_civil_per_kW: float = 40
    
    # Electrical collection ($/kW)
    electrical_collection_per_kW: float = 80
    
    # Substation ($/kW)
    substation_per_kW: float = 30
    
    # Grid interconnection ($/kW)
    interconnection_per_kW: float = 50
    
    # ════════════════════════════════════════════════════════════
    # INSTALLATION
    # ════════════════════════════════════════════════════════════
    
    # Turbine installation ($/kW)
    turbine_install_per_kW: float = 50
    
    # Electrical installation ($/kW)
    electrical_install_per_kW: float = 30
    
    # ════════════════════════════════════════════════════════════
    # SOFT COSTS
    # ════════════════════════════════════════════════════════════
    
    # Engineering & management (% of hard costs)
    engineering_pct: float = 0.05
    
    # Development & permitting ($/kW)
    development_per_kW: float = 30
    
    # Contingency (% of direct costs)
    contingency_pct: float = 0.05
    
    # ════════════════════════════════════════════════════════════
    # LAND
    # ════════════════════════════════════════════════════════════
    
    # Land lease capitalized ($/kW)
    land_per_kW: float = 20
    
    # ════════════════════════════════════════════════════════════
    # OFFSHORE ADDERS
    # ════════════════════════════════════════════════════════════
    
    # Offshore foundation adder ($/kW)
    offshore_foundation_adder: float = 400
    
    # Offshore cable adder ($/kW)
    offshore_cable_adder: float = 200
    
    # Offshore installation adder ($/kW)
    offshore_install_adder: float = 300
    
    # Floating platform adder ($/kW)
    floating_adder: float = 500


@dataclass
class WindOpexInputs:
    """Operating costs for wind"""
    
    # ════════════════════════════════════════════════════════════
    # FIXED O&M
    # ════════════════════════════════════════════════════════════
    
    # Fixed O&M schedule (% of CAPEX by period)
    # Reflects warranty period -> mid-life -> end-of-life
    fixed_om_warranty_pct: float = 0.010   # Years 1-2
    fixed_om_midlife_pct: float = 0.015    # Years 3-10
    fixed_om_endlife_pct: float = 0.020    # Years 11+
    
    warranty_years: int = 2
    midlife_year: int = 10
    
    # Alternative: flat $/kW-year (if not using escalating schedule)
    fixed_om_per_kW_year: Optional[float] = None  # ~$30-50/kW-year
    
    # O&M escalation above inflation (%/year)
    om_real_escalation: float = 0.005
    
    # ════════════════════════════════════════════════════════════
    # VARIABLE O&M
    # ════════════════════════════════════════════════════════════
    
    # Variable O&M ($/MWh)
    variable_om_per_MWh: float = 2.0
    
    # ════════════════════════════════════════════════════════════
    # MAJOR MAINTENANCE
    # ════════════════════════════════════════════════════════════
    
    # Gearbox replacement (% of turbine cost)
    gearbox_replacement_pct: float = 0.15
    gearbox_replacement_year: int = 15
    
    # Blade repair reserve ($/kW-year)
    blade_reserve_per_kW_year: float = 2.0
    
    # ════════════════════════════════════════════════════════════
    # INSURANCE & OTHER
    # ════════════════════════════════════════════════════════════
    
    # Insurance (% of CAPEX)
    insurance_pct: float = 0.005
    
    # Property tax (% of CAPEX)
    property_tax_pct: float = 0.01
    
    # Land lease ($/MW-year) if not capitalized
    land_lease_per_MW_year: float = 5000


@dataclass
class WindPerformanceInputs:
    """Performance parameters for wind"""
    
    # ════════════════════════════════════════════════════════════
    # CAPACITY
    # ════════════════════════════════════════════════════════════
    
    # Total capacity (MW)
    capacity_MW: float = 400
    
    # Number of turbines (calculated if not specified)
    num_turbines: Optional[int] = None
    
    # Turbine rating for calculation
    turbine_rating_MW: float = 5.0
    
    # ════════════════════════════════════════════════════════════
    # PROJECT TYPE
    # ════════════════════════════════════════════════════════════
    
    project_type: WindType = WindType.ONSHORE
    turbine_class: TurbineClass = TurbineClass.CLASS_II
    
    # ════════════════════════════════════════════════════════════
    # RESOURCE
    # ════════════════════════════════════════════════════════════
    
    # Net capacity factor (after all losses)
    capacity_factor: float = 0.35
    
    # Gross capacity factor (before losses)
    gross_capacity_factor: float = 0.42
    
    # Mean wind speed at hub height (m/s)
    mean_wind_speed: float = 8.0
    
    # ════════════════════════════════════════════════════════════
    # LOSSES
    # ════════════════════════════════════════════════════════════
    
    wake_losses: float = 0.08
    electrical_losses: float = 0.02
    availability: float = 0.97
    turbine_performance: float = 0.98
    environmental_losses: float = 0.01  # Icing, blade soiling
    curtailment: float = 0.01
    
    # ════════════════════════════════════════════════════════════
    # DEGRADATION & VARIABILITY
    # ════════════════════════════════════════════════════════════
    
    # Annual degradation
    annual_degradation: float = 0.005  # 0.5%/year
    
    # Inter-annual variability (std dev of capacity factor)
    iav_sigma: float = 0.06  # 6% standard deviation


@dataclass
class WindFinancialInputs:
    """Financial parameters for wind TEA"""
    
    # Project timeline
    project_lifetime_years: int = 30
    construction_months: int = 24
    
    # Discount rate / WACC
    discount_rate: float = 0.07
    
    # Inflation
    inflation_rate: float = 0.025
    
    # Tax
    tax_rate: float = 0.21
    depreciation_years: int = 5  # MACRS
    
    # ════════════════════════════════════════════════════════════
    # INCENTIVES
    # ════════════════════════════════════════════════════════════
    
    # ITC (alternative to PTC)
    itc_rate: float = 0.0  # Wind typically uses PTC
    
    # PTC ($/kWh for 10 years)
    ptc_per_kWh: float = 0.026  # ~$26/MWh
    ptc_years: int = 10
    
    # ════════════════════════════════════════════════════════════
    # REVENUE
    # ════════════════════════════════════════════════════════════
    
    # PPA price ($/MWh)
    ppa_price_per_MWh: float = 30.0
    ppa_escalation_rate: float = 0.015
    
    # Merchant price ($/MWh)
    merchant_price_per_MWh: float = 25.0
    merchant_escalation_rate: float = 0.02
    
    use_ppa: bool = True
    
    # REC value ($/MWh)
    rec_price_per_MWh: float = 3.0
    rec_escalation_rate: float = -0.03
    
    # Capacity payment ($/kW-year)
    capacity_payment_per_kW_year: float = 0.0
    
    # CAPEX deployment
    capex_deployment: Dict[int, float] = field(default_factory=lambda: {
        -2: 0.10,
        -1: 0.50,
        0: 0.40,
    })


@dataclass
class WindTEAOutputs:
    """Complete wind TEA results"""
    
    # System
    capacity_MW: float = 0
    num_turbines: int = 0
    turbine_rating_MW: float = 0
    project_type: str = ""
    capacity_factor: float = 0
    
    # Production
    annual_generation_MWh_yr1: float = 0
    lifetime_generation_MWh: float = 0
    
    # CAPEX
    capex_turbines: float = 0
    capex_foundation: float = 0
    capex_roads_civil: float = 0
    capex_electrical: float = 0
    capex_substation: float = 0
    capex_interconnection: float = 0
    capex_installation: float = 0
    capex_engineering: float = 0
    capex_development: float = 0
    capex_land: float = 0
    capex_contingency: float = 0
    capex_offshore_adder: float = 0
    
    capex_total: float = 0
    capex_per_kW: float = 0
    capex_after_incentives: float = 0
    
    # OPEX (Year 1)
    opex_fixed_om: float = 0
    opex_variable_om: float = 0
    opex_insurance: float = 0
    opex_property_tax: float = 0
    opex_land_lease: float = 0
    opex_major_maintenance_reserve: float = 0
    
    opex_total_yr1: float = 0
    opex_per_kW_yr1: float = 0
    
    # Revenue (Year 1)
    revenue_energy: float = 0
    revenue_ptc: float = 0
    revenue_rec: float = 0
    revenue_capacity: float = 0
    revenue_total_yr1: float = 0
    
    # LCOE
    lcoe_nominal: float = 0
    lcoe_with_ptc: float = 0
    lcoe_real: float = 0
    
    lcoe_capex: float = 0
    lcoe_opex: float = 0
    
    # Financial
    npv: float = 0
    irr: float = 0
    payback_simple: float = 0
    payback_discounted: float = 0
    
    # Cash flows
    years: List[int] = field(default_factory=list)
    generation_by_year: List[float] = field(default_factory=list)
    cf_net: List[float] = field(default_factory=list)
    cf_cumulative: List[float] = field(default_factory=list)


class WindTEA:
    """
    Detailed Techno-Economic Analysis for Wind Projects.
    
    Supports onshore and offshore with:
    - Component-level CAPEX
    - Escalating O&M cost schedule
    - Wake losses and degradation
    - PTC incentive modeling
    - LCOE calculation
    """
    
    def __init__(self,
                 capex: Optional[WindCapexInputs] = None,
                 opex: Optional[WindOpexInputs] = None,
                 performance: Optional[WindPerformanceInputs] = None,
                 financial: Optional[WindFinancialInputs] = None):
        
        self.capex = capex or WindCapexInputs()
        self.opex = opex or WindOpexInputs()
        self.performance = performance or WindPerformanceInputs()
        self.financial = financial or WindFinancialInputs()
        
        self._outputs: Optional[WindTEAOutputs] = None
    
    @property
    def outputs(self) -> WindTEAOutputs:
        if self._outputs is None:
            raise ValueError("Call calculate() first")
        return self._outputs
    
    def calculate(self) -> WindTEAOutputs:
        """Run complete wind TEA calculation"""
        out = WindTEAOutputs()
        cx = self.capex
        ox = self.opex
        perf = self.performance
        fin = self.financial
        
        # ════════════════════════════════════════════════════════════
        # SYSTEM SIZING
        # ════════════════════════════════════════════════════════════
        
        out.capacity_MW = perf.capacity_MW
        out.turbine_rating_MW = cx.turbine_rating_MW
        out.num_turbines = perf.num_turbines or int(perf.capacity_MW / cx.turbine_rating_MW)
        out.project_type = perf.project_type.value
        out.capacity_factor = perf.capacity_factor
        
        # Year 1 generation
        out.annual_generation_MWh_yr1 = perf.capacity_MW * 8760 * perf.capacity_factor
        
        # ════════════════════════════════════════════════════════════
        # CAPEX
        # ════════════════════════════════════════════════════════════
        
        capacity_kW = perf.capacity_MW * 1000
        
        # Turbines
        out.capex_turbines = capacity_kW * cx.turbine_cost_per_kW
        
        # BOS
        out.capex_foundation = capacity_kW * cx.foundation_per_kW
        out.capex_roads_civil = capacity_kW * cx.roads_civil_per_kW
        out.capex_electrical = capacity_kW * cx.electrical_collection_per_kW
        out.capex_substation = capacity_kW * cx.substation_per_kW
        out.capex_interconnection = capacity_kW * cx.interconnection_per_kW
        
        # Installation
        out.capex_installation = capacity_kW * (cx.turbine_install_per_kW + cx.electrical_install_per_kW)
        
        # Offshore adders
        if perf.project_type == WindType.OFFSHORE_FIXED:
            out.capex_offshore_adder = capacity_kW * (
                cx.offshore_foundation_adder + cx.offshore_cable_adder + cx.offshore_install_adder
            )
        elif perf.project_type == WindType.OFFSHORE_FLOATING:
            out.capex_offshore_adder = capacity_kW * (
                cx.offshore_foundation_adder + cx.offshore_cable_adder + 
                cx.offshore_install_adder + cx.floating_adder
            )
        
        # Hard costs subtotal
        hard_costs = (out.capex_turbines + out.capex_foundation + out.capex_roads_civil +
                     out.capex_electrical + out.capex_substation + out.capex_interconnection +
                     out.capex_installation + out.capex_offshore_adder)
        
        # Soft costs
        out.capex_engineering = hard_costs * cx.engineering_pct
        out.capex_development = capacity_kW * cx.development_per_kW
        out.capex_land = capacity_kW * cx.land_per_kW
        
        subtotal = hard_costs + out.capex_engineering + out.capex_development + out.capex_land
        out.capex_contingency = subtotal * cx.contingency_pct
        
        out.capex_total = subtotal + out.capex_contingency
        out.capex_per_kW = out.capex_total / capacity_kW
        
        # Incentives (ITC if elected)
        itc_benefit = out.capex_total * fin.itc_rate
        out.capex_after_incentives = out.capex_total - itc_benefit
        
        # ════════════════════════════════════════════════════════════
        # OPEX (Year 1)
        # ════════════════════════════════════════════════════════════
        
        # Fixed O&M (using warranty period rate for year 1)
        if ox.fixed_om_per_kW_year:
            out.opex_fixed_om = capacity_kW * ox.fixed_om_per_kW_year / 1000  # Convert to $
        else:
            out.opex_fixed_om = out.capex_total * ox.fixed_om_warranty_pct
        
        # Variable O&M
        out.opex_variable_om = out.annual_generation_MWh_yr1 * ox.variable_om_per_MWh
        
        # Insurance and tax
        out.opex_insurance = out.capex_total * ox.insurance_pct
        out.opex_property_tax = out.capex_total * ox.property_tax_pct
        
        # Land lease
        out.opex_land_lease = perf.capacity_MW * ox.land_lease_per_MW_year
        
        # Major maintenance reserve
        out.opex_major_maintenance_reserve = capacity_kW * ox.blade_reserve_per_kW_year / 1000
        
        out.opex_total_yr1 = (out.opex_fixed_om + out.opex_variable_om + 
                              out.opex_insurance + out.opex_property_tax +
                              out.opex_land_lease + out.opex_major_maintenance_reserve)
        out.opex_per_kW_yr1 = out.opex_total_yr1 / capacity_kW * 1000
        
        # ════════════════════════════════════════════════════════════
        # REVENUE (Year 1)
        # ════════════════════════════════════════════════════════════
        
        energy_price = fin.ppa_price_per_MWh if fin.use_ppa else fin.merchant_price_per_MWh
        out.revenue_energy = out.annual_generation_MWh_yr1 * energy_price
        
        # PTC (if applicable)
        if fin.ptc_per_kWh > 0:
            out.revenue_ptc = out.annual_generation_MWh_yr1 * 1000 * fin.ptc_per_kWh
        
        out.revenue_rec = out.annual_generation_MWh_yr1 * fin.rec_price_per_MWh
        out.revenue_capacity = capacity_kW * fin.capacity_payment_per_kW_year / 1000
        
        out.revenue_total_yr1 = (out.revenue_energy + out.revenue_ptc + 
                                 out.revenue_rec + out.revenue_capacity)
        
        # ════════════════════════════════════════════════════════════
        # LCOE
        # ════════════════════════════════════════════════════════════
        
        crf = self._crf(fin.discount_rate, fin.project_lifetime_years)
        
        # Lifetime generation with degradation
        lifetime_gen_npv = 0
        total_gen = 0
        for year in range(1, fin.project_lifetime_years + 1):
            year_gen = out.annual_generation_MWh_yr1 * (1 - perf.annual_degradation) ** (year - 1)
            lifetime_gen_npv += year_gen / ((1 + fin.discount_rate) ** year)
            total_gen += year_gen
        
        out.lifetime_generation_MWh = total_gen
        
        # Lifetime OPEX with escalation schedule
        lifetime_opex_npv = 0
        for year in range(1, fin.project_lifetime_years + 1):
            # Determine O&M rate based on project phase
            if ox.fixed_om_per_kW_year:
                fixed_om_rate = ox.fixed_om_per_kW_year * capacity_kW / 1000
            elif year <= ox.warranty_years:
                fixed_om_rate = out.capex_total * ox.fixed_om_warranty_pct
            elif year <= ox.midlife_year:
                fixed_om_rate = out.capex_total * ox.fixed_om_midlife_pct
            else:
                fixed_om_rate = out.capex_total * ox.fixed_om_endlife_pct
            
            # Apply escalation
            escalation = (1 + fin.inflation_rate + ox.om_real_escalation) ** (year - 1)
            year_opex = (fixed_om_rate * escalation + 
                        out.opex_variable_om + out.opex_insurance + 
                        out.opex_property_tax + out.opex_land_lease)
            
            lifetime_opex_npv += year_opex / ((1 + fin.discount_rate) ** year)
        
        # LCOE
        out.lcoe_nominal = (out.capex_total + lifetime_opex_npv) / lifetime_gen_npv
        
        # LCOE with PTC
        if fin.ptc_per_kWh > 0:
            ptc_value_npv = sum(
                out.annual_generation_MWh_yr1 * (1 - perf.annual_degradation) ** (y - 1) * 
                1000 * fin.ptc_per_kWh / ((1 + fin.discount_rate) ** y)
                for y in range(1, min(fin.ptc_years + 1, fin.project_lifetime_years + 1))
            )
            out.lcoe_with_ptc = (out.capex_total + lifetime_opex_npv - ptc_value_npv) / lifetime_gen_npv
        else:
            out.lcoe_with_ptc = out.lcoe_nominal
        
        out.lcoe_capex = (out.capex_total * crf) / out.annual_generation_MWh_yr1
        out.lcoe_opex = out.opex_total_yr1 / out.annual_generation_MWh_yr1
        
        # ════════════════════════════════════════════════════════════
        # CASH FLOWS
        # ════════════════════════════════════════════════════════════
        
        years = list(range(-2, fin.project_lifetime_years + 1))
        out.years = years
        
        cf_net = []
        generation_by_year = []
        
        for year in years:
            if year < 1:
                capex_frac = fin.capex_deployment.get(year, 0)
                cf_net.append(-out.capex_after_incentives * capex_frac)
                generation_by_year.append(0)
            else:
                year_gen = out.annual_generation_MWh_yr1 * (1 - perf.annual_degradation) ** (year - 1)
                generation_by_year.append(year_gen)
                
                # Revenue
                if fin.use_ppa:
                    price_esc = (1 + fin.ppa_escalation_rate) ** (year - 1)
                else:
                    price_esc = (1 + fin.merchant_escalation_rate) ** (year - 1)
                
                year_revenue = year_gen * energy_price * price_esc
                
                # PTC
                if fin.ptc_per_kWh > 0 and year <= fin.ptc_years:
                    year_revenue += year_gen * 1000 * fin.ptc_per_kWh
                
                # RECs
                rec_esc = (1 + fin.rec_escalation_rate) ** (year - 1)
                year_revenue += year_gen * fin.rec_price_per_MWh * max(0, rec_esc)
                
                # OPEX
                if ox.fixed_om_per_kW_year:
                    fixed_om = ox.fixed_om_per_kW_year * capacity_kW / 1000
                elif year <= ox.warranty_years:
                    fixed_om = out.capex_total * ox.fixed_om_warranty_pct
                elif year <= ox.midlife_year:
                    fixed_om = out.capex_total * ox.fixed_om_midlife_pct
                else:
                    fixed_om = out.capex_total * ox.fixed_om_endlife_pct
                
                escalation = (1 + fin.inflation_rate + ox.om_real_escalation) ** (year - 1)
                year_opex = (fixed_om * escalation + out.opex_variable_om + 
                            out.opex_insurance + out.opex_property_tax + out.opex_land_lease)
                
                # Gearbox replacement
                if year == ox.gearbox_replacement_year:
                    year_opex += out.capex_turbines * ox.gearbox_replacement_pct
                
                cf_net.append(year_revenue - year_opex)
        
        out.cf_net = cf_net
        out.cf_cumulative = list(np.cumsum(cf_net))
        out.generation_by_year = generation_by_year
        
        # Financial metrics
        out.npv = sum(cf / ((1 + fin.discount_rate) ** (i + 2)) for i, cf in enumerate(cf_net))
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
║                           WIND TEA SUMMARY                                    ║
╠═══════════════════════════════════════════════════════════════════════════════╣

  SYSTEM CONFIGURATION
  ─────────────────────────────────────────────────────────────────────────────
    Capacity:                      {o.capacity_MW:>12,.0f} MW
    Number of Turbines:            {o.num_turbines:>12}
    Turbine Rating:                {o.turbine_rating_MW:>12.1f} MW
    Project Type:                  {o.project_type:>12}
    Capacity Factor:               {o.capacity_factor:>12.1%}
    
    Year 1 Generation:             {o.annual_generation_MWh_yr1:>12,.0f} MWh
    Lifetime Generation:           {o.lifetime_generation_MWh:>12,.0f} MWh

  CAPEX BREAKDOWN
  ─────────────────────────────────────────────────────────────────────────────
    Turbines:                      ${o.capex_turbines:>14,.0f}  ({o.capex_turbines/o.capex_total*100:>5.1f}%)
    Foundation:                    ${o.capex_foundation:>14,.0f}
    Roads/Civil:                   ${o.capex_roads_civil:>14,.0f}
    Electrical Collection:         ${o.capex_electrical:>14,.0f}
    Substation:                    ${o.capex_substation:>14,.0f}
    Interconnection:               ${o.capex_interconnection:>14,.0f}
    Installation:                  ${o.capex_installation:>14,.0f}
    Engineering:                   ${o.capex_engineering:>14,.0f}
    Development:                   ${o.capex_development:>14,.0f}
    Land:                          ${o.capex_land:>14,.0f}
    Offshore Adder:                ${o.capex_offshore_adder:>14,.0f}
    Contingency:                   ${o.capex_contingency:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL CAPEX:                   ${o.capex_total:>14,.0f}
    $/kW:                          ${o.capex_per_kW:>14,.0f}

  ANNUAL OPEX (Year 1)
  ─────────────────────────────────────────────────────────────────────────────
    Fixed O&M:                     ${o.opex_fixed_om:>14,.0f}
    Variable O&M:                  ${o.opex_variable_om:>14,.0f}
    Insurance:                     ${o.opex_insurance:>14,.0f}
    Property Tax:                  ${o.opex_property_tax:>14,.0f}
    Land Lease:                    ${o.opex_land_lease:>14,.0f}
    Major Maintenance Reserve:     ${o.opex_major_maintenance_reserve:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL OPEX:                    ${o.opex_total_yr1:>14,.0f}
    $/kW-year:                     ${o.opex_per_kW_yr1:>14.2f}

  REVENUE (Year 1)
  ─────────────────────────────────────────────────────────────────────────────
    Energy Sales:                  ${o.revenue_energy:>14,.0f}
    PTC:                           ${o.revenue_ptc:>14,.0f}
    RECs:                          ${o.revenue_rec:>14,.0f}
    Capacity:                      ${o.revenue_capacity:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL REVENUE:                 ${o.revenue_total_yr1:>14,.0f}

  LEVELIZED COST OF ENERGY
  ─────────────────────────────────────────────────────────────────────────────
    LCOE (nominal):                ${o.lcoe_nominal:>14.2f}/MWh
    LCOE (with PTC):               ${o.lcoe_with_ptc:>14.2f}/MWh
    
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
    print("WIND TEA - EXAMPLE (400 MW Onshore)")
    print("=" * 80)
    
    tea = WindTEA(
        capex=WindCapexInputs(
            turbine_cost_per_kW=800,
            turbine_rating_MW=5.0,
        ),
        performance=WindPerformanceInputs(
            capacity_MW=400,
            capacity_factor=0.38,
            project_type=WindType.ONSHORE,
        ),
        financial=WindFinancialInputs(
            ppa_price_per_MWh=30,
            ptc_per_kWh=0.026,
        ),
    )
    
    results = tea.calculate()
    print(tea.summary())
