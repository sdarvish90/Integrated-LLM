"""
Steam Methane Reformer (SMR) Module

This module replicates the SMR calculations from the Electrolyzer_SMR sheet.
SMR produces hydrogen from natural gas via steam reforming.

CRITICAL REVIEW NOTES:
1. The Excel model uses a simplified stoichiometry (3 kg CH4 per kg H2) which is 
   reasonable for industrial SMR with ~75% efficiency.
2. Heat integration with TriGen is handled - excess TriGen heat reduces NG consumption.
3. CO2 emissions are tracked (important for carbon intensity calculations).
"""

from dataclasses import dataclass
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SMRConfig


@dataclass
class SMROutputs:
    """Output results from SMR calculations."""
    
    # Production rates
    H2_output_kg_h: float  # Hydrogen production rate
    H2_output_kg_day: float  # Daily hydrogen production
    
    # Feedstock consumption
    NG_input_kg_h: float  # Natural gas mass flow
    NG_input_scf_h: float  # Natural gas volumetric flow
    NG_input_scf_day: float  # Daily NG consumption
    
    # Steam requirements
    steam_input_kg_h: float  # Steam mass flow to reformer
    
    # Heat duties (kW_th)
    Q_reforming_duty_kW: float  # Endothermic reforming reaction heat
    Q_CH4_preheat_kW: float  # CH4 preheating duty
    Q_steam_generation_kW: float  # Steam generation duty
    Q_steam_superheat_kW: float  # Steam superheating duty
    Q_total_feed_preheat_kW: float  # Total preheat duty
    
    # Heat recovery
    Q_PSA_offgas_recovery_kW: float  # Heat from PSA off-gas combustion
    Q_internal_recovery_kW: float  # Internal heat recovery
    Q_from_trigen_kW: float  # Heat imported from TriGen
    Q_external_required_kW: float  # Additional heat needed (if any)
    Q_surplus_kW: float  # Excess heat available for export
    
    # Auxiliary NG for heating (if TriGen heat insufficient)
    NG_aux_heater_scf_h: float  # Additional NG for process heat
    
    # Emissions
    CO2_output_kg_h: float  # CO2 from reforming
    CO2_intensity_kg_per_kgH2: float  # Carbon intensity
    
    # Electricity (minimal for SMR)
    electricity_kWh_day: float  # Mainly for controls, pumps


class SMR:
    """
    Steam Methane Reformer System.
    
    Produces hydrogen via catalytic steam reforming of natural gas:
    CH4 + H2O → CO + 3H2 (reforming)
    CO + H2O → CO2 + H2 (water-gas shift)
    Net: CH4 + 2H2O → CO2 + 4H2
    
    The Excel model simplifies this with an empirical ratio of 3 kg CH4 per kg H2,
    which accounts for ~75% conversion efficiency and heat losses.
    """
    
    def __init__(self, config: Optional[SMRConfig] = None):
        self.config = config or SMRConfig()
        self._outputs: Optional[SMROutputs] = None
        self._issues: list = []  # Track any physical inconsistencies
    
    @property
    def outputs(self) -> SMROutputs:
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> list:
        """Return list of any physical/engineering issues identified."""
        return self._issues
    
    def calculate(self, 
                  H2_rate_tpd: Optional[float] = None,
                  Q_from_trigen_kW: float = 0) -> SMROutputs:
        """
        Run SMR mass and energy balance.
        
        Args:
            H2_rate_tpd: Hydrogen production rate in ton/day. If None, uses config.
            Q_from_trigen_kW: Heat available from TriGen system (kW_th)
        
        Returns:
            SMROutputs with all calculated values.
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
        # MASS BALANCE
        # ============================================================
        
        # H2 output: Convert tpd to kg/h
        # Excel C8: =C7*1000/24
        H2_output_kg_h = H2_tpd * 1000 / 24
        H2_output_kg_day = H2_tpd * 1000
        
        # NG input based on stoichiometry
        # Excel C11: kg_CH4_per_kg_H2 = 3 (empirical, includes efficiency)
        # Excel C12: =C9*C13 where C9 is NG mass flow
        # But simpler: NG_mass = H2_mass * kg_CH4_per_kg_H2
        NG_input_kg_h = H2_output_kg_h * cfg.kg_CH4_per_kg_H2
        
        # Convert to scf/h
        # Excel C9: =C8*C11/C13 but this seems convoluted
        # Direct: kg/h / (kg/scf) = scf/h
        NG_input_scf_h = NG_input_kg_h / cfg.rho_CH4_kg_per_scf
        NG_input_scf_day = NG_input_scf_h * 24
        
        # VALIDATION CHECK: Compare with Excel formula
        # Excel C9 = C8 * C11 / C13 = H2_kg/h * 3 / 0.02007
        NG_check = H2_output_kg_h * cfg.kg_CH4_per_kg_H2 / cfg.rho_CH4_kg_per_scf
        if abs(NG_input_scf_h - NG_check) > 0.1:
            self._issues.append(f"NG input calculation mismatch: {NG_input_scf_h} vs {NG_check}")
        
        # Steam requirement
        # Excel C16: =(C15*C12/C27)*C30
        # C15 = steam_to_carbon = 3, C12 = NG_kg/h, C27 = MW_H2, C30 = MW_H2O
        # This formula seems wrong dimensionally. Let me recalculate properly:
        # Steam/Carbon ratio is mol/mol, so:
        # n_CH4 = NG_kg/h / MW_CH4
        # n_steam = S/C * n_CH4
        # m_steam = n_steam * MW_H2O
        n_CH4_kmol_h = NG_input_kg_h / cfg.MW_CH4
        n_steam_kmol_h = cfg.steam_to_carbon_SMR * n_CH4_kmol_h
        steam_input_kg_h = n_steam_kmol_h * cfg.MW_H2O
        
        # Check against Excel formula interpretation
        # Excel seems to use: (S/C * NG_kg/h / MW_H2) * MW_H2O
        # This is WRONG physically - should divide by MW_CH4, not MW_H2
        excel_steam = (cfg.steam_to_carbon_SMR * NG_input_kg_h / cfg.MW_H2) * cfg.MW_H2O
        if abs(steam_input_kg_h - excel_steam) > 1:
            self._issues.append(
                f"ISSUE: Excel steam calculation may have error. "
                f"Physically correct: {steam_input_kg_h:.2f} kg/h, "
                f"Excel formula gives: {excel_steam:.2f} kg/h. "
                f"Using physically correct value."
            )
        
        # ============================================================
        # HEAT BALANCE
        # ============================================================
        
        # Reforming duty (endothermic reaction heat)
        # Excel C36: =C8*C23 (H2_kg/h * kWh_th/kgH2 for reforming)
        Q_reforming_duty_kW = H2_output_kg_h * cfg.reforming_kWh_per_kg_H2
        
        # CH4 preheat duty
        # Excel C37: =C12*C20*(C18-C17)/3600
        # Q = m_dot * Cp * ΔT (kJ/h -> kW)
        delta_T_preheat = cfg.T_preheat_feed - cfg.T_amb
        Q_CH4_preheat_kW = NG_input_kg_h * cfg.Cp_CH4 * delta_T_preheat / 3600
        
        # Steam generation duty
        # Excel C38: =C16*C22 (steam_kg/h * kWh_th/kg_steam)
        Q_steam_generation_kW = steam_input_kg_h * cfg.kWh_th_per_kg_steam_generation
        
        # Steam superheat duty
        # Excel C39: =C16*C21*(C18-C19)/3600
        delta_T_superheat = cfg.T_preheat_feed - cfg.T_sat_steam
        Q_steam_superheat_kW = steam_input_kg_h * cfg.Cp_steam * delta_T_superheat / 3600
        
        # Total feed/steam preheat duty
        # Excel C40: =C37+C38+C39
        Q_total_feed_preheat_kW = Q_CH4_preheat_kW + Q_steam_generation_kW + Q_steam_superheat_kW
        
        # PSA off-gas heat recovery
        # Excel C41: =C9*C10*C31/3412
        # Off-gas is burned to provide process heat
        # C10 = PSA_off_gas_fraction = 0.25
        # This recovers energy from the PSA purge stream
        Q_PSA_offgas_recovery_kW = (NG_input_scf_h * cfg.PSA_off_gas_fraction * 
                                    cfg.LHV_NG_BTU_per_scf / 3412)
        
        # Internal recoverable heat
        # Excel C42: =(C36+C40)*C24
        # C24 = recoverable_heat_fraction = 0.25
        Q_internal_recovery_kW = (Q_reforming_duty_kW + Q_total_feed_preheat_kW) * cfg.recoverable_heat_fraction
        
        # Heat from TriGen (external input)
        Q_from_trigen = Q_from_trigen_kW
        
        # Total heat available
        Q_total_available = Q_PSA_offgas_recovery_kW + Q_internal_recovery_kW + Q_from_trigen
        
        # Total heat required
        Q_total_required = Q_reforming_duty_kW + Q_total_feed_preheat_kW
        
        # External heat required (if TriGen not enough)
        # Excel C44: =IF(C43>C40-C42, 0, C40-C42)
        # This checks if TriGen heat covers the remaining need after internal recovery
        Q_remaining_need = Q_total_required - Q_PSA_offgas_recovery_kW - Q_internal_recovery_kW
        Q_external_required_kW = max(0, Q_remaining_need - Q_from_trigen)
        
        # Surplus heat (if more than needed)
        # Excel C46: =MAX(0, C43+C42-C40)
        Q_surplus_kW = max(0, Q_from_trigen + Q_internal_recovery_kW + 
                          Q_PSA_offgas_recovery_kW - Q_total_required)
        
        # Additional NG for auxiliary heater (if needed)
        # Excel C48, C49
        if Q_external_required_kW > 0:
            Q_fuel_needed = Q_external_required_kW / cfg.additional_heater_efficiency
            NG_aux_heater_scf_h = Q_fuel_needed * 3412 / cfg.LHV_NG_BTU_per_scf
        else:
            NG_aux_heater_scf_h = 0
        
        # ============================================================
        # CO2 EMISSIONS
        # ============================================================
        
        # CO2 from reforming reaction
        # CH4 + 2H2O → CO2 + 4H2
        # 1 mol CH4 → 1 mol CO2
        # Excel C25: =C29/C27*C12 where C29=MW_CO2, C27=MW_H2
        # This formula is also dimensionally suspect
        # Correct: n_CO2 = n_CH4, m_CO2 = n_CH4 * MW_CO2
        n_CO2_kmol_h = n_CH4_kmol_h  # 1:1 stoichiometry
        CO2_output_kg_h = n_CO2_kmol_h * cfg.MW_CO2
        
        # Carbon intensity
        # Excel C26: =C25/C8
        CO2_intensity = CO2_output_kg_h / H2_output_kg_h
        
        # VALIDATION: Theoretical minimum is ~5.5 kg CO2/kg H2
        # With efficiency losses, 8-10 is typical
        if CO2_intensity < 5 or CO2_intensity > 15:
            self._issues.append(
                f"CO2 intensity {CO2_intensity:.2f} kg/kgH2 outside typical range (5-15). "
                f"Check stoichiometry assumptions."
            )
        
        # ============================================================
        # ELECTRICITY (minor for SMR)
        # ============================================================
        
        # SMR has minimal electricity needs (controls, pumps)
        # Not explicitly calculated in Excel SMR section
        # Estimate ~5 kWh/day per kg/h of H2 capacity
        electricity_kWh_day = H2_output_kg_h * 5
        
        # ============================================================
        # BUILD OUTPUT
        # ============================================================
        
        self._outputs = SMROutputs(
            H2_output_kg_h=H2_output_kg_h,
            H2_output_kg_day=H2_output_kg_day,
            NG_input_kg_h=NG_input_kg_h,
            NG_input_scf_h=NG_input_scf_h,
            NG_input_scf_day=NG_input_scf_day,
            steam_input_kg_h=steam_input_kg_h,
            Q_reforming_duty_kW=Q_reforming_duty_kW,
            Q_CH4_preheat_kW=Q_CH4_preheat_kW,
            Q_steam_generation_kW=Q_steam_generation_kW,
            Q_steam_superheat_kW=Q_steam_superheat_kW,
            Q_total_feed_preheat_kW=Q_total_feed_preheat_kW,
            Q_PSA_offgas_recovery_kW=Q_PSA_offgas_recovery_kW,
            Q_internal_recovery_kW=Q_internal_recovery_kW,
            Q_from_trigen_kW=Q_from_trigen,
            Q_external_required_kW=Q_external_required_kW,
            Q_surplus_kW=Q_surplus_kW,
            NG_aux_heater_scf_h=NG_aux_heater_scf_h,
            CO2_output_kg_h=CO2_output_kg_h,
            CO2_intensity_kg_per_kgH2=CO2_intensity,
            electricity_kWh_day=electricity_kWh_day,
        )
        
        return self._outputs
    
    def _zero_output(self) -> SMROutputs:
        """Return zero outputs when H2 production is zero."""
        return SMROutputs(
            H2_output_kg_h=0, H2_output_kg_day=0,
            NG_input_kg_h=0, NG_input_scf_h=0, NG_input_scf_day=0,
            steam_input_kg_h=0,
            Q_reforming_duty_kW=0, Q_CH4_preheat_kW=0,
            Q_steam_generation_kW=0, Q_steam_superheat_kW=0,
            Q_total_feed_preheat_kW=0, Q_PSA_offgas_recovery_kW=0,
            Q_internal_recovery_kW=0, Q_from_trigen_kW=0,
            Q_external_required_kW=0, Q_surplus_kW=0,
            NG_aux_heater_scf_h=0,
            CO2_output_kg_h=0, CO2_intensity_kg_per_kgH2=0,
            electricity_kWh_day=0,
        )
    
    def summary(self) -> str:
        """Return formatted summary of SMR outputs."""
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"  ⚠ {issue}" for issue in self._issues) if self._issues else "  None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                      SMR SYSTEM SUMMARY                          ║
╠══════════════════════════════════════════════════════════════════╣
║  MASS BALANCE                                                    ║
║    H2 Production:            {o.H2_output_kg_h:>12,.2f} kg/h              ║
║    H2 Daily:                 {o.H2_output_kg_day:>12,.2f} kg/day            ║
║    NG Consumption:           {o.NG_input_scf_h:>12,.2f} scf/h             ║
║    NG Daily:                 {o.NG_input_scf_day:>12,.0f} scf/day           ║
║    Steam Input:              {o.steam_input_kg_h:>12,.2f} kg/h              ║
║                                                                  ║
║  HEAT BALANCE                                                    ║
║    Reforming Duty:           {o.Q_reforming_duty_kW:>12,.2f} kW_th            ║
║    Feed Preheat Duty:        {o.Q_total_feed_preheat_kW:>12,.2f} kW_th            ║
║    PSA Off-gas Recovery:     {o.Q_PSA_offgas_recovery_kW:>12,.2f} kW_th            ║
║    Internal Recovery:        {o.Q_internal_recovery_kW:>12,.2f} kW_th            ║
║    Heat from TriGen:         {o.Q_from_trigen_kW:>12,.2f} kW_th            ║
║    External Heat Needed:     {o.Q_external_required_kW:>12,.2f} kW_th            ║
║    Surplus Heat:             {o.Q_surplus_kW:>12,.2f} kW_th            ║
║                                                                  ║
║  EMISSIONS                                                       ║
║    CO2 Output:               {o.CO2_output_kg_h:>12,.2f} kg/h              ║
║    Carbon Intensity:         {o.CO2_intensity_kg_per_kgH2:>12,.2f} kgCO2/kgH2       ║
╠══════════════════════════════════════════════════════════════════╣
║  ISSUES IDENTIFIED                                               ║
{issues_str}
╚══════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 70)
    print("SMR MODULE VALIDATION")
    print("=" * 70)
    
    # Test with default config (0.5 tpd)
    smr = SMR()
    outputs = smr.calculate(H2_rate_tpd=0.5, Q_from_trigen_kW=414.23)
    
    print(smr.summary())
    
    # Compare key values with Excel
    print("\nComparison with Excel (Electrolyzer_SMR sheet, SMR mode):")
    print("-" * 60)
    print(f"H2 output: {outputs.H2_output_kg_h:.4f} kg/h (Excel C8: 20.8333)")
    print(f"NG input: {outputs.NG_input_scf_h:.4f} scf/h (Excel C9: 3114.04)")
    print(f"CO2 output: {outputs.CO2_output_kg_h:.4f} kg/h (Excel C25: 171.49)")
    print(f"CO2 intensity: {outputs.CO2_intensity_kg_per_kgH2:.4f} (Excel C26: 8.23)")
