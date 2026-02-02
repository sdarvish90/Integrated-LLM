"""
PEM Fuel Cell Module

This module handles the fuel cell that converts excess hydrogen to electricity.
H2 from LP buffer (when exceeding station demand) → Fuel Cell → Electricity + Heat

CRITICAL REVIEW NOTES:
1. The fuel cell only receives H2 from the LP buffer, NOT from compressed storage.
   This is thermodynamically sensible - why compress then decompress?
2. Electrical efficiency of 50% (LHV) is typical for stationary PEM fuel cells.
3. Thermal efficiency of 30% for heat recovery is conservative.
4. BOP parasitic load of 2% is reasonable for air blower, pumps, controls.
5. The fuel cell produces low-grade heat (~80°C exhaust) which may have
   limited utility compared to TriGen's high-temperature heat.
"""

from dataclasses import dataclass
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FuelCellConfig


@dataclass
class FuelCellOutputs:
    """Output results from Fuel Cell calculations."""
    
    # H2 consumption
    H2_input_kg_h: float
    H2_input_kg_day: float
    
    # Electrical output
    power_gross_kW: float  # Gross electrical output
    power_BOP_kW: float  # Parasitic BOP consumption
    power_net_kW: float  # Net electrical output
    electricity_net_kWh_day: float  # Daily net electricity
    
    # Thermal output
    heat_output_kW: float  # Recoverable thermal power
    heat_output_kWh_day: float  # Daily heat output
    
    # Efficiency
    efficiency_electrical: float  # LHV basis
    efficiency_thermal: float  # Heat recovery efficiency
    efficiency_total: float  # CHP efficiency
    
    # Air consumption
    air_flow_kg_h: float  # Air mass flow
    O2_consumed_kg_h: float  # Oxygen consumed
    
    # Byproducts
    water_produced_kg_h: float  # Product water


class FuelCell:
    """
    PEM Fuel Cell System.
    
    Converts hydrogen to electricity via electrochemical reaction:
    2H2 + O2 → 2H2O + electricity + heat
    
    This fuel cell receives EXCESS hydrogen from the LP buffer
    (i.e., on-site production that exceeds station demand).
    
    Key characteristics:
    - High electrical efficiency (~50% LHV)
    - Rapid load following
    - Low-grade waste heat (~80°C)
    - Zero direct emissions (only water)
    """
    
    # Physical constants
    LHV_H2_kWh_per_kg = 33.33
    HHV_H2_kWh_per_kg = 39.4
    O2_per_H2_mass_ratio = 8.0  # Stoichiometric: 32/(2*2) = 8
    H2O_per_H2_mass_ratio = 9.0  # Stoichiometric: 36/4 = 9
    
    def __init__(self, config: Optional[FuelCellConfig] = None):
        self.config = config or FuelCellConfig()
        self._outputs: Optional[FuelCellOutputs] = None
        self._issues: list = []
    
    @property
    def outputs(self) -> FuelCellOutputs:
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> list:
        return self._issues
    
    def calculate(self, H2_input_kg_day: float = 0) -> FuelCellOutputs:
        """
        Run fuel cell calculations.
        
        Args:
            H2_input_kg_day: Hydrogen input from LP buffer excess (kg/day)
        
        Returns:
            FuelCellOutputs with all calculated values.
        """
        self._issues = []
        cfg = self.config
        
        # Handle zero input
        if H2_input_kg_day == 0:
            self._outputs = self._zero_output()
            return self._outputs
        
        # ============================================================
        # H2 CONSUMPTION
        # ============================================================
        
        H2_input_kg_h = H2_input_kg_day / 24
        
        # ============================================================
        # ELECTRICAL OUTPUT
        # ============================================================
        
        # Gross power: H2 flow * LHV * electrical efficiency
        # Excel C40: =C23*C26*C24
        power_gross_kW = H2_input_kg_h * cfg.LHV_H2_kWh_per_kg * cfg.eta_fc_el
        
        # BOP parasitic load
        # Excel C42: =C31*C41 (BOP fraction * gross energy)
        power_BOP_kW = power_gross_kW * cfg.fc_BOP_fraction
        
        # Net power
        # Excel C43: =C41-C42
        power_net_kW = power_gross_kW - power_BOP_kW
        
        # Daily electricity
        electricity_net_kWh_day = power_net_kW * 24
        
        # ============================================================
        # THERMAL OUTPUT
        # ============================================================
        
        # Recoverable heat
        # Excel C46: =C23*C26*C25
        heat_output_kW = H2_input_kg_h * cfg.LHV_H2_kWh_per_kg * cfg.eta_fc_th
        heat_output_kWh_day = heat_output_kW * 24
        
        # ============================================================
        # EFFICIENCY
        # ============================================================
        
        efficiency_electrical = cfg.eta_fc_el
        efficiency_thermal = cfg.eta_fc_th
        efficiency_total = efficiency_electrical + efficiency_thermal
        
        # VALIDATION: Total efficiency should be < 1
        if efficiency_total > 0.95:
            self._issues.append(
                f"Total CHP efficiency {efficiency_total:.0%} seems too high. "
                f"Check electrical ({efficiency_electrical:.0%}) and thermal "
                f"({efficiency_thermal:.0%}) efficiency assumptions."
            )
        
        # VALIDATION: Energy balance
        energy_in = H2_input_kg_h * cfg.LHV_H2_kWh_per_kg
        energy_out = power_gross_kW + heat_output_kW
        energy_loss = energy_in - energy_out
        if energy_loss < 0:
            self._issues.append(
                f"Energy output ({energy_out:.2f} kW) exceeds input ({energy_in:.2f} kW). "
                f"This violates thermodynamics. Check efficiency values."
            )
        
        # ============================================================
        # AIR CONSUMPTION
        # ============================================================
        
        # Stoichiometric O2 requirement
        # Excel C47: =8*C23 (mass ratio)
        O2_stoich_kg_h = self.O2_per_H2_mass_ratio * H2_input_kg_h
        
        # Actual O2 with excess air (lambda > 1 for complete reaction)
        # Excel C48: =C33*C47
        O2_consumed_kg_h = O2_stoich_kg_h * cfg.lambda_air
        
        # Air mass flow
        # Excel C49: =C48/C34 (O2 / O2_mass_fraction_in_air)
        air_flow_kg_h = O2_consumed_kg_h / cfg.x_O2_air_mass
        
        # ============================================================
        # WATER PRODUCTION
        # ============================================================
        
        # Product water (stoichiometric)
        water_produced_kg_h = self.H2O_per_H2_mass_ratio * H2_input_kg_h
        
        # ============================================================
        # BUILD OUTPUT
        # ============================================================
        
        self._outputs = FuelCellOutputs(
            H2_input_kg_h=H2_input_kg_h,
            H2_input_kg_day=H2_input_kg_day,
            power_gross_kW=power_gross_kW,
            power_BOP_kW=power_BOP_kW,
            power_net_kW=power_net_kW,
            electricity_net_kWh_day=electricity_net_kWh_day,
            heat_output_kW=heat_output_kW,
            heat_output_kWh_day=heat_output_kWh_day,
            efficiency_electrical=efficiency_electrical,
            efficiency_thermal=efficiency_thermal,
            efficiency_total=efficiency_total,
            air_flow_kg_h=air_flow_kg_h,
            O2_consumed_kg_h=O2_consumed_kg_h,
            water_produced_kg_h=water_produced_kg_h,
        )
        
        return self._outputs
    
    def _zero_output(self) -> FuelCellOutputs:
        """Return zero outputs when no H2 input."""
        cfg = self.config
        return FuelCellOutputs(
            H2_input_kg_h=0, H2_input_kg_day=0,
            power_gross_kW=0, power_BOP_kW=0, power_net_kW=0,
            electricity_net_kWh_day=0,
            heat_output_kW=0, heat_output_kWh_day=0,
            efficiency_electrical=cfg.eta_fc_el,
            efficiency_thermal=cfg.eta_fc_th,
            efficiency_total=cfg.eta_fc_el + cfg.eta_fc_th,
            air_flow_kg_h=0, O2_consumed_kg_h=0, water_produced_kg_h=0,
        )
    
    def summary(self) -> str:
        """Return formatted summary."""
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"  ⚠ {issue}" for issue in self._issues) if self._issues else "  None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                   FUEL CELL SUMMARY                              ║
╠══════════════════════════════════════════════════════════════════╣
║  H2 CONSUMPTION                                                  ║
║    H2 Input:                 {o.H2_input_kg_h:>12,.2f} kg/h              ║
║    H2 Daily:                 {o.H2_input_kg_day:>12,.2f} kg/day            ║
║                                                                  ║
║  ELECTRICAL OUTPUT                                               ║
║    Gross Power:              {o.power_gross_kW:>12,.2f} kW               ║
║    BOP Parasitic:            {o.power_BOP_kW:>12,.2f} kW               ║
║    Net Power:                {o.power_net_kW:>12,.2f} kW               ║
║    Daily Electricity:        {o.electricity_net_kWh_day:>12,.2f} kWh/day          ║
║                                                                  ║
║  THERMAL OUTPUT                                                  ║
║    Heat Output:              {o.heat_output_kW:>12,.2f} kW_th            ║
║    Daily Heat:               {o.heat_output_kWh_day:>12,.2f} kWh_th/day       ║
║                                                                  ║
║  EFFICIENCY                                                      ║
║    Electrical (LHV):         {o.efficiency_electrical:>12,.1%}                 ║
║    Thermal:                  {o.efficiency_thermal:>12,.1%}                 ║
║    Total CHP:                {o.efficiency_total:>12,.1%}                 ║
║                                                                  ║
║  MASS FLOWS                                                      ║
║    Air Consumption:          {o.air_flow_kg_h:>12,.2f} kg/h              ║
║    Water Produced:           {o.water_produced_kg_h:>12,.2f} kg/h              ║
╠══════════════════════════════════════════════════════════════════╣
║  ISSUES IDENTIFIED                                               ║
{issues_str}
╚══════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 70)
    print("FUEL CELL MODULE VALIDATION")
    print("=" * 70)
    
    # Test with some excess H2
    fc = FuelCell()
    outputs = fc.calculate(H2_input_kg_day=100)
    
    print(fc.summary())
