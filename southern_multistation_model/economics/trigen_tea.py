"""
TriGen Techno-Economic Analysis (TEA) Module

This module performs comprehensive techno-economic analysis for the TriGen MCFC system,
calculating CAPEX, OPEX, LCOE, LCOH, and financial metrics.

Key Economic Parameters (based on FuelCell Energy SureSource data and industry benchmarks):
- CAPEX: $4,000-6,000/kW installed (includes BOP, installation)
- Stack replacement: Every 5-7 years, ~40% of initial CAPEX
- Electrical efficiency: 47-52% (LHV)
- Availability: 95%+ after initial commissioning
- Lifetime: 20-25 years (with stack replacements)

References:
- FuelCell Energy investor presentations and 10-K filings
- DOE Hydrogen and Fuel Cells Program Record
- NREL ATB (Annual Technology Baseline)
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
import math


@dataclass
class TriGenCostInputs:
    """
    Cost and financial inputs for TriGen TEA.
    
    All costs in USD, rates as decimals.
    """
    # ============================================================
    # CAPITAL COSTS
    # ============================================================
    
    # System CAPEX ($/kW rated capacity)
    capex_per_kW: float = 5000  # Installed cost including BOP
    
    # Installation and commissioning (fraction of equipment cost)
    installation_factor: float = 0.15  # 15% for site prep, interconnection
    
    # Engineering and project management
    engineering_factor: float = 0.10  # 10% soft costs
    
    # Contingency
    contingency_factor: float = 0.10  # 10% contingency
    
    # Stack replacement cost (fraction of initial system cost)
    stack_replacement_fraction: float = 0.40  # Stack is ~40% of system
    
    # Stack replacement interval (years)
    stack_life_years: float = 7  # Typical for MCFC
    
    # ============================================================
    # OPERATING COSTS
    # ============================================================
    
    # Natural gas price ($/MMBtu)
    ng_price_per_MMBtu: float = 4.00  # Henry Hub + delivery
    
    # Electricity price for comparison/backup ($/kWh)
    electricity_price_per_kWh: float = 0.08
    
    # H2 market price for comparison ($/kg)
    h2_market_price_per_kg: float = 6.00  # Gray H2 benchmark
    
    # Heat value if sold ($/MMBtu thermal)
    heat_value_per_MMBtu: float = 8.00  # ~$2.35/therm equivalent
    
    # O&M costs
    fixed_om_per_kW_year: float = 50  # $/kW-year (labor, insurance, admin)
    variable_om_per_kWh: float = 0.005  # $/kWh (consumables, minor repairs)
    
    # Water cost ($/gallon) - for reformer steam makeup
    water_price_per_gallon: float = 0.005
    
    # ============================================================
    # FINANCIAL PARAMETERS
    # ============================================================
    
    # Project lifetime (years)
    project_life_years: int = 20
    
    # Discount rate / WACC
    discount_rate: float = 0.08  # 8%
    
    # Inflation rate (for escalation)
    inflation_rate: float = 0.025  # 2.5%
    
    # Tax rate
    tax_rate: float = 0.21  # Federal corporate
    
    # Depreciation method and period
    depreciation_years: int = 7  # MACRS 7-year for fuel cells
    
    # Investment Tax Credit (ITC) - if applicable
    itc_rate: float = 0.30  # 30% ITC under IRA for clean energy
    
    # Capacity factor (accounting for maintenance, startup/shutdown)
    capacity_factor: float = 0.92  # 92% typical for baseload MCFC
    
    # Annual degradation rate
    degradation_rate: float = 0.005  # 0.5% per year efficiency loss
    
    # ============================================================
    # CO2 / EMISSIONS
    # ============================================================
    
    # CO2 price ($/ton) - if carbon pricing applies
    co2_price_per_ton: float = 0  # Set to 0 if no carbon price
    
    # Grid electricity CO2 intensity (kg CO2/kWh) for avoided emissions
    grid_co2_intensity: float = 0.4  # US average
    
    # Natural gas CO2 factor (kg CO2/MMBtu)
    ng_co2_factor: float = 53.06  # EPA default


@dataclass
class TriGenTEAOutputs:
    """
    Complete TEA results for TriGen system.
    """
    # System sizing
    rated_capacity_kW: float
    annual_electricity_MWh: float
    annual_H2_production_kg: float
    annual_heat_production_MMBtu: float
    annual_NG_consumption_MMBtu: float
    
    # Capital costs
    total_capex: float
    equipment_cost: float
    installation_cost: float
    engineering_cost: float
    contingency: float
    
    # Annual operating costs
    annual_fuel_cost: float
    annual_fixed_om: float
    annual_variable_om: float
    annual_water_cost: float
    annual_stack_replacement_reserve: float  # Amortized
    total_annual_opex: float
    
    # Revenue streams (if applicable)
    annual_electricity_value: float
    annual_H2_value: float
    annual_heat_value: float
    total_annual_revenue: float
    
    # Levelized costs
    LCOE: float  # $/kWh electricity
    LCOH: float  # $/kg H2 (allocated)
    LCOH_heat: float  # $/MMBtu heat (allocated)
    
    # Financial metrics
    NPV: float  # Net Present Value
    IRR: float  # Internal Rate of Return
    simple_payback_years: float
    discounted_payback_years: float
    
    # Emissions
    annual_CO2_emissions_tons: float
    avoided_CO2_tons: float  # vs grid electricity
    CO2_intensity_kg_per_kWh: float
    
    # Cash flows (for detailed analysis)
    annual_cash_flows: List[float] = field(default_factory=list)
    cumulative_cash_flows: List[float] = field(default_factory=list)


class TriGenTEA:
    """
    Techno-Economic Analysis for TriGen MCFC System.
    
    Calculates:
    - Total installed cost (CAPEX)
    - Operating costs (OPEX) including fuel, O&M, stack replacements
    - Levelized costs (LCOE, LCOH)
    - Financial metrics (NPV, IRR, payback)
    - Emissions and avoided emissions
    
    Cost allocation methodology:
    - For multi-product systems (electricity + H2 + heat), costs are allocated
      based on energy content or market value weighting.
    """
    
    def __init__(self, cost_inputs: Optional[TriGenCostInputs] = None):
        self.costs = cost_inputs or TriGenCostInputs()
        self._outputs: Optional[TriGenTEAOutputs] = None
        self._issues: List[str] = []
    
    @property
    def outputs(self) -> TriGenTEAOutputs:
        if self._outputs is None:
            raise ValueError("TEA not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> List[str]:
        return self._issues
    
    def calculate(self,
                  rated_capacity_kW: float,
                  annual_electricity_MWh: float,
                  annual_H2_kg: float,
                  annual_heat_MMBtu: float,
                  annual_NG_MMBtu: float) -> TriGenTEAOutputs:
        """
        Run complete TEA calculation.
        
        Args:
            rated_capacity_kW: Nameplate capacity of TriGen system (kW)
            annual_electricity_MWh: Annual net electricity production (MWh)
            annual_H2_kg: Annual hydrogen production (kg)
            annual_heat_MMBtu: Annual useful heat production (MMBtu)
            annual_NG_MMBtu: Annual natural gas consumption (MMBtu)
        
        Returns:
            TriGenTEAOutputs with complete economic analysis.
        """
        self._issues = []
        c = self.costs
        
        # ============================================================
        # CAPITAL COSTS
        # ============================================================
        
        # Base equipment cost
        equipment_cost = rated_capacity_kW * c.capex_per_kW
        
        # Installation
        installation_cost = equipment_cost * c.installation_factor
        
        # Engineering/soft costs
        engineering_cost = equipment_cost * c.engineering_factor
        
        # Contingency
        subtotal = equipment_cost + installation_cost + engineering_cost
        contingency = subtotal * c.contingency_factor
        
        # Total CAPEX
        total_capex = subtotal + contingency
        
        # Apply ITC if applicable
        itc_benefit = total_capex * c.itc_rate
        net_capex = total_capex - itc_benefit
        
        # ============================================================
        # OPERATING COSTS
        # ============================================================
        
        # Fuel cost
        annual_fuel_cost = annual_NG_MMBtu * c.ng_price_per_MMBtu
        
        # Fixed O&M
        annual_fixed_om = rated_capacity_kW * c.fixed_om_per_kW_year
        
        # Variable O&M
        annual_variable_om = annual_electricity_MWh * 1000 * c.variable_om_per_kWh
        
        # Water cost (estimate based on steam requirements)
        # Assume ~0.5 gallons per kWh for MCFC steam generation
        annual_water_gallons = annual_electricity_MWh * 1000 * 0.5
        annual_water_cost = annual_water_gallons * c.water_price_per_gallon
        
        # Stack replacement reserve (annualized)
        stack_replacement_cost = equipment_cost * c.stack_replacement_fraction
        num_replacements = c.project_life_years // c.stack_life_years
        total_stack_cost = stack_replacement_cost * num_replacements
        
        # NPV of stack replacements
        stack_npv = 0
        for i in range(1, num_replacements + 1):
            year = i * c.stack_life_years
            if year < c.project_life_years:
                stack_npv += stack_replacement_cost / ((1 + c.discount_rate) ** year)
        
        # Annualized stack reserve
        crf = self._capital_recovery_factor(c.discount_rate, c.project_life_years)
        annual_stack_reserve = stack_npv * crf
        
        # Total OPEX
        total_annual_opex = (annual_fuel_cost + annual_fixed_om + 
                           annual_variable_om + annual_water_cost + 
                           annual_stack_reserve)
        
        # ============================================================
        # REVENUE STREAMS
        # ============================================================
        
        # Electricity value
        annual_electricity_value = annual_electricity_MWh * 1000 * c.electricity_price_per_kWh
        
        # H2 value
        annual_H2_value = annual_H2_kg * c.h2_market_price_per_kg
        
        # Heat value
        annual_heat_value = annual_heat_MMBtu * c.heat_value_per_MMBtu
        
        total_annual_revenue = annual_electricity_value + annual_H2_value + annual_heat_value
        
        # ============================================================
        # LEVELIZED COSTS
        # ============================================================
        
        # Total annual cost (CAPEX annualized + OPEX)
        annualized_capex = net_capex * crf
        total_annual_cost = annualized_capex + total_annual_opex
        
        # Cost allocation based on energy content
        # Electricity: 3.412 MMBtu/MWh
        # H2: 0.114 MMBtu/kg (LHV)
        # Heat: 1 MMBtu/MMBtu
        
        elec_energy_MMBtu = annual_electricity_MWh * 3.412
        H2_energy_MMBtu = annual_H2_kg * 0.114
        heat_energy_MMBtu = annual_heat_MMBtu
        total_energy_MMBtu = elec_energy_MMBtu + H2_energy_MMBtu + heat_energy_MMBtu
        
        if total_energy_MMBtu > 0:
            elec_fraction = elec_energy_MMBtu / total_energy_MMBtu
            H2_fraction = H2_energy_MMBtu / total_energy_MMBtu
            heat_fraction = heat_energy_MMBtu / total_energy_MMBtu
        else:
            elec_fraction = H2_fraction = heat_fraction = 0
        
        # Allocated costs
        elec_cost = total_annual_cost * elec_fraction
        H2_cost = total_annual_cost * H2_fraction
        heat_cost = total_annual_cost * heat_fraction
        
        # LCOE ($/kWh)
        if annual_electricity_MWh > 0:
            LCOE = elec_cost / (annual_electricity_MWh * 1000)
        else:
            LCOE = 0
        
        # LCOH ($/kg)
        if annual_H2_kg > 0:
            LCOH = H2_cost / annual_H2_kg
        else:
            LCOH = 0
        
        # LCOH_heat ($/MMBtu)
        if annual_heat_MMBtu > 0:
            LCOH_heat = heat_cost / annual_heat_MMBtu
        else:
            LCOH_heat = 0
        
        # ============================================================
        # CASH FLOW ANALYSIS
        # ============================================================
        
        annual_cash_flows = []
        cumulative_cash_flows = []
        
        # Year 0: Initial investment
        annual_cash_flows.append(-net_capex)
        cumulative_cash_flows.append(-net_capex)
        
        # Years 1-N
        cumulative = -net_capex
        for year in range(1, c.project_life_years + 1):
            # Apply degradation to production
            degradation_factor = (1 - c.degradation_rate) ** (year - 1)
            
            # Revenue (degraded)
            year_revenue = total_annual_revenue * degradation_factor
            
            # OPEX (fuel scales with degradation, O&M roughly constant)
            year_fuel = annual_fuel_cost * degradation_factor
            year_opex = year_fuel + annual_fixed_om + annual_variable_om * degradation_factor + annual_water_cost
            
            # Stack replacement years
            if year % c.stack_life_years == 0 and year < c.project_life_years:
                year_opex += stack_replacement_cost
            
            # Net cash flow (simplified - no detailed tax calc)
            year_cf = year_revenue - year_opex
            
            annual_cash_flows.append(year_cf)
            cumulative += year_cf
            cumulative_cash_flows.append(cumulative)
        
        # ============================================================
        # FINANCIAL METRICS
        # ============================================================
        
        # NPV
        NPV = self._calculate_npv(annual_cash_flows, c.discount_rate)
        
        # IRR
        IRR = self._calculate_irr(annual_cash_flows)
        
        # Simple payback
        simple_payback = self._calculate_simple_payback(cumulative_cash_flows)
        
        # Discounted payback
        discounted_payback = self._calculate_discounted_payback(annual_cash_flows, c.discount_rate)
        
        # ============================================================
        # EMISSIONS
        # ============================================================
        
        # Direct CO2 emissions from NG
        annual_CO2_tons = (annual_NG_MMBtu * c.ng_co2_factor) / 1000
        
        # Avoided emissions vs grid electricity
        avoided_CO2_tons = (annual_electricity_MWh * 1000 * c.grid_co2_intensity) / 1000 - annual_CO2_tons
        
        # CO2 intensity
        if annual_electricity_MWh > 0:
            CO2_intensity = (annual_CO2_tons * 1000) / (annual_electricity_MWh * 1000)
        else:
            CO2_intensity = 0
        
        # ============================================================
        # VALIDATION
        # ============================================================
        
        if LCOE > 0.20:
            self._issues.append(
                f"LCOE of ${LCOE:.3f}/kWh is high. Check capacity factor "
                f"and NG price assumptions."
            )
        
        if IRR < c.discount_rate:
            self._issues.append(
                f"IRR of {IRR:.1%} is below discount rate of {c.discount_rate:.1%}. "
                f"Project may not be financially viable at current assumptions."
            )
        
        if simple_payback > 10:
            self._issues.append(
                f"Simple payback of {simple_payback:.1f} years exceeds 10 years. "
                f"Consider revenue optimization or cost reduction."
            )
        
        # ============================================================
        # BUILD OUTPUT
        # ============================================================
        
        self._outputs = TriGenTEAOutputs(
            # Sizing
            rated_capacity_kW=rated_capacity_kW,
            annual_electricity_MWh=annual_electricity_MWh,
            annual_H2_production_kg=annual_H2_kg,
            annual_heat_production_MMBtu=annual_heat_MMBtu,
            annual_NG_consumption_MMBtu=annual_NG_MMBtu,
            
            # CAPEX
            total_capex=total_capex,
            equipment_cost=equipment_cost,
            installation_cost=installation_cost,
            engineering_cost=engineering_cost,
            contingency=contingency,
            
            # OPEX
            annual_fuel_cost=annual_fuel_cost,
            annual_fixed_om=annual_fixed_om,
            annual_variable_om=annual_variable_om,
            annual_water_cost=annual_water_cost,
            annual_stack_replacement_reserve=annual_stack_reserve,
            total_annual_opex=total_annual_opex,
            
            # Revenue
            annual_electricity_value=annual_electricity_value,
            annual_H2_value=annual_H2_value,
            annual_heat_value=annual_heat_value,
            total_annual_revenue=total_annual_revenue,
            
            # Levelized costs
            LCOE=LCOE,
            LCOH=LCOH,
            LCOH_heat=LCOH_heat,
            
            # Financial
            NPV=NPV,
            IRR=IRR,
            simple_payback_years=simple_payback,
            discounted_payback_years=discounted_payback,
            
            # Emissions
            annual_CO2_emissions_tons=annual_CO2_tons,
            avoided_CO2_tons=avoided_CO2_tons,
            CO2_intensity_kg_per_kWh=CO2_intensity,
            
            # Cash flows
            annual_cash_flows=annual_cash_flows,
            cumulative_cash_flows=cumulative_cash_flows,
        )
        
        return self._outputs
    
    def _capital_recovery_factor(self, rate: float, years: int) -> float:
        """Calculate capital recovery factor (CRF)."""
        if rate == 0:
            return 1 / years
        return (rate * (1 + rate) ** years) / ((1 + rate) ** years - 1)
    
    def _calculate_npv(self, cash_flows: List[float], rate: float) -> float:
        """Calculate Net Present Value."""
        npv = 0
        for t, cf in enumerate(cash_flows):
            npv += cf / ((1 + rate) ** t)
        return npv
    
    def _calculate_irr(self, cash_flows: List[float], max_iter: int = 1000) -> float:
        """Calculate Internal Rate of Return using Newton-Raphson."""
        # Initial guess
        irr = 0.10
        
        for _ in range(max_iter):
            npv = sum(cf / ((1 + irr) ** t) for t, cf in enumerate(cash_flows))
            dnpv = sum(-t * cf / ((1 + irr) ** (t + 1)) for t, cf in enumerate(cash_flows))
            
            if abs(dnpv) < 1e-10:
                break
            
            new_irr = irr - npv / dnpv
            
            if abs(new_irr - irr) < 1e-6:
                return new_irr
            
            irr = new_irr
            
            # Bounds check
            if irr < -0.99:
                irr = -0.99
            elif irr > 10:
                irr = 10
        
        return irr
    
    def _calculate_simple_payback(self, cumulative_flows: List[float]) -> float:
        """Calculate simple payback period."""
        for i, cf in enumerate(cumulative_flows):
            if cf >= 0:
                if i == 0:
                    return 0
                # Linear interpolation
                prev_cf = cumulative_flows[i - 1]
                fraction = -prev_cf / (cf - prev_cf)
                return (i - 1) + fraction
        return float('inf')
    
    def _calculate_discounted_payback(self, cash_flows: List[float], rate: float) -> float:
        """Calculate discounted payback period."""
        cumulative = 0
        for t, cf in enumerate(cash_flows):
            pv = cf / ((1 + rate) ** t)
            cumulative += pv
            if cumulative >= 0 and t > 0:
                prev_cum = cumulative - pv
                fraction = -prev_cum / pv
                return (t - 1) + fraction
        return float('inf')
    
    def sensitivity_analysis(self, 
                            base_inputs: Dict,
                            parameter: str,
                            range_pct: float = 0.30,
                            steps: int = 11) -> Dict:
        """
        Run sensitivity analysis on a single parameter.
        
        Args:
            base_inputs: Dict with calculate() arguments
            parameter: Cost parameter to vary (e.g., 'ng_price_per_MMBtu')
            range_pct: +/- percentage range (0.30 = ±30%)
            steps: Number of steps
        
        Returns:
            Dict with parameter values and resulting metrics.
        """
        base_value = getattr(self.costs, parameter)
        min_val = base_value * (1 - range_pct)
        max_val = base_value * (1 + range_pct)
        step_size = (max_val - min_val) / (steps - 1)
        
        results = {
            'parameter': parameter,
            'values': [],
            'LCOE': [],
            'LCOH': [],
            'NPV': [],
            'IRR': [],
        }
        
        for i in range(steps):
            test_value = min_val + i * step_size
            setattr(self.costs, parameter, test_value)
            
            outputs = self.calculate(**base_inputs)
            
            results['values'].append(test_value)
            results['LCOE'].append(outputs.LCOE)
            results['LCOH'].append(outputs.LCOH)
            results['NPV'].append(outputs.NPV)
            results['IRR'].append(outputs.IRR)
        
        # Reset to base value
        setattr(self.costs, parameter, base_value)
        
        return results
    
    def summary(self) -> str:
        """Return formatted summary of TEA results."""
        if self._outputs is None:
            return "TEA not calculated. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"    ⚠ {issue}" for issue in self._issues) if self._issues else "    None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              TRIGEN TECHNO-ECONOMIC ANALYSIS SUMMARY                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  SYSTEM SIZING                                                               ║
║    Rated Capacity:              {o.rated_capacity_kW:>12,.0f} kW                          ║
║    Annual Electricity:          {o.annual_electricity_MWh:>12,.0f} MWh/yr                      ║
║    Annual H2 Production:        {o.annual_H2_production_kg:>12,.0f} kg/yr                       ║
║    Annual Heat Production:      {o.annual_heat_production_MMBtu:>12,.0f} MMBtu/yr                    ║
║    Annual NG Consumption:       {o.annual_NG_consumption_MMBtu:>12,.0f} MMBtu/yr                    ║
║                                                                              ║
║  CAPITAL COSTS                                                               ║
║    Equipment:                   ${o.equipment_cost:>12,.0f}                              ║
║    Installation:                ${o.installation_cost:>12,.0f}                              ║
║    Engineering:                 ${o.engineering_cost:>12,.0f}                              ║
║    Contingency:                 ${o.contingency:>12,.0f}                              ║
║    ─────────────────────────────────────────────                             ║
║    TOTAL CAPEX:                 ${o.total_capex:>12,.0f}                              ║
║                                                                              ║
║  ANNUAL OPERATING COSTS                                                      ║
║    Fuel (NG):                   ${o.annual_fuel_cost:>12,.0f}                              ║
║    Fixed O&M:                   ${o.annual_fixed_om:>12,.0f}                              ║
║    Variable O&M:                ${o.annual_variable_om:>12,.0f}                              ║
║    Water:                       ${o.annual_water_cost:>12,.0f}                              ║
║    Stack Reserve:               ${o.annual_stack_replacement_reserve:>12,.0f}                              ║
║    ─────────────────────────────────────────────                             ║
║    TOTAL OPEX:                  ${o.total_annual_opex:>12,.0f} /yr                         ║
║                                                                              ║
║  ANNUAL REVENUE (at market prices)                                           ║
║    Electricity:                 ${o.annual_electricity_value:>12,.0f}                              ║
║    Hydrogen:                    ${o.annual_H2_value:>12,.0f}                              ║
║    Heat:                        ${o.annual_heat_value:>12,.0f}                              ║
║    ─────────────────────────────────────────────                             ║
║    TOTAL REVENUE:               ${o.total_annual_revenue:>12,.0f} /yr                         ║
║                                                                              ║
║  LEVELIZED COSTS (energy-allocated)                                          ║
║    LCOE:                        ${o.LCOE:>12.4f} /kWh                          ║
║    LCOH:                        ${o.LCOH:>12.2f} /kg                           ║
║    LCOH (heat):                 ${o.LCOH_heat:>12.2f} /MMBtu                       ║
║                                                                              ║
║  FINANCIAL METRICS                                                           ║
║    NPV:                         ${o.NPV:>12,.0f}                              ║
║    IRR:                         {o.IRR:>12.1%}                              ║
║    Simple Payback:              {o.simple_payback_years:>12.1f} years                        ║
║    Discounted Payback:          {o.discounted_payback_years:>12.1f} years                        ║
║                                                                              ║
║  EMISSIONS                                                                   ║
║    Annual CO2:                  {o.annual_CO2_emissions_tons:>12,.0f} tons/yr                      ║
║    CO2 Intensity:               {o.CO2_intensity_kg_per_kWh:>12.3f} kg/kWh                       ║
║    Avoided vs Grid:             {o.avoided_CO2_tons:>12,.0f} tons/yr                      ║
║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  ISSUES/WARNINGS                                                             ║
{issues_str}
╚══════════════════════════════════════════════════════════════════════════════╝
"""


# ============================================================
# INTEGRATION WITH TRIGEN MASS/ENERGY BALANCE
# ============================================================

def run_trigen_tea_from_mass_balance(trigen_outputs, cost_inputs: Optional[TriGenCostInputs] = None):
    """
    Convenience function to run TEA using outputs from TriGen mass/energy balance.
    
    Args:
        trigen_outputs: TriGenOutputs from the trigen.py module
        cost_inputs: Optional custom cost inputs
    
    Returns:
        TriGenTEAOutputs with complete economic analysis.
    """
    # Extract annual values from mass/energy balance
    # Assuming trigen_outputs contains daily values
    
    # Operating days per year (accounting for capacity factor)
    operating_days = 365 * 0.92  # 92% capacity factor
    
    # Convert daily to annual
    annual_electricity_MWh = (trigen_outputs.electricity_kWh_day * operating_days) / 1000
    annual_H2_kg = trigen_outputs.H2_output_kg_day * operating_days
    
    # Heat: convert kWh_th to MMBtu (1 MMBtu = 293.07 kWh)
    annual_heat_MMBtu = (trigen_outputs.Q_available_for_export_kW_day * operating_days) / 293.07
    
    # NG: convert scf to MMBtu (1 MMBtu = ~1000 scf for pipeline NG)
    annual_NG_MMBtu = (trigen_outputs.NG_input_scf_day * operating_days) / 1000
    
    # Rated capacity (from power output)
    rated_capacity_kW = trigen_outputs.power_electric_kW
    
    # Run TEA
    tea = TriGenTEA(cost_inputs)
    return tea.calculate(
        rated_capacity_kW=rated_capacity_kW,
        annual_electricity_MWh=annual_electricity_MWh,
        annual_H2_kg=annual_H2_kg,
        annual_heat_MMBtu=annual_heat_MMBtu,
        annual_NG_MMBtu=annual_NG_MMBtu
    ), tea


# ============================================================
# MAIN - Run example TEA
# ============================================================

if __name__ == "__main__":
    print("=" * 80)
    print("TRIGEN TEA MODULE - EXAMPLE CALCULATION")
    print("=" * 80)
    
    # Example: 2.8 MW TriGen system (SureSource 3000)
    # Based on mass/energy balance outputs at 10,700 scf/h NG
    
    # Annual values (365 days * 92% CF)
    operating_days = 365 * 0.92
    
    inputs = {
        'rated_capacity_kW': 1358,  # Net power output
        'annual_electricity_MWh': 30636 * operating_days / 1000,  # ~10,284 MWh/yr
        'annual_H2_kg': 522 * operating_days,  # ~175,306 kg/yr
        'annual_heat_MMBtu': 9942 * operating_days / 293.07,  # ~11,386 MMBtu/yr
        'annual_NG_MMBtu': 256800 * operating_days / 1000,  # ~86,234 MMBtu/yr
    }
    
    # Default cost assumptions
    tea = TriGenTEA()
    outputs = tea.calculate(**inputs)
    
    print(tea.summary())
    
    # Run sensitivity on NG price
    print("\n" + "=" * 80)
    print("SENSITIVITY ANALYSIS: Natural Gas Price")
    print("=" * 80)
    
    sensitivity = tea.sensitivity_analysis(inputs, 'ng_price_per_MMBtu', range_pct=0.50, steps=5)
    
    print(f"\n{'NG Price ($/MMBtu)':<20} {'LCOE ($/kWh)':<15} {'LCOH ($/kg)':<15} {'IRR':<15}")
    print("-" * 65)
    for i in range(len(sensitivity['values'])):
        print(f"${sensitivity['values'][i]:<19.2f} ${sensitivity['LCOE'][i]:<14.4f} ${sensitivity['LCOH'][i]:<14.2f} {sensitivity['IRR'][i]:<14.1%}")
