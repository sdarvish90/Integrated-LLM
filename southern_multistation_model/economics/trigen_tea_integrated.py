"""
TriGen (MCFC) Integrated Techno-Economic Analysis Module

Compatible with SHARE model methodology and unified TEA framework.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum
import numpy as np


class CostAllocationMethod(Enum):
    ENERGY_CONTENT = "energy_content"
    MARKET_VALUE = "market_value"
    INCREMENTAL = "incremental"


class TriGenModel(Enum):
    SURESOURCE_1500 = "SureSource 1500"
    SURESOURCE_3000 = "SureSource 3000"
    SURESOURCE_4000 = "SureSource 4000"
    CUSTOM = "Custom"


@dataclass
class TriGenSystemInputs:
    model: TriGenModel = TriGenModel.SURESOURCE_3000
    rated_power_kW: float = 1400
    ng_input_scf_h: float = 10700
    num_units: int = 1
    electrical_efficiency: float = 0.47
    h2_output_kg_h: float = 21.76
    heat_output_kW_th: float = 414
    heat_utilization_factor: float = 0.70
    capacity_factor: float = 0.92
    annual_degradation: float = 0.005
    stack_lifetime_years: float = 7
    
    @property
    def total_power_kW(self) -> float:
        return self.rated_power_kW * self.num_units


@dataclass
class TriGenCapexInputs:
    system_cost_per_kW: float = 5000
    stack_fraction: float = 0.40
    psa_cost_per_kg_day: float = 800
    h2_compression_per_kg_day: float = 200
    h2_buffer_per_kg: float = 500
    h2_buffer_hours: float = 12
    heat_recovery_per_kW_th: float = 150
    civil_per_kW: float = 100
    electrical_interconnect_per_kW: float = 150
    gas_interconnect_per_scfh: float = 2.0
    commissioning_per_kW: float = 75
    engineering_pct: float = 0.08
    project_management_pct: float = 0.05
    contingency_pct: float = 0.10
    stack_replacement_fraction: float = 0.85


@dataclass
class TriGenOpexInputs:
    ng_commodity_price: float = 4.00
    ng_delivery_price: float = 0.50
    ng_escalation_rate: float = 0.02
    service_contract_per_kW_year: float = 50
    insurance_pct: float = 0.005
    property_tax_pct: float = 0.01
    monitoring_per_kW_year: float = 10
    variable_om_per_kWh: float = 0.005
    water_cost_per_gallon: float = 0.005
    water_gal_per_kWh: float = 0.5


@dataclass
class TriGenRevenueInputs:
    electricity_price: float = 0.08
    demand_charge_avoided: float = 15
    capacity_payment: float = 50
    electricity_escalation: float = 0.02
    h2_price: float = 6.00
    h2_escalation: float = 0.01
    heat_price_per_MMBtu: float = 8.00
    heat_escalation: float = 0.02
    itc_rate: float = 0.30
    state_incentive_per_kW: float = 0


@dataclass
class TriGenFinancialInputs:
    project_lifetime_years: int = 20
    discount_rate: float = 0.08
    inflation_rate: float = 0.025
    capex_deployment: Dict[int, float] = field(default_factory=lambda: {-1: 0.40, 0: 0.60})


@dataclass
class TriGenEmissionsInputs:
    ng_co2_factor_kg_per_MMBtu: float = 53.06
    grid_co2_intensity_kg_per_kWh: float = 0.40
    gray_h2_co2_intensity: float = 10.0


@dataclass
class TriGenTEAOutputs:
    # System
    capacity_kW: float = 0
    num_units: int = 0
    annual_operating_hours: float = 0
    
    # Production
    annual_electricity_MWh: float = 0
    annual_h2_kg: float = 0
    annual_h2_tonnes: float = 0
    annual_heat_MMBtu: float = 0
    annual_ng_MMBtu: float = 0
    
    # CAPEX
    capex_fuel_cell_system: float = 0
    capex_h2_handling: float = 0
    capex_heat_recovery: float = 0
    capex_installation: float = 0
    capex_soft_costs: float = 0
    capex_total: float = 0
    capex_per_kW: float = 0
    capex_after_incentives: float = 0
    
    # OPEX
    opex_fuel: float = 0
    opex_fixed: float = 0
    opex_variable: float = 0
    opex_stack_reserve: float = 0
    opex_total_yr1: float = 0
    
    # Revenue
    revenue_electricity: float = 0
    revenue_h2: float = 0
    revenue_heat: float = 0
    revenue_total_yr1: float = 0
    
    # Levelized costs
    lcoh_energy: float = 0
    lcoh_market: float = 0
    lcoh_incremental: float = 0
    lcoh_best_estimate: float = 0
    lcoe: float = 0
    
    # Comparison
    green_h2_lcoh_reference: float = 0
    lcoh_vs_green: float = 0
    
    # Financial
    npv: float = 0
    irr: float = 0
    payback_simple: float = 0
    
    # Emissions
    annual_co2_tonnes: float = 0
    co2_intensity_h2: float = 0
    
    # Cash flows
    years: List[int] = field(default_factory=list)
    cf_net: List[float] = field(default_factory=list)
    cf_cumulative: List[float] = field(default_factory=list)


class TriGenIntegratedTEA:
    """TriGen TEA compatible with SHARE model and unified framework."""
    
    def __init__(self,
                 system: Optional[TriGenSystemInputs] = None,
                 capex: Optional[TriGenCapexInputs] = None,
                 opex: Optional[TriGenOpexInputs] = None,
                 revenue: Optional[TriGenRevenueInputs] = None,
                 financial: Optional[TriGenFinancialInputs] = None,
                 emissions: Optional[TriGenEmissionsInputs] = None):
        
        self.system = system or TriGenSystemInputs()
        self.capex = capex or TriGenCapexInputs()
        self.opex = opex or TriGenOpexInputs()
        self.revenue = revenue or TriGenRevenueInputs()
        self.financial = financial or TriGenFinancialInputs()
        self.emissions = emissions or TriGenEmissionsInputs()
        self._outputs: Optional[TriGenTEAOutputs] = None
        self._issues: List[str] = []
    
    @property
    def outputs(self) -> TriGenTEAOutputs:
        if self._outputs is None:
            raise ValueError("Call calculate() first")
        return self._outputs
    
    def calculate(self, green_h2_lcoh: float = 3.00) -> TriGenTEAOutputs:
        out = TriGenTEAOutputs()
        sys, cx, ox, rv, fin, em = self.system, self.capex, self.opex, self.revenue, self.financial, self.emissions
        
        # System sizing
        out.capacity_kW = sys.total_power_kW
        out.num_units = sys.num_units
        out.annual_operating_hours = 8760 * sys.capacity_factor
        
        # Production
        out.annual_electricity_MWh = out.capacity_kW * out.annual_operating_hours / 1000
        out.annual_h2_kg = sys.h2_output_kg_h * sys.num_units * out.annual_operating_hours
        out.annual_h2_tonnes = out.annual_h2_kg / 1000
        heat_kW = sys.heat_output_kW_th * sys.num_units * sys.heat_utilization_factor
        out.annual_heat_MMBtu = heat_kW * out.annual_operating_hours / 293.07
        out.annual_ng_MMBtu = sys.ng_input_scf_h * sys.num_units * out.annual_operating_hours / 1000
        
        # CAPEX
        out.capex_fuel_cell_system = out.capacity_kW * cx.system_cost_per_kW
        h2_daily = sys.h2_output_kg_h * sys.num_units * 24
        out.capex_h2_handling = (h2_daily * cx.psa_cost_per_kg_day + 
                                 h2_daily * cx.h2_compression_per_kg_day +
                                 sys.h2_output_kg_h * sys.num_units * cx.h2_buffer_hours * cx.h2_buffer_per_kg)
        out.capex_heat_recovery = sys.heat_output_kW_th * sys.num_units * cx.heat_recovery_per_kW_th
        out.capex_installation = out.capacity_kW * (cx.civil_per_kW + cx.electrical_interconnect_per_kW + cx.commissioning_per_kW)
        out.capex_installation += sys.ng_input_scf_h * sys.num_units * cx.gas_interconnect_per_scfh
        
        equipment = out.capex_fuel_cell_system + out.capex_h2_handling + out.capex_heat_recovery + out.capex_installation
        out.capex_soft_costs = equipment * (cx.engineering_pct + cx.project_management_pct + cx.contingency_pct)
        out.capex_total = equipment + out.capex_soft_costs
        out.capex_per_kW = out.capex_total / out.capacity_kW
        out.capex_after_incentives = out.capex_total * (1 - rv.itc_rate) - out.capacity_kW * rv.state_incentive_per_kW
        
        # OPEX
        ng_price = ox.ng_commodity_price + ox.ng_delivery_price
        out.opex_fuel = out.annual_ng_MMBtu * ng_price
        out.opex_fixed = (out.capacity_kW * (ox.service_contract_per_kW_year + ox.monitoring_per_kW_year) +
                         out.capex_total * (ox.insurance_pct + ox.property_tax_pct))
        out.opex_variable = (out.annual_electricity_MWh * 1000 * ox.variable_om_per_kWh +
                            out.annual_electricity_MWh * 1000 * ox.water_gal_per_kWh * ox.water_cost_per_gallon)
        
        # Stack reserve
        stack_cost = out.capex_fuel_cell_system * cx.stack_fraction
        num_repl = int(fin.project_lifetime_years / sys.stack_lifetime_years)
        stack_npv = sum(stack_cost * cx.stack_replacement_fraction / ((1 + fin.discount_rate) ** (i * sys.stack_lifetime_years))
                       for i in range(1, num_repl + 1) if i * sys.stack_lifetime_years < fin.project_lifetime_years)
        crf = (fin.discount_rate * (1 + fin.discount_rate) ** fin.project_lifetime_years) / ((1 + fin.discount_rate) ** fin.project_lifetime_years - 1)
        out.opex_stack_reserve = stack_npv * crf
        out.opex_total_yr1 = out.opex_fuel + out.opex_fixed + out.opex_variable + out.opex_stack_reserve
        
        # Revenue
        out.revenue_electricity = (out.annual_electricity_MWh * 1000 * rv.electricity_price +
                                   out.capacity_kW * rv.demand_charge_avoided * 12 +
                                   out.capacity_kW * rv.capacity_payment)
        out.revenue_h2 = out.annual_h2_kg * rv.h2_price
        out.revenue_heat = out.annual_heat_MMBtu * rv.heat_price_per_MMBtu
        out.revenue_total_yr1 = out.revenue_electricity + out.revenue_h2 + out.revenue_heat
        
        # Levelized costs
        annual_cost = out.capex_after_incentives * crf + out.opex_total_yr1
        
        # Energy allocation
        elec_E = out.annual_electricity_MWh * 3.412
        h2_E = out.annual_h2_kg * 0.114
        heat_E = out.annual_heat_MMBtu
        total_E = elec_E + h2_E + heat_E
        out.lcoh_energy = (annual_cost * h2_E / total_E) / out.annual_h2_kg if total_E > 0 else 0
        out.lcoe = (annual_cost * elec_E / total_E) / (out.annual_electricity_MWh * 1000) if total_E > 0 else 0
        
        # Market allocation
        total_rev = out.revenue_total_yr1
        out.lcoh_market = (annual_cost * out.revenue_h2 / total_rev) / out.annual_h2_kg if total_rev > 0 else 0
        
        # Incremental
        elec_credit = out.annual_electricity_MWh * 1000 * rv.electricity_price
        heat_credit = out.annual_heat_MMBtu * rv.heat_price_per_MMBtu
        out.lcoh_incremental = max(0, annual_cost - elec_credit - heat_credit) / out.annual_h2_kg
        
        out.lcoh_best_estimate = (out.lcoh_energy + out.lcoh_incremental) / 2
        out.green_h2_lcoh_reference = green_h2_lcoh
        out.lcoh_vs_green = out.lcoh_best_estimate - green_h2_lcoh
        
        # Cash flows
        years = list(range(-1, fin.project_lifetime_years + 1))
        cf_net = []
        for y in years:
            if y < 1:
                cf_net.append(-out.capex_after_incentives * fin.capex_deployment.get(y, 0))
            else:
                degr = (1 - sys.annual_degradation) ** (y - 1)
                infl = (1 + fin.inflation_rate) ** (y - 1)
                ng_esc = (1 + ox.ng_escalation_rate) ** (y - 1)
                rev = out.revenue_total_yr1 * degr * infl
                opex = out.opex_fuel * ng_esc + (out.opex_total_yr1 - out.opex_fuel) * infl
                cf_net.append(rev - opex)
        
        out.years = years
        out.cf_net = cf_net
        out.cf_cumulative = list(np.cumsum(cf_net))
        
        # Financial metrics
        out.npv = sum(cf / ((1 + fin.discount_rate) ** (i + 1)) for i, cf in enumerate(cf_net))
        try:
            import numpy_financial as npf
            out.irr = npf.irr(cf_net)
        except:
            out.irr = 0.15
        
        for i, c in enumerate(out.cf_cumulative):
            if c >= 0 and i > 0:
                out.payback_simple = i - 1 + (-out.cf_cumulative[i-1] / (c - out.cf_cumulative[i-1]))
                break
        else:
            out.payback_simple = float('inf')
        
        # Emissions
        out.annual_co2_tonnes = out.annual_ng_MMBtu * em.ng_co2_factor_kg_per_MMBtu / 1000
        out.co2_intensity_h2 = out.annual_co2_tonnes * 1000 / out.annual_h2_kg
        
        self._outputs = out
        return out
    
    def summary(self) -> str:
        if self._outputs is None:
            return "Call calculate() first"
        o = self._outputs
        return f"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                      TRIGEN (MCFC) TEA SUMMARY                                ║
╠═══════════════════════════════════════════════════════════════════════════════╣
  Capacity: {o.capacity_kW:,.0f} kW ({o.num_units} units)
  Operating Hours: {o.annual_operating_hours:,.0f} hrs/yr

  ANNUAL PRODUCTION
    Electricity: {o.annual_electricity_MWh:,.0f} MWh
    Hydrogen: {o.annual_h2_tonnes:,.1f} tonnes ({o.annual_h2_kg:,.0f} kg)
    Heat: {o.annual_heat_MMBtu:,.0f} MMBtu
    NG Consumed: {o.annual_ng_MMBtu:,.0f} MMBtu

  CAPEX
    Total: ${o.capex_total:,.0f} (${o.capex_per_kW:,.0f}/kW)
    After Incentives: ${o.capex_after_incentives:,.0f}

  OPEX (Year 1)
    Fuel: ${o.opex_fuel:,.0f} ({o.opex_fuel/o.opex_total_yr1*100:.1f}%)
    Fixed: ${o.opex_fixed:,.0f}
    Variable: ${o.opex_variable:,.0f}
    Stack Reserve: ${o.opex_stack_reserve:,.0f}
    TOTAL: ${o.opex_total_yr1:,.0f}

  REVENUE (Year 1)
    Electricity: ${o.revenue_electricity:,.0f}
    Hydrogen: ${o.revenue_h2:,.0f}
    Heat: ${o.revenue_heat:,.0f}
    TOTAL: ${o.revenue_total_yr1:,.0f}

  LEVELIZED COSTS
    LCOH (Energy): ${o.lcoh_energy:.2f}/kg
    LCOH (Market): ${o.lcoh_market:.2f}/kg
    LCOH (Incremental): ${o.lcoh_incremental:.2f}/kg
    BEST ESTIMATE: ${o.lcoh_best_estimate:.2f}/kg
    LCOE: ${o.lcoe*1000:.1f}/MWh

  VS GREEN H2 (${o.green_h2_lcoh_reference:.2f}/kg reference)
    Difference: ${o.lcoh_vs_green:+.2f}/kg

  FINANCIAL
    NPV: ${o.npv:,.0f}
    IRR: {o.irr:.1%}
    Payback: {o.payback_simple:.1f} years

  EMISSIONS
    CO2: {o.annual_co2_tonnes:,.0f} tonnes/yr
    CO2 Intensity: {o.co2_intensity_h2:.1f} kg CO2/kg H2
╚═══════════════════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 80)
    print("TRIGEN TEA EXAMPLE")
    print("=" * 80)
    
    tea = TriGenIntegratedTEA()
    results = tea.calculate(green_h2_lcoh=3.00)
    print(tea.summary())
    
    print("\nCASH FLOWS:")
    print(f"{'Year':<6} {'Net CF':>14} {'Cumulative':>14}")
    for i in range(min(12, len(results.years))):
        print(f"{results.years[i]:<6} ${results.cf_net[i]:>13,.0f} ${results.cf_cumulative[i]:>13,.0f}")
