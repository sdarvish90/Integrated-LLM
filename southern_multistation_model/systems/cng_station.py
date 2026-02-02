"""
CNG Station Module

This module handles Compressed Natural Gas dispensing:
NG Supply → LP Buffer → Compressor → HP Storage → Dispenser

CRITICAL REVIEW NOTES:
1. The station is largely independent from H2 systems - only shares:
   - NG feedstock accounting
   - Electricity load on common bus
2. Compressor energy of 0.6 kWh/GGE is reasonable for 60 psig inlet to 3600 psi.
3. PSA dryer has 2% purge loss which adds to NG consumption.
4. Peak sizing calculations (scfm) are for equipment specification, not energy.
"""

from dataclasses import dataclass
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CNGStationConfig


@dataclass
class CNGStationOutputs:
    """Output results from CNG Station calculations."""
    
    # Demand
    trucks_per_day: int
    fuel_per_truck: float
    fuel_unit: str
    daily_demand_GGE: float
    daily_demand_scf: float
    
    # NG consumption (including losses)
    total_NG_consumption_scf_day: float
    dryer_purge_loss_scf_day: float
    blowdown_loss_scf_day: float
    fugitive_loss_scf_day: float
    
    # Compressor
    compressor_capacity_scfm: float  # Recommended size
    compressor_energy_kWh_day: float
    
    # Dispenser/Auxiliary
    dispenser_energy_kWh_day: float
    aftercooler_aux_energy_kWh_day: float
    
    # Total electricity
    total_electricity_kWh_day: float
    
    # Peak analysis
    peak_flow_scfh: float
    peak_window_demand_scf: float


class CNGStation:
    """
    Compressed Natural Gas Fueling Station.
    
    Provides CNG for heavy-duty trucks and buses.
    
    Key components:
    - Low-pressure buffer tank (at utility pressure ~60 psig)
    - Multi-stage compressor (to ~3600 psi)
    - High-pressure storage cascade
    - Fast-fill dispensers
    - PSA dryer (removes moisture)
    
    Operating notes:
    - Peak demand typically during shift changes (4-hour window)
    - PSA dryer has 2% purge loss (vented NG)
    - Blowdown and fugitive losses add ~0.7% total
    """
    
    def __init__(self, config: Optional[CNGStationConfig] = None):
        self.config = config or CNGStationConfig()
        self._outputs: Optional[CNGStationOutputs] = None
        self._issues: list = []
    
    @property
    def outputs(self) -> CNGStationOutputs:
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> list:
        return self._issues
    
    def calculate(self, trucks_per_day: Optional[int] = None) -> CNGStationOutputs:
        """
        Run CNG station calculations.
        
        Args:
            trucks_per_day: Number of trucks to fuel. If None, uses config.
        
        Returns:
            CNGStationOutputs with all calculated values.
        """
        self._issues = []
        cfg = self.config
        
        # Get truck count
        trucks = trucks_per_day if trucks_per_day is not None else cfg.trucks_per_day
        
        # Handle zero demand
        if trucks == 0:
            self._outputs = self._zero_output()
            return self._outputs
        
        # ============================================================
        # DEMAND CALCULATION
        # ============================================================
        
        # Daily demand in native units (GGE or DGE)
        daily_demand_native = trucks * cfg.fuel_per_truck_amount
        
        # Convert to GGE for standardization
        # Excel C11: =IF(C4="GGE", C3*C5, C3*C5*J7/J6)
        if cfg.fuel_per_truck_unit == "GGE":
            daily_demand_GGE = daily_demand_native
        else:  # DGE
            # DGE to GGE conversion: DGE * (DGE_scf/GGE_scf)
            daily_demand_GGE = daily_demand_native * (cfg.scf_per_DGE / cfg.scf_per_GGE)
        
        # Convert to scf
        # Excel C12: =IF(C4="GGE",C3*C5*(J6/J5),C3*C5*(J7/J5))
        daily_demand_scf = daily_demand_GGE * cfg.scf_per_GGE
        
        # ============================================================
        # LOSSES
        # ============================================================
        
        # Dryer purge (PSA type)
        # Excel C46: =C12*C21
        if cfg.dryer_type == "PSA":
            dryer_purge_fraction = cfg.dryer_purge_fraction
        else:  # TSA
            dryer_purge_fraction = 0
        dryer_purge_loss_scf = daily_demand_scf * dryer_purge_fraction
        
        # Blowdown/vent losses
        # Excel C47: =C12*C22
        blowdown_loss_scf = daily_demand_scf * cfg.blowdown_vent_losses
        
        # Fugitive leaks
        # Excel C48: =C12*C23
        fugitive_loss_scf = daily_demand_scf * cfg.fugitive_leaks
        
        # Total NG consumption
        # Excel C52: =C12+SUM(C46:C48)
        total_losses_scf = dryer_purge_loss_scf + blowdown_loss_scf + fugitive_loss_scf
        total_NG_consumption_scf = daily_demand_scf + total_losses_scf
        
        # VALIDATION: Check loss fraction
        total_loss_fraction = total_losses_scf / daily_demand_scf
        if total_loss_fraction > 0.05:
            self._issues.append(
                f"Total CNG losses ({total_loss_fraction:.1%}) exceed 5%. "
                f"Consider reviewing dryer type and maintenance practices."
            )
        
        # ============================================================
        # PEAK ANALYSIS
        # ============================================================
        
        # Peak window demand
        # Excel C45: =C12*C6/100 (daily * peak_fraction)
        peak_window_demand_scf = daily_demand_scf * cfg.peak_window_trucks_fraction
        
        # Peak flow rate
        # Excel C19: =C45/C7 (peak_demand / peak_hours)
        peak_flow_scfh = peak_window_demand_scf / cfg.peak_window_hours
        
        # Minimum compressor capacity (scfm)
        # Excel C34: =C19/60
        min_compressor_scfm = peak_flow_scfh / 60
        
        # Recommended compressor (with 20% margin)
        # Excel C35: =ROUNDUP(C34*1.2, 0)
        recommended_compressor_scfm = min_compressor_scfm * 1.2
        
        # ============================================================
        # ENERGY CONSUMPTION
        # ============================================================
        
        # Compressor energy
        # Excel C37: =IF(C4="GGE",C11*C17,C11*C18)
        if cfg.fuel_per_truck_unit == "GGE":
            compressor_energy_kWh = daily_demand_GGE * cfg.compressor_energy_per_GGE
        else:
            compressor_energy_kWh = daily_demand_GGE * cfg.compressor_energy_per_DGE
        
        # Dispenser energy
        # Excel C42: =(C29*C27+(24-C29)*C28)*C9
        # Active hours * active_kW + idle hours * idle_kW * num_hoses
        active_hours_per_hose = cfg.dispenser_active_hours_per_hose
        idle_hours_per_hose = 24 - active_hours_per_hose
        dispenser_energy_kWh = (
            (active_hours_per_hose * cfg.dispenser_active_kW + 
             idle_hours_per_hose * cfg.dispenser_idle_kW) * 
            cfg.active_hoses_during_peak
        )
        
        # Aftercooler and auxiliary
        # Excel C43: =5*C25+C26
        aftercooler_aux_kWh = (cfg.aftercooler_fan_power_kW * cfg.aftercooler_run_hours_per_day + 
                               cfg.aux_controls_building_kWh_day)
        
        # Total electricity
        # Excel C54: =IF(C3=0,0,C42+C43+C37)
        total_electricity_kWh = compressor_energy_kWh + dispenser_energy_kWh + aftercooler_aux_kWh
        
        # ============================================================
        # BUILD OUTPUT
        # ============================================================
        
        self._outputs = CNGStationOutputs(
            trucks_per_day=trucks,
            fuel_per_truck=cfg.fuel_per_truck_amount,
            fuel_unit=cfg.fuel_per_truck_unit,
            daily_demand_GGE=daily_demand_GGE,
            daily_demand_scf=daily_demand_scf,
            total_NG_consumption_scf_day=total_NG_consumption_scf,
            dryer_purge_loss_scf_day=dryer_purge_loss_scf,
            blowdown_loss_scf_day=blowdown_loss_scf,
            fugitive_loss_scf_day=fugitive_loss_scf,
            compressor_capacity_scfm=recommended_compressor_scfm,
            compressor_energy_kWh_day=compressor_energy_kWh,
            dispenser_energy_kWh_day=dispenser_energy_kWh,
            aftercooler_aux_energy_kWh_day=aftercooler_aux_kWh,
            total_electricity_kWh_day=total_electricity_kWh,
            peak_flow_scfh=peak_flow_scfh,
            peak_window_demand_scf=peak_window_demand_scf,
        )
        
        return self._outputs
    
    def _zero_output(self) -> CNGStationOutputs:
        """Return zero outputs when no trucks."""
        cfg = self.config
        return CNGStationOutputs(
            trucks_per_day=0, fuel_per_truck=cfg.fuel_per_truck_amount,
            fuel_unit=cfg.fuel_per_truck_unit,
            daily_demand_GGE=0, daily_demand_scf=0,
            total_NG_consumption_scf_day=0,
            dryer_purge_loss_scf_day=0, blowdown_loss_scf_day=0, fugitive_loss_scf_day=0,
            compressor_capacity_scfm=0, compressor_energy_kWh_day=0,
            dispenser_energy_kWh_day=0, aftercooler_aux_energy_kWh_day=0,
            total_electricity_kWh_day=0,
            peak_flow_scfh=0, peak_window_demand_scf=0,
        )
    
    def summary(self) -> str:
        """Return formatted summary."""
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"  ⚠ {issue}" for issue in self._issues) if self._issues else "  None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                   CNG STATION SUMMARY                            ║
╠══════════════════════════════════════════════════════════════════╣
║  DEMAND                                                          ║
║    Trucks per Day:           {o.trucks_per_day:>12,d}                   ║
║    Fuel per Truck:           {o.fuel_per_truck:>12,.0f} {o.fuel_unit:<16}║
║    Daily Demand:             {o.daily_demand_GGE:>12,.0f} GGE              ║
║    Daily Demand:             {o.daily_demand_scf:>12,.0f} scf              ║
║                                                                  ║
║  NG CONSUMPTION                                                  ║
║    Dispensed:                {o.daily_demand_scf:>12,.0f} scf/day           ║
║    Dryer Purge Loss:         {o.dryer_purge_loss_scf_day:>12,.0f} scf/day           ║
║    Blowdown Loss:            {o.blowdown_loss_scf_day:>12,.0f} scf/day           ║
║    Fugitive Loss:            {o.fugitive_loss_scf_day:>12,.0f} scf/day           ║
║    Total Consumption:        {o.total_NG_consumption_scf_day:>12,.0f} scf/day           ║
║                                                                  ║
║  EQUIPMENT SIZING                                                ║
║    Peak Flow:                {o.peak_flow_scfh:>12,.0f} scfh             ║
║    Compressor Size:          {o.compressor_capacity_scfm:>12,.0f} scfm             ║
║                                                                  ║
║  ELECTRICITY                                                     ║
║    Compressor:               {o.compressor_energy_kWh_day:>12,.2f} kWh/day          ║
║    Dispenser:                {o.dispenser_energy_kWh_day:>12,.2f} kWh/day          ║
║    Aftercooler/Aux:          {o.aftercooler_aux_energy_kWh_day:>12,.2f} kWh/day          ║
║    Total:                    {o.total_electricity_kWh_day:>12,.2f} kWh/day          ║
╠══════════════════════════════════════════════════════════════════╣
║  ISSUES IDENTIFIED                                               ║
{issues_str}
╚══════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 70)
    print("CNG STATION MODULE VALIDATION")
    print("=" * 70)
    
    # Test with default config (25 trucks)
    cng = CNGStation()
    outputs = cng.calculate(trucks_per_day=25)
    
    print(cng.summary())
    
    # Compare with Excel
    print("\nComparison with Excel (CNG_Station sheet):")
    print("-" * 60)
    print(f"Daily demand: {outputs.daily_demand_scf:.0f} scf (Excel C12: 228000)")
    print(f"Total NG: {outputs.total_NG_consumption_scf_day:.0f} scf (Excel C52: 234156)")
    print(f"Compressor energy: {outputs.compressor_energy_kWh_day:.0f} kWh (Excel C37: 1200)")
    print(f"Total electricity: {outputs.total_electricity_kWh_day:.2f} kWh (Excel C54: 1301.8)")
