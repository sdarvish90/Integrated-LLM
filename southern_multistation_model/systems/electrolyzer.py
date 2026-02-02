"""
PEM Electrolyzer Module

This module replicates the Electrolyzer calculations from the Electrolyzer_SMR sheet.
PEM Electrolysis splits water into hydrogen and oxygen using electricity.

CRITICAL REVIEW NOTES:
1. The specific energy consumption of 52-53 kWh/kg H2 is reasonable for current
   PEM technology (theoretical minimum is ~39 kWh/kg based on HHV).
2. Water consumption of 10 kg/kg H2 is higher than stoichiometric (9 kg/kg)
   to account for purification losses and makeup.
3. Waste heat recovery at 40% is optimistic - depends heavily on system design.
"""

from dataclasses import dataclass
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ElectrolyzerConfig


@dataclass
class ElectrolyzerOutputs:
    """Output results from Electrolyzer calculations."""
    
    # Production rates
    H2_output_kg_h: float  # Hydrogen production rate
    H2_output_kg_day: float  # Daily hydrogen production
    
    # Electricity consumption
    power_DC_kW: float  # Instantaneous DC power draw
    electricity_kWh_day: float  # Daily electricity consumption
    SEC_kWh_per_kg: float  # Specific energy consumption
    
    # Water consumption
    water_input_kg_h: float  # Water mass flow
    water_input_m3_h: float  # Water volumetric flow
    water_input_gpm: float  # Water flow in GPM
    water_input_gallons_day: float  # Daily water consumption
    water_buffer_volume_gallons: float  # Required buffer tank size
    
    # Oxygen byproduct
    O2_output_kg_h: float  # Oxygen production rate
    O2_output_kg_day: float  # Daily oxygen production
    
    # Heat
    waste_heat_kW: float  # Low-grade waste heat
    waste_heat_recovered_kWh_day: float  # Recoverable waste heat daily
    
    # Efficiency
    efficiency_HHV: float  # Efficiency based on HHV
    efficiency_LHV: float  # Efficiency based on LHV


class Electrolyzer:
    """
    PEM Electrolyzer System.
    
    Produces hydrogen via Proton Exchange Membrane electrolysis:
    2H2O → 2H2 + O2
    
    Key characteristics:
    - High purity H2 output (>99.99%)
    - Rapid response to load changes
    - Operates at 50-80°C
    - Requires deionized water
    """
    
    # Physical constants
    H2_HHV_kWh_per_kg = 39.4  # Higher heating value
    H2_LHV_kWh_per_kg = 33.33  # Lower heating value
    STOICH_WATER_kg_per_kgH2 = 9.0  # 2H2O → 2H2 + O2, MW ratio = 18/2 = 9
    STOICH_O2_kg_per_kgH2 = 8.0  # MW ratio = 32/4 = 8
    
    def __init__(self, config: Optional[ElectrolyzerConfig] = None):
        self.config = config or ElectrolyzerConfig()
        self._outputs: Optional[ElectrolyzerOutputs] = None
        self._issues: list = []
    
    @property
    def outputs(self) -> ElectrolyzerOutputs:
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> list:
        """Return list of any physical/engineering issues identified."""
        return self._issues
    
    def calculate(self, H2_rate_tpd: Optional[float] = None) -> ElectrolyzerOutputs:
        """
        Run electrolyzer mass and energy balance.
        
        Args:
            H2_rate_tpd: Hydrogen production rate in ton/day. If None, uses config.
        
        Returns:
            ElectrolyzerOutputs with all calculated values.
        """
        self._issues = []
        cfg = self.config
        
        # Get H2 production rate
        H2_tpd = H2_rate_tpd if H2_rate_tpd is not None else cfg.design_H2_rate_tpd
        
        # Handle zero production
        if H2_tpd == 0:
            self._outputs = self._zero_output()
            return self._outputs
        
        # ============================================================
        # HYDROGEN PRODUCTION
        # ============================================================
        
        # Excel K8: =K7*1000/24
        H2_output_kg_h = H2_tpd * 1000 / 24
        H2_output_kg_day = H2_tpd * 1000
        
        # ============================================================
        # ELECTRICITY CONSUMPTION
        # ============================================================
        
        # Specific energy consumption (including water treatment overhead)
        # Excel K11: =K9*(1+K10)
        # K9 = PEM_kWhDC_per_kgH2_core = 52
        # K10 = water_treatment_overhead_frac = 0.02
        SEC_kWh_per_kg = cfg.PEM_kWh_DC_per_kg_H2_core * (1 + cfg.water_treatment_overhead_frac)
        
        # Power consumption
        # Excel K33: =K8*K11
        power_DC_kW = H2_output_kg_h * SEC_kWh_per_kg
        
        # Daily electricity
        # Excel K34: =K33*24
        electricity_kWh_day = power_DC_kW * 24
        
        # VALIDATION: Check efficiency
        efficiency_HHV = cfg.H2_HHV_kWh_per_kg / SEC_kWh_per_kg
        efficiency_LHV = self.H2_LHV_kWh_per_kg / SEC_kWh_per_kg
        
        if efficiency_HHV > 0.85:
            self._issues.append(
                f"Electrolyzer efficiency {efficiency_HHV:.1%} (HHV basis) exceeds typical maximum ~85%. "
                f"Check SEC assumption of {SEC_kWh_per_kg} kWh/kg."
            )
        if efficiency_HHV < 0.55:
            self._issues.append(
                f"Electrolyzer efficiency {efficiency_HHV:.1%} (HHV basis) is below typical minimum ~55%. "
                f"This would indicate very old or inefficient equipment."
            )
        
        # ============================================================
        # WATER CONSUMPTION
        # ============================================================
        
        # Water input rate
        # Excel K25: =K8*K12
        water_input_kg_h = H2_output_kg_h * cfg.water_kg_per_kg_H2
        
        # VALIDATION: Check water consumption ratio
        if cfg.water_kg_per_kg_H2 < self.STOICH_WATER_kg_per_kgH2:
            self._issues.append(
                f"Water consumption {cfg.water_kg_per_kg_H2} kg/kgH2 is below stoichiometric minimum "
                f"of {self.STOICH_WATER_kg_per_kgH2} kg/kgH2. This is physically impossible."
            )
        
        # Volumetric flow
        # Excel K26: =K25/1000 (assuming water density ~1000 kg/m3)
        water_input_m3_h = water_input_kg_h / 1000
        
        # GPM conversion
        # Excel K27: =K26/60*K14
        water_input_gpm = water_input_m3_h * cfg.m3_to_gallon / 60
        
        # Daily water
        water_input_gallons_day = water_input_kg_h * 24 / 1000 * cfg.m3_to_gallon
        
        # Buffer tank sizing
        # Excel K29: =K26*K28*K14
        water_buffer_volume_gallons = water_input_m3_h * cfg.water_buffer_hours * cfg.m3_to_gallon
        
        # ============================================================
        # OXYGEN BYPRODUCT
        # ============================================================
        
        # O2 production (stoichiometric)
        # Excel K39: =K38*K8 where K38 = O2_H2_mass_ratio = 8
        O2_output_kg_h = H2_output_kg_h * self.STOICH_O2_kg_per_kgH2
        O2_output_kg_day = O2_output_kg_h * 24
        
        # ============================================================
        # WASTE HEAT
        # ============================================================
        
        # Waste heat intensity
        # Excel K18: = K11 - K13 (SEC minus H2 HHV)
        # This represents energy that doesn't end up in H2 chemical energy
        waste_heat_intensity = SEC_kWh_per_kg - cfg.H2_HHV_kWh_per_kg
        
        # Instantaneous waste heat
        # Excel K19: =K18*K8
        waste_heat_kW = waste_heat_intensity * H2_output_kg_h
        
        # Recoverable waste heat (with recovery fraction)
        # Excel K21: =K19*K20*24
        waste_heat_recovered_kWh_day = waste_heat_kW * cfg.recovery_fraction * 24
        
        # VALIDATION: Energy balance check
        energy_in = power_DC_kW
        energy_out_H2 = H2_output_kg_h * cfg.H2_HHV_kWh_per_kg
        energy_waste = waste_heat_kW
        energy_balance_error = abs(energy_in - energy_out_H2 - energy_waste)
        
        if energy_balance_error > 0.1 * energy_in:
            self._issues.append(
                f"Energy balance error: {energy_balance_error:.2f} kW "
                f"({energy_balance_error/energy_in:.1%} of input). Check calculations."
            )
        
        # ============================================================
        # BUILD OUTPUT
        # ============================================================
        
        self._outputs = ElectrolyzerOutputs(
            H2_output_kg_h=H2_output_kg_h,
            H2_output_kg_day=H2_output_kg_day,
            power_DC_kW=power_DC_kW,
            electricity_kWh_day=electricity_kWh_day,
            SEC_kWh_per_kg=SEC_kWh_per_kg,
            water_input_kg_h=water_input_kg_h,
            water_input_m3_h=water_input_m3_h,
            water_input_gpm=water_input_gpm,
            water_input_gallons_day=water_input_gallons_day,
            water_buffer_volume_gallons=water_buffer_volume_gallons,
            O2_output_kg_h=O2_output_kg_h,
            O2_output_kg_day=O2_output_kg_day,
            waste_heat_kW=waste_heat_kW,
            waste_heat_recovered_kWh_day=waste_heat_recovered_kWh_day,
            efficiency_HHV=efficiency_HHV,
            efficiency_LHV=efficiency_LHV,
        )
        
        return self._outputs
    
    def _zero_output(self) -> ElectrolyzerOutputs:
        """Return zero outputs when H2 production is zero."""
        return ElectrolyzerOutputs(
            H2_output_kg_h=0, H2_output_kg_day=0,
            power_DC_kW=0, electricity_kWh_day=0, SEC_kWh_per_kg=0,
            water_input_kg_h=0, water_input_m3_h=0, water_input_gpm=0,
            water_input_gallons_day=0, water_buffer_volume_gallons=0,
            O2_output_kg_h=0, O2_output_kg_day=0,
            waste_heat_kW=0, waste_heat_recovered_kWh_day=0,
            efficiency_HHV=0, efficiency_LHV=0,
        )
    
    def summary(self) -> str:
        """Return formatted summary of Electrolyzer outputs."""
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"  ⚠ {issue}" for issue in self._issues) if self._issues else "  None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                  ELECTROLYZER SYSTEM SUMMARY                     ║
╠══════════════════════════════════════════════════════════════════╣
║  HYDROGEN PRODUCTION                                             ║
║    H2 Output:                {o.H2_output_kg_h:>12,.2f} kg/h              ║
║    H2 Daily:                 {o.H2_output_kg_day:>12,.2f} kg/day            ║
║                                                                  ║
║  ELECTRICITY                                                     ║
║    Power Draw (DC):          {o.power_DC_kW:>12,.2f} kW               ║
║    Daily Consumption:        {o.electricity_kWh_day:>12,.2f} kWh/day          ║
║    Specific Energy:          {o.SEC_kWh_per_kg:>12,.2f} kWh/kg            ║
║    Efficiency (HHV):         {o.efficiency_HHV:>12,.1%}                 ║
║    Efficiency (LHV):         {o.efficiency_LHV:>12,.1%}                 ║
║                                                                  ║
║  WATER                                                           ║
║    Water Input:              {o.water_input_kg_h:>12,.2f} kg/h              ║
║    Water Daily:              {o.water_input_gallons_day:>12,.0f} gal/day           ║
║    Buffer Tank:              {o.water_buffer_volume_gallons:>12,.0f} gallons          ║
║                                                                  ║
║  BYPRODUCTS                                                      ║
║    O2 Output:                {o.O2_output_kg_h:>12,.2f} kg/h              ║
║    Waste Heat:               {o.waste_heat_kW:>12,.2f} kW_th            ║
║    Recoverable Heat:         {o.waste_heat_recovered_kWh_day:>12,.2f} kWh_th/day       ║
╠══════════════════════════════════════════════════════════════════╣
║  ISSUES IDENTIFIED                                               ║
{issues_str}
╚══════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 70)
    print("ELECTROLYZER MODULE VALIDATION")
    print("=" * 70)
    
    # Test with 0.5 tpd (same as Excel default)
    elec = Electrolyzer()
    outputs = elec.calculate(H2_rate_tpd=0.5)
    
    print(elec.summary())
    
    # Compare with Excel
    print("\nComparison with Excel (Electrolyzer_SMR sheet, Electrolyzer mode):")
    print("-" * 60)
    print(f"H2 output: {outputs.H2_output_kg_h:.4f} kg/h")
    print(f"Power DC: {outputs.power_DC_kW:.4f} kW")
    print(f"SEC: {outputs.SEC_kWh_per_kg:.4f} kWh/kg (Excel K11: 53.04)")
    print(f"Electricity/day: {outputs.electricity_kWh_day:.4f} kWh/day")
