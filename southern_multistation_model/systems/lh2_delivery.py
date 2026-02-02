"""
LH2 Delivery Module (Pump + Vaporizer)

This module handles liquid hydrogen delivery pathway:
LH2 Trailer → Pump (3 bar → 700 bar) → Vaporizer → HP Storage → Dispenser

CRITICAL REVIEW NOTES:
1. Pump power calculation uses isentropic work formula which is appropriate
   for incompressible LH2. The 65% efficiency is reasonable for cryogenic pumps.
2. Vaporizer heat duty includes both latent heat (0.45 MJ/kg) and sensible
   heat (14 kJ/kg-K from 20K to 300K). This is physically correct.
3. Key advantage: H2 is already at high pressure after pump, so no compression
   needed - goes directly to dispenser (may need pre-cooling only).
4. IMPORTANT: Excel shows this pathway feeds DIRECTLY to dispenser, not through
   the main compressor. This is a separate HP storage.
"""

from dataclasses import dataclass
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LH2DeliveryConfig


@dataclass
class LH2DeliveryOutputs:
    """Output results from LH2 Delivery calculations."""
    
    # Throughput
    H2_throughput_kg_h: float
    H2_throughput_kg_day: float
    H2_output_kg_day: float  # After losses
    
    # Pump
    pump_power_kW: float  # LH2 pump shaft power
    pressure_rise_MPa: float  # Pressure increase
    specific_work_J_kg: float  # Specific pump work
    
    # Vaporizer
    vaporizer_duty_kW: float  # Heat required for vaporization
    delta_h_total_MJ_kg: float  # Total enthalpy change
    
    # Heat supply
    heat_from_trigen_kW: float  # Heat imported from TriGen
    heat_shortfall_kW: float  # Additional heat needed
    NG_aux_heater_scf_h: float  # NG for backup heater (if needed)
    
    # Electricity
    total_power_kW: float  # Total electrical load
    electricity_kWh_day: float  # Daily electricity
    
    # Losses
    boiloff_loss_kg_day: float  # Boil-off/transfer losses


class LH2Delivery:
    """
    Liquid Hydrogen Delivery System.
    
    Pathway: LH2 Trailer → Cryogenic Pump → Vaporizer → HP Storage → Dispenser
    
    Key advantages over gaseous delivery:
    - Higher energy density (70 kg/m³ vs ~15-40 kg/m³ for compressed gas)
    - Pumping liquid requires much less energy than compressing gas
    - Output already at high pressure (700 bar)
    
    Key challenges:
    - Requires heat input for vaporization
    - Boil-off losses during storage and transfer
    - Cryogenic equipment costs
    """
    
    def __init__(self, config: Optional[LH2DeliveryConfig] = None):
        self.config = config or LH2DeliveryConfig()
        self._outputs: Optional[LH2DeliveryOutputs] = None
        self._issues: list = []
    
    @property
    def outputs(self) -> LH2DeliveryOutputs:
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> list:
        return self._issues
    
    def calculate(self,
                  throughput_tpd: Optional[float] = None,
                  Q_from_trigen_kW: float = 0) -> LH2DeliveryOutputs:
        """
        Run LH2 delivery system calculations.
        
        Args:
            throughput_tpd: LH2 throughput in ton/day. If None, uses config.
            Q_from_trigen_kW: Heat available from TriGen (kW_th)
        
        Returns:
            LH2DeliveryOutputs with all calculated values.
        """
        self._issues = []
        cfg = self.config
        
        # Get throughput
        tpd = throughput_tpd if throughput_tpd is not None else cfg.throughput_tpd
        
        # Handle zero throughput
        if tpd == 0:
            self._outputs = self._zero_output()
            return self._outputs
        
        # ============================================================
        # MASS FLOW
        # ============================================================
        
        # Convert tpd to kg/s and kg/h
        # Excel B4: =B3*1000/86400
        mass_flow_kg_s = tpd * 1000 / 86400
        
        # Excel B5: =B3*1000/24
        H2_throughput_kg_h = tpd * 1000 / 24
        H2_throughput_kg_day = tpd * 1000
        
        # Losses
        # Excel B48: =B3*1000*(1+B19)
        # Wait, this formula adds losses? Should subtract. Let me check...
        # Actually looking at the context, B19 is loss_fraction = 0.02
        # The formula B3*1000*(1+B19) would INCREASE output, which is wrong
        # Correct formula should be: output = input * (1 - loss_fraction)
        boiloff_loss_kg_day = H2_throughput_kg_day * cfg.loss_fraction
        H2_output_kg_day = H2_throughput_kg_day * (1 - cfg.loss_fraction)
        
        # Flag the Excel issue
        excel_formula_output = tpd * 1000 * (1 + cfg.loss_fraction)
        if abs(excel_formula_output - H2_throughput_kg_day) > 1:
            self._issues.append(
                f"ISSUE: Excel B48 formula '=B3*1000*(1+B19)' gives {excel_formula_output:.0f} kg/day "
                f"which ADDS losses instead of subtracting. This appears to be a bug. "
                f"Correct output after {cfg.loss_fraction:.0%} loss: {H2_output_kg_day:.0f} kg/day"
            )
        
        # ============================================================
        # LH2 PUMP CALCULATIONS
        # ============================================================
        
        # Specific volume (inverse of density)
        # Excel B9: =1/B8
        specific_volume_m3_kg = 1 / cfg.LH2_density_kg_m3
        
        # Pressure rise
        # Excel B24: =(B7-B6)*100000  (bar to Pa)
        pressure_rise_Pa = (cfg.P_discharge - cfg.P_suction) * 100000
        pressure_rise_MPa = pressure_rise_Pa / 1e6
        
        # Specific work (for incompressible fluid: w = v * ΔP / η)
        # Excel B25: =B9*B24/B10
        specific_work_J_kg = specific_volume_m3_kg * pressure_rise_Pa / cfg.pump_efficiency
        
        # Pump power
        # Excel B26: =B4*B25/1000  (W to kW)
        pump_power_kW = mass_flow_kg_s * specific_work_J_kg / 1000
        
        # VALIDATION: Check pump power reasonableness
        # For LH2 at ~70 kg/m³, pumping from 3 to 700 bar
        # Theoretical minimum: v*ΔP = 0.0143 * 697e5 = 996 kJ/kg
        # With 65% efficiency: ~1532 kJ/kg = 0.426 kWh/kg
        # For 20.83 kg/h (0.5 tpd): ~8.9 kW - this matches!
        theoretical_min_work = specific_volume_m3_kg * pressure_rise_Pa
        if specific_work_J_kg < theoretical_min_work:
            self._issues.append(
                f"Specific work {specific_work_J_kg/1000:.2f} kJ/kg is less than "
                f"theoretical minimum {theoretical_min_work/1000:.2f} kJ/kg. Check efficiency."
            )
        
        # ============================================================
        # VAPORIZER CALCULATIONS
        # ============================================================
        
        # Sensible heat from 20K to 300K
        # Excel B15: =B12*(B14-B13)/1000  (kJ to MJ)
        sensible_heat_MJ_kg = cfg.gas_Cp_avg_kJ_per_kgK * (cfg.T_out_delivery - cfg.T_in_after_pump) / 1000
        
        # Total enthalpy change (latent + sensible)
        # Excel B16: =B15+B11
        delta_h_total_MJ_kg = sensible_heat_MJ_kg + cfg.latent_heat_MJ_per_kg
        
        # VALIDATION: Check enthalpy values
        # Latent heat of H2 at 20K is ~0.45 MJ/kg (correct)
        # Sensible heat: Cp_avg ~ 14 kJ/kg-K, ΔT = 280K → 3.92 MJ/kg
        # Total ~4.37 MJ/kg which matches Excel B16
        if delta_h_total_MJ_kg < 4.0 or delta_h_total_MJ_kg > 5.0:
            self._issues.append(
                f"Total enthalpy change {delta_h_total_MJ_kg:.2f} MJ/kg outside "
                f"expected range (4-5 MJ/kg). Check thermodynamic properties."
            )
        
        # Vaporizer heat duty
        # Excel B30: =IF(B3=0,0,B16*B4*1000)  (MJ/s to kW)
        vaporizer_duty_kW = delta_h_total_MJ_kg * mass_flow_kg_s * 1000
        
        # ============================================================
        # HEAT SUPPLY
        # ============================================================
        
        # Heat from TriGen
        heat_from_trigen_kW = Q_from_trigen_kW
        
        # Shortfall
        # Excel B31: =MAX(0,B30-B17)
        heat_shortfall_kW = max(0, vaporizer_duty_kW - heat_from_trigen_kW)
        
        # Auxiliary NG heater (if shortfall exists)
        # Excel C33: =IF(B31>0,B31/C32,0) then C35: =MAX(C33*3412/C34, 0)
        if heat_shortfall_kW > 0:
            Q_fuel_needed = heat_shortfall_kW / cfg.additional_heater_efficiency
            NG_aux_heater_scf_h = Q_fuel_needed * 3412 / cfg.LHV_NG_BTU_per_scf
        else:
            NG_aux_heater_scf_h = 0
        
        # ============================================================
        # ELECTRICITY
        # ============================================================
        
        # Total power: pump + precooler + BOP
        # Excel B42: =SUM(B39:B41)
        total_power_kW = pump_power_kW + cfg.precooler_electric_kW + cfg.BOP_aux_loads_kW
        
        # Daily electricity
        # Excel B47: =IF(B3>0,B42*24,0)
        electricity_kWh_day = total_power_kW * 24
        
        # ============================================================
        # BUILD OUTPUT
        # ============================================================
        
        self._outputs = LH2DeliveryOutputs(
            H2_throughput_kg_h=H2_throughput_kg_h,
            H2_throughput_kg_day=H2_throughput_kg_day,
            H2_output_kg_day=H2_output_kg_day,
            pump_power_kW=pump_power_kW,
            pressure_rise_MPa=pressure_rise_MPa,
            specific_work_J_kg=specific_work_J_kg,
            vaporizer_duty_kW=vaporizer_duty_kW,
            delta_h_total_MJ_kg=delta_h_total_MJ_kg,
            heat_from_trigen_kW=heat_from_trigen_kW,
            heat_shortfall_kW=heat_shortfall_kW,
            NG_aux_heater_scf_h=NG_aux_heater_scf_h,
            total_power_kW=total_power_kW,
            electricity_kWh_day=electricity_kWh_day,
            boiloff_loss_kg_day=boiloff_loss_kg_day,
        )
        
        return self._outputs
    
    def _zero_output(self) -> LH2DeliveryOutputs:
        """Return zero outputs when throughput is zero."""
        return LH2DeliveryOutputs(
            H2_throughput_kg_h=0, H2_throughput_kg_day=0, H2_output_kg_day=0,
            pump_power_kW=0, pressure_rise_MPa=0, specific_work_J_kg=0,
            vaporizer_duty_kW=0, delta_h_total_MJ_kg=0,
            heat_from_trigen_kW=0, heat_shortfall_kW=0, NG_aux_heater_scf_h=0,
            total_power_kW=0, electricity_kWh_day=0,
            boiloff_loss_kg_day=0,
        )
    
    def summary(self) -> str:
        """Return formatted summary."""
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"  ⚠ {issue}" for issue in self._issues) if self._issues else "  None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                 LH2 DELIVERY SYSTEM SUMMARY                      ║
╠══════════════════════════════════════════════════════════════════╣
║  THROUGHPUT                                                      ║
║    LH2 Input:                {o.H2_throughput_kg_day:>12,.2f} kg/day            ║
║    H2 Output (after loss):   {o.H2_output_kg_day:>12,.2f} kg/day            ║
║    Boil-off Loss:            {o.boiloff_loss_kg_day:>12,.2f} kg/day            ║
║                                                                  ║
║  LH2 PUMP                                                        ║
║    Pressure Rise:            {o.pressure_rise_MPa:>12,.1f} MPa              ║
║    Specific Work:            {o.specific_work_J_kg/1000:>12,.2f} kJ/kg            ║
║    Pump Power:               {o.pump_power_kW:>12,.2f} kW               ║
║                                                                  ║
║  VAPORIZER                                                       ║
║    Enthalpy Change:          {o.delta_h_total_MJ_kg:>12,.2f} MJ/kg            ║
║    Heat Duty:                {o.vaporizer_duty_kW:>12,.2f} kW_th            ║
║    Heat from TriGen:         {o.heat_from_trigen_kW:>12,.2f} kW_th            ║
║    Heat Shortfall:           {o.heat_shortfall_kW:>12,.2f} kW_th            ║
║    Aux NG Heater:            {o.NG_aux_heater_scf_h:>12,.2f} scf/h             ║
║                                                                  ║
║  ELECTRICITY                                                     ║
║    Total Power:              {o.total_power_kW:>12,.2f} kW               ║
║    Daily Consumption:        {o.electricity_kWh_day:>12,.2f} kWh/day          ║
╠══════════════════════════════════════════════════════════════════╣
║  ISSUES IDENTIFIED                                               ║
{issues_str}
╚══════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 70)
    print("LH2 DELIVERY MODULE VALIDATION")
    print("=" * 70)
    
    # Test with 0.5 tpd and TriGen heat available
    lh2 = LH2Delivery()
    outputs = lh2.calculate(throughput_tpd=0.5, Q_from_trigen_kW=414.23)
    
    print(lh2.summary())
    
    # Compare with Excel
    print("\nComparison with Excel (LH2_Pump_Vaporizer sheet):")
    print("-" * 60)
    print(f"Pump power: {outputs.pump_power_kW:.4f} kW (Excel B26: 8.865)")
    print(f"Vaporizer duty: {outputs.vaporizer_duty_kW:.4f} kW (Excel B30: 25.29)")
    print(f"Total power: {outputs.total_power_kW:.4f} kW (Excel B42: 33.86)")
    print(f"Daily electricity: {outputs.electricity_kWh_day:.4f} kWh (Excel B47: 812.76)")
