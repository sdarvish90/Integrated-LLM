"""
TriGen MCFC System Module (SureSource 3000)

This module replicates the calculations from the TriGen sheet in Calculator-Final_V1_4.xlsx.
The system takes natural gas input and produces:
- Electricity (AC)
- Hydrogen (co-product via PSA/shift)
- Useful heat (for SMR preheat, vaporizer, etc.)

Reference: FuelCell Energy SureSource 3000 specifications
"""

from dataclasses import dataclass
from typing import Optional
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import TriGenConfig


@dataclass
class TriGenOutputs:
    """
    Output results from TriGen calculations.
    All values are computed from the input parameters.
    """
    # Mass flows (kg/h)
    m_dot_NG_kg_h: float  # Natural gas mass flow
    m_dot_air_kg_h: float  # Air flow to cathode
    m_dot_steam_kg_h: float  # Steam feed to reformer
    
    # Energy flows
    fuel_energy_in_kW_LHV: float  # Total fuel energy input
    fuel_energy_to_stack_kW: float  # Energy to MCFC stack (after H2 co-prod)
    
    # Electrical output
    power_electric_kW: float  # Net AC electrical output
    power_electric_gross_kW: float  # Gross electrical (before aux)
    aux_load_kW: float  # Auxiliary consumption
    
    # Hydrogen output
    H2_energy_kW: float  # H2 energy content (LHV basis)
    H2_output_kg_h: float  # H2 mass flow rate
    H2_output_kg_day: float  # Daily H2 production
    
    # Thermal output
    useful_heat_kW_th: float  # Recoverable heat
    unrecovered_loss_kW: float  # Heat losses
    
    # Reformer flows
    CH4_flow_kmol_h: float  # CH4 molar flow
    steam_flow_kmol_h: float  # Steam molar flow
    air_flow_kmol_h: float  # Air molar flow
    
    # Preheat duties (kW_th)
    Q_preheat_NG_kW: float  # NG preheat duty
    Q_preheat_air_kW: float  # Air preheat duty
    Q_preheat_steam_kW: float  # Steam preheat duty
    Q_preheat_total_kW: float  # Total preheat duty
    
    # Heat available for export
    Q_available_for_export_kW: float  # Heat that can go to SMR/vaporizer
    Q_available_for_export_kW_day: float  # Daily heat available
    
    # Daily totals
    NG_input_scf_day: float  # Daily NG consumption
    electricity_kWh_day: float  # Daily electricity production


class TriGen:
    """
    TriGen MCFC (Molten Carbonate Fuel Cell) System.
    
    Based on FuelCell Energy SureSource 3000 specifications.
    Produces electricity, hydrogen, and useful heat from natural gas.
    
    Key features:
    - Internal reforming of natural gas
    - PSA-based hydrogen co-production
    - High-temperature exhaust heat recovery
    """
    
    def __init__(self, config: Optional[TriGenConfig] = None):
        """
        Initialize TriGen system with configuration.
        
        Args:
            config: TriGenConfig object with system parameters.
                   If None, uses default configuration.
        """
        self.config = config or TriGenConfig()
        self._outputs: Optional[TriGenOutputs] = None
        self._issues: list = []  # Track any physical inconsistencies
    
    @property
    def outputs(self) -> TriGenOutputs:
        """Get calculated outputs. Raises error if not yet calculated."""
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    def calculate(self, NG_input_scf_h: Optional[float] = None) -> TriGenOutputs:
        """
        Run mass and energy balance calculations.
        
        Args:
            NG_input_scf_h: Natural gas input in scf/h. 
                           If None, uses config value.
                           Max allowed: 21,840 scf/h
        
        Returns:
            TriGenOutputs dataclass with all calculated values.
        """
        cfg = self.config
        
        # Use provided NG input or config default
        NG_input = NG_input_scf_h if NG_input_scf_h is not None else cfg.NG_input_scf_per_h
        
        # Validate NG input
        if NG_input > 21840:
            raise ValueError(f"NG input {NG_input} scf/h exceeds maximum of 21,840 scf/h")
        
        # Handle zero input case
        if NG_input == 0:
            self._outputs = self._zero_output()
            return self._outputs
        
        # ============================================================
        # MASS FLOW CALCULATIONS (Excel rows 34-36)
        # ============================================================
        
        # NG mass flow: C34 = C4 * 0.0283168 * 0.717
        # Conversion: scf/h -> m3/h -> kg/h (using NG density ~0.717 kg/m3)
        m_dot_NG_kg_h = NG_input * 0.0283168 * 0.717
        
        # Air mass flow to cathode: C35 = C34 * 17.2 * C13 (cathode lambda)
        # The 17.2 factor is the stoichiometric air-to-fuel ratio for MCFC
        m_dot_air_kg_h = m_dot_NG_kg_h * 17.2 * cfg.cathode_excess_air_lambda
        
        # Steam mass flow: C36 = C12 * C16 / C17 * C34
        # C12 = steam_to_carbon_ratio, C16 = MW_H2O, C17 = MW_CH4
        # This calculates steam needed for internal reforming
        m_dot_steam_kg_h = (cfg.steam_to_carbon_ratio * cfg.MW_H2O / cfg.MW_CH4) * m_dot_NG_kg_h
        
        # ============================================================
        # ENERGY BALANCE CALCULATIONS (Excel rows 43-50)
        # ============================================================
        
        # Fuel energy input (LHV basis): C43 = C4 * C8 / 1000000 * 1000 / 3.412
        # C4 = NG input scf/h, C8 = LHV BTU/scf
        # Converts BTU/h to kW: (scf/h * BTU/scf) / 3412 BTU/kWh
        fuel_energy_in_kW_LHV = NG_input * cfg.LHV_NG_BTU_per_scf / 3412
        
        # Fuel energy to stack (after H2 co-production): C44 = C43 * (1 - C11)
        # C11 = H2_coprod_share (fraction diverted to H2 production)
        fuel_energy_to_stack_kW = fuel_energy_in_kW_LHV * (1 - cfg.H2_coprod_share)
        
        # Minimum power based on turndown: C21 = IF(C4=0, 0, C19*C20)
        # C19 = plant_rated_kW, C20 = turndown fraction
        power_min_kW = cfg.plant_rated_kW * cfg.plant_turndown_fraction
        
        # Electrical power output: C45 = MAX(C21, C5 * C44)
        # Either minimum power or efficiency-based output
        power_electric_gross_kW = max(power_min_kW, cfg.electrical_eff_LHV * fuel_energy_to_stack_kW)
        
        # H2 energy content: C46 = C43 * C11
        H2_energy_kW = fuel_energy_in_kW_LHV * cfg.H2_coprod_share
        
        # H2 mass output: C47 = C46 / C10 (kW / kWh/kg = kg/h)
        H2_output_kg_h = H2_energy_kW / cfg.LHV_H2_kWh_per_kg
        
        # Daily H2 output: C48 = C47 * 24
        H2_output_kg_day = H2_output_kg_h * 24
        
        # Useful thermal output: C49 = (C6 - C5) * C44
        # NOTE: Excel formula says C43 but based on values it should be C44 (fuel to stack)
        # Thermal efficiency portion of overall efficiency
        # C6 = overall_eff, C5 = electrical_eff
        useful_heat_kW_th = (cfg.overall_eff_LHV - cfg.electrical_eff_LHV) * fuel_energy_in_kW_LHV
        
        # Unrecovered losses: C50 = (1 - C6) * C44
        unrecovered_loss_kW = (1 - cfg.overall_eff_LHV) * fuel_energy_to_stack_kW
        
        # ============================================================
        # AUXILIARY LOAD AND NET POWER (Excel rows 51-52)
        # ============================================================
        
        # Auxiliary load: C51 = C45 * C7
        aux_load_kW = power_electric_gross_kW * cfg.aux_load_fraction
        
        # Net electrical output: C52 = C45 - C51
        power_electric_kW = power_electric_gross_kW - aux_load_kW
        
        # ============================================================
        # MOLAR FLOWS FOR REFORMER (Excel rows 53-62)
        # ============================================================
        
        # CH4 molar flow: C53 = IF(C4=0, 0, C4 * H3/H4 * C9 * C44/C43)
        # H3 = NG_m3_per_scf = 0.028317, H4 = NG_m3_per_kmol_STP = 22.414
        # C9 = NG_CH4_mol_frac
        NG_m3_per_scf = 0.028317
        NG_m3_per_kmol_STP = 22.414
        CH4_flow_kmol_h = (NG_input * NG_m3_per_scf / NG_m3_per_kmol_STP * 
                          cfg.NG_CH4_mol_frac * fuel_energy_to_stack_kW / fuel_energy_in_kW_LHV)
        
        # Steam molar flow: C54 = C53 * C12 (S/C ratio)
        steam_flow_kmol_h = CH4_flow_kmol_h * cfg.steam_to_carbon_ratio
        
        # H2 to anode (50% goes to FC): C56 = C55 * 0.5
        # Note: H2_from_reforming_kmol_h = CH4_flow_kmol_h * 4 (calculated later for heat)
        H2_to_anode_kmol_h = (CH4_flow_kmol_h * 4) * 0.5
        
        # O2 required at cathode: C57 = C56 * C13 (with excess air)
        O2_required_kmol_h = H2_to_anode_kmol_h * cfg.cathode_excess_air_lambda
        
        # Air molar flow: C58 = C57 / C14 (O2 fraction in air)
        air_flow_kmol_h = O2_required_kmol_h / cfg.air_O2_mol_frac
        
        # Air mass flow check: C59 = C58 * C15 (MW_air)
        air_mass_flow_check = air_flow_kmol_h * cfg.MW_air
        
        # CH4 mass flow: C60 = C54 * C16
        CH4_mass_flow_kg_h = CH4_flow_kmol_h * cfg.MW_CH4
        
        # Steam mass flow: C61 = C55 * C16
        steam_mass_flow_kg_h = steam_flow_kmol_h * cfg.MW_H2O
        
        # ============================================================
        # PREHEAT DUTY CALCULATIONS (Excel rows 71-77)
        # ============================================================
        
        # Temperature difference for preheating
        delta_T_preheat = cfg.T_stack_in - cfg.T_amb  # °C
        
        # NG preheat duty: C71 = C34 * C26 * (C23-C22) / 3600 * C30
        # Q = m_dot * Cp * ΔT * ε (with conversion from kJ/h to kW)
        Q_preheat_NG_kW = (m_dot_NG_kg_h * cfg.Cp_NG * delta_T_preheat / 3600 * cfg.epsilon_NG)
        
        # Air preheat duty: C72 = C35 * C27 * (C23-C22) / 3600 * C31
        Q_preheat_air_kW = (m_dot_air_kg_h * cfg.Cp_air * delta_T_preheat / 3600 * cfg.epsilon_air)
        
        # Steam preheat duty: C73 = C36 * C28 * (C23-C22) / 3600 * C32
        Q_preheat_steam_kW = (m_dot_steam_kg_h * cfg.Cp_steam * delta_T_preheat / 3600 * cfg.epsilon_steam)
        
        # Total preheat duty: C74 = SUM(C71:C73)
        Q_preheat_total_kW = Q_preheat_NG_kW + Q_preheat_air_kW + Q_preheat_steam_kW
        
        # ============================================================
        # HEAT AVAILABLE FOR EXPORT (Excel rows 60-63, 75-77, 85)
        # ============================================================
        
        # Steam mass flow calculations for latent heat
        # C55 = C53 * 4 (H2 from reforming in kmol/h)
        H2_from_reforming_kmol_h = CH4_flow_kmol_h * 4
        
        # C60 = C54 * C16 (steam flow * MW_CH4 - but actually uses C54=steam_kmol * MW_H2O)
        # C61 = C55 * C16 (H2 flow * MW - but this seems to be water produced)
        # The difference C62 = C61 - C60 represents net water/steam mass difference
        steam_mass_C60 = steam_flow_kmol_h * cfg.MW_H2O  # Steam input
        water_produced_C61 = H2_from_reforming_kmol_h * cfg.MW_H2O  # Water from H2 oxidation at cathode
        net_steam_diff_kg_h = water_produced_C61 - steam_mass_C60  # C62
        
        # Latent heat term: C63 = (C62/3600) * (C38 + C29*(C37-C22) + C28*MAX(0, C23-C37))
        # C38 = latent heat, C29 = Cp_water, C37 = T_sat, C22 = T_amb, C28 = Cp_steam, C23 = T_stack
        T_sat = cfg.T_sat_1_5bar  # 111.4 °C
        latent_heat = cfg.latent_heat_1_5bar  # 2235 kJ/kg
        
        # Heat from condensation and cooling
        Q_latent_kW = (net_steam_diff_kg_h / 3600) * (
            latent_heat + 
            cfg.Cp_water * (T_sat - cfg.T_amb) + 
            cfg.Cp_steam * max(0, cfg.T_stack_in - T_sat)
        )
        
        # Heat balance: C75 = C49 + C63 - C74
        Q_net_before_efficiency = useful_heat_kW_th + Q_latent_kW - Q_preheat_total_kW
        
        # C76 = MAX(C49 + C63 - C74, 0)
        Q_available_gross = max(Q_net_before_efficiency, 0)
        
        # Apply exhaust HX efficiency: C77 = MAX(C33 * C76, 0)
        Q_available_for_export_kW = max(cfg.epsilon_exhaust * Q_available_gross, 0)
        
        # Daily heat available: C85 = C77 * 24
        Q_available_for_export_kW_day = Q_available_for_export_kW * 24
        
        # ============================================================
        # DAILY TOTALS (Excel rows 82-84)
        # ============================================================
        
        # Daily NG consumption: C82 = C4 * 24
        NG_input_scf_day = NG_input * 24
        
        # Daily electricity: C84 = IF(C4=0, 0, C52 * 24 * C39)
        # C39 = AC/DC conversion rate
        electricity_kWh_day = power_electric_kW * 24 * cfg.AC_DC_conversion_rate
        
        # ============================================================
        # BUILD OUTPUT DATACLASS
        # ============================================================
        
        self._outputs = TriGenOutputs(
            # Mass flows
            m_dot_NG_kg_h=m_dot_NG_kg_h,
            m_dot_air_kg_h=m_dot_air_kg_h,
            m_dot_steam_kg_h=m_dot_steam_kg_h,
            
            # Energy flows
            fuel_energy_in_kW_LHV=fuel_energy_in_kW_LHV,
            fuel_energy_to_stack_kW=fuel_energy_to_stack_kW,
            
            # Electrical
            power_electric_kW=power_electric_kW,
            power_electric_gross_kW=power_electric_gross_kW,
            aux_load_kW=aux_load_kW,
            
            # Hydrogen
            H2_energy_kW=H2_energy_kW,
            H2_output_kg_h=H2_output_kg_h,
            H2_output_kg_day=H2_output_kg_day,
            
            # Thermal
            useful_heat_kW_th=useful_heat_kW_th,
            unrecovered_loss_kW=unrecovered_loss_kW,
            
            # Molar flows
            CH4_flow_kmol_h=CH4_flow_kmol_h,
            steam_flow_kmol_h=steam_flow_kmol_h,
            air_flow_kmol_h=air_flow_kmol_h,
            
            # Preheat duties
            Q_preheat_NG_kW=Q_preheat_NG_kW,
            Q_preheat_air_kW=Q_preheat_air_kW,
            Q_preheat_steam_kW=Q_preheat_steam_kW,
            Q_preheat_total_kW=Q_preheat_total_kW,
            
            # Heat export
            Q_available_for_export_kW=Q_available_for_export_kW,
            Q_available_for_export_kW_day=Q_available_for_export_kW_day,
            
            # Daily totals
            NG_input_scf_day=NG_input_scf_day,
            electricity_kWh_day=electricity_kWh_day,
        )
        
        return self._outputs
    
    def _zero_output(self) -> TriGenOutputs:
        """Return zero outputs when NG input is zero."""
        return TriGenOutputs(
            m_dot_NG_kg_h=0,
            m_dot_air_kg_h=0,
            m_dot_steam_kg_h=0,
            fuel_energy_in_kW_LHV=0,
            fuel_energy_to_stack_kW=0,
            power_electric_kW=0,
            power_electric_gross_kW=0,
            aux_load_kW=0,
            H2_energy_kW=0,
            H2_output_kg_h=0,
            H2_output_kg_day=0,
            useful_heat_kW_th=0,
            unrecovered_loss_kW=0,
            CH4_flow_kmol_h=0,
            steam_flow_kmol_h=0,
            air_flow_kmol_h=0,
            Q_preheat_NG_kW=0,
            Q_preheat_air_kW=0,
            Q_preheat_steam_kW=0,
            Q_preheat_total_kW=0,
            Q_available_for_export_kW=0,
            Q_available_for_export_kW_day=0,
            NG_input_scf_day=0,
            electricity_kWh_day=0,
        )
    
    def summary(self) -> str:
        """
        Return a formatted summary of TriGen outputs.
        
        Returns:
            String with key outputs formatted for display.
        """
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                    TRIGEN SYSTEM SUMMARY                         ║
║                    ({self.config.model})                         ║
╠══════════════════════════════════════════════════════════════════╣
║  INPUTS                                                          ║
║    Natural Gas Input:        {o.NG_input_scf_day:>12,.0f} scf/day           ║
║    NG Mass Flow:             {o.m_dot_NG_kg_h:>12,.2f} kg/h              ║
║                                                                  ║
║  OUTPUTS                                                         ║
║    ─── Electricity ───                                           ║
║    Net Power:                {o.power_electric_kW:>12,.2f} kW               ║
║    Daily Electricity:        {o.electricity_kWh_day:>12,.2f} kWh/day          ║
║                                                                  ║
║    ─── Hydrogen ───                                              ║
║    H2 Production:            {o.H2_output_kg_h:>12,.2f} kg/h              ║
║    Daily H2:                 {o.H2_output_kg_day:>12,.2f} kg/day            ║
║                                                                  ║
║    ─── Thermal ───                                               ║
║    Useful Heat:              {o.useful_heat_kW_th:>12,.2f} kW_th            ║
║    Heat for Export:          {o.Q_available_for_export_kW:>12,.2f} kW_th            ║
║    Daily Heat Export:        {o.Q_available_for_export_kW_day:>12,.2f} kWh_th/day       ║
║                                                                  ║
║  ENERGY BALANCE                                                  ║
║    Fuel Energy In (LHV):     {o.fuel_energy_in_kW_LHV:>12,.2f} kW               ║
║    To Stack:                 {o.fuel_energy_to_stack_kW:>12,.2f} kW               ║
║    To H2 Co-prod:            {o.H2_energy_kW:>12,.2f} kW               ║
║    Losses:                   {o.unrecovered_loss_kW:>12,.2f} kW               ║
╚══════════════════════════════════════════════════════════════════╝
"""


def validate_against_excel(trigen: TriGen) -> dict:
    """
    Compare Python outputs against Excel reference values.
    
    Uses the default configuration (NG input = 10,700 scf/h) 
    to validate against Excel Calculator-Final_V1_4.xlsx.
    
    Returns:
        Dictionary with comparison results and % differences.
    """
    # Excel reference values (from TriGen sheet with NG_input = 10700)
    excel_ref = {
        'NG_input_scf_day': 256800,  # C82
        'H2_output_kg_day': 522.196916,  # C48
        'electricity_kWh_day': 30636.48,  # C84
        'useful_heat_kW_th': 975.606682,  # C49 (approximate)
        'Q_available_for_export_kW_day': 9941.616336,  # C85
    }
    
    # Run calculation
    outputs = trigen.calculate()
    
    # Compare
    results = {}
    for key, excel_val in excel_ref.items():
        python_val = getattr(outputs, key)
        diff_pct = ((python_val - excel_val) / excel_val * 100) if excel_val != 0 else 0
        results[key] = {
            'excel': excel_val,
            'python': python_val,
            'diff_pct': diff_pct,
            'match': abs(diff_pct) < 1.0  # Within 1%
        }
    
    return results


# ============================================================
# MAIN - Run validation when executed directly
# ============================================================

if __name__ == "__main__":
    print("=" * 70)
    print("TRIGEN MODULE VALIDATION")
    print("=" * 70)
    
    # Create TriGen with default config
    trigen = TriGen()
    
    # Calculate with default NG input (10,700 scf/h)
    outputs = trigen.calculate()
    
    # Print summary
    print(trigen.summary())
    
    # Validate against Excel
    print("\n" + "=" * 70)
    print("VALIDATION AGAINST EXCEL")
    print("=" * 70)
    
    results = validate_against_excel(trigen)
    
    print(f"\n{'Parameter':<35} {'Excel':>15} {'Python':>15} {'Diff %':>10} {'Match':>8}")
    print("-" * 85)
    
    for key, vals in results.items():
        match_str = "✓" if vals['match'] else "✗"
        print(f"{key:<35} {vals['excel']:>15,.2f} {vals['python']:>15,.2f} {vals['diff_pct']:>9.2f}% {match_str:>8}")
    
    all_match = all(v['match'] for v in results.values())
    print("-" * 85)
    print(f"Overall validation: {'PASSED ✓' if all_match else 'NEEDS REVIEW ✗'}")
