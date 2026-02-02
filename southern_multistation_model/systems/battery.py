"""
Battery Energy Storage System (BESS) Module

This module handles battery storage for load leveling and grid independence.

CRITICAL REVIEW NOTES:
1. The Excel model has a simple daily dispatch: discharge once per day
   when TriGen + FC output is less than station load.
2. Roundtrip efficiency of 90% is typical for lithium-ion.
3. SOC limits of 20%-90% protect battery life (70% usable capacity).
4. The model doesn't include degradation over time or temperature effects.
5. NOTE: Excel sets BESS capacity to 0 by default, making it inactive.
"""

from dataclasses import dataclass
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import BESSConfig


@dataclass
class BESSOutputs:
    """Output results from BESS calculations."""
    
    # Capacity
    capacity_nameplate_kWh: float
    capacity_usable_kWh: float  # After SOC limits
    
    # Energy flows
    energy_charged_kWh_day: float  # Energy into battery
    energy_discharged_kWh_day: float  # Energy out of battery
    losses_kWh_day: float  # Roundtrip losses
    
    # Power (peak)
    max_charge_power_kW: float
    max_discharge_power_kW: float
    
    # State
    SOC_min: float
    SOC_max: float
    cycles_per_day: float  # Approximate daily cycling


class BESS:
    """
    Battery Energy Storage System.
    
    Provides load leveling and backup power for the station.
    Charges from excess TriGen/FC power, discharges during shortfall.
    
    Key characteristics:
    - Lithium-ion chemistry assumed
    - 90% roundtrip efficiency
    - 70% depth of discharge (20-90% SOC window)
    - Single daily discharge cycle (simplified dispatch)
    """
    
    def __init__(self, config: Optional[BESSConfig] = None):
        self.config = config or BESSConfig()
        self._outputs: Optional[BESSOutputs] = None
        self._issues: list = []
    
    @property
    def outputs(self) -> BESSOutputs:
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> list:
        return self._issues
    
    def calculate(self,
                  excess_power_kWh_day: float = 0,
                  shortfall_kWh_day: float = 0) -> BESSOutputs:
        """
        Run BESS energy balance.
        
        Args:
            excess_power_kWh_day: Excess power available for charging (kWh/day)
            shortfall_kWh_day: Power shortfall requiring discharge (kWh/day)
        
        Returns:
            BESSOutputs with all calculated values.
        """
        self._issues = []
        cfg = self.config
        
        # ============================================================
        # CAPACITY
        # ============================================================
        
        capacity_nameplate = cfg.capacity_nameplate_kWh
        
        # Usable capacity based on SOC limits
        DOD = cfg.SOC_max_frac - cfg.SOC_min_frac
        capacity_usable = capacity_nameplate * DOD
        
        # Handle zero capacity
        if capacity_nameplate == 0:
            self._outputs = self._zero_output()
            return self._outputs
        
        # ============================================================
        # ENERGY FLOWS
        # ============================================================
        
        # Maximum energy that can be charged (limited by usable capacity)
        max_chargeable = capacity_usable / (cfg.roundtrip_efficiency ** 0.5)
        
        # Maximum energy that can be discharged
        max_dischargeable = capacity_usable * (cfg.roundtrip_efficiency ** 0.5)
        
        # Actual charging (limited by excess and capacity)
        energy_charged = min(excess_power_kWh_day, max_chargeable)
        
        # Energy stored after charge losses
        energy_stored = energy_charged * (cfg.roundtrip_efficiency ** 0.5)
        
        # Actual discharge (limited by shortfall and stored energy)
        energy_discharged = min(shortfall_kWh_day, 
                                energy_stored * (cfg.roundtrip_efficiency ** 0.5))
        
        # Total losses
        losses = energy_charged - energy_discharged
        
        # ============================================================
        # POWER RATINGS
        # ============================================================
        
        # Assume 4-hour battery (C/4 rate)
        max_charge_power_kW = capacity_nameplate / 4
        max_discharge_power_kW = capacity_nameplate / 4
        
        # ============================================================
        # CYCLING
        # ============================================================
        
        if capacity_usable > 0:
            cycles_per_day = energy_discharged / capacity_usable
        else:
            cycles_per_day = 0
        
        # VALIDATION: Check cycling rate
        if cycles_per_day > 2:
            self._issues.append(
                f"Battery cycling {cycles_per_day:.1f} times/day exceeds typical "
                f"design limit of 1-2 cycles/day. This may accelerate degradation."
            )
        
        # ============================================================
        # BUILD OUTPUT
        # ============================================================
        
        self._outputs = BESSOutputs(
            capacity_nameplate_kWh=capacity_nameplate,
            capacity_usable_kWh=capacity_usable,
            energy_charged_kWh_day=energy_charged,
            energy_discharged_kWh_day=energy_discharged,
            losses_kWh_day=losses,
            max_charge_power_kW=max_charge_power_kW,
            max_discharge_power_kW=max_discharge_power_kW,
            SOC_min=cfg.SOC_min_frac,
            SOC_max=cfg.SOC_max_frac,
            cycles_per_day=cycles_per_day,
        )
        
        return self._outputs
    
    def _zero_output(self) -> BESSOutputs:
        """Return zero outputs when battery capacity is zero."""
        cfg = self.config
        return BESSOutputs(
            capacity_nameplate_kWh=0, capacity_usable_kWh=0,
            energy_charged_kWh_day=0, energy_discharged_kWh_day=0,
            losses_kWh_day=0,
            max_charge_power_kW=0, max_discharge_power_kW=0,
            SOC_min=cfg.SOC_min_frac, SOC_max=cfg.SOC_max_frac,
            cycles_per_day=0,
        )
    
    def summary(self) -> str:
        """Return formatted summary."""
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"  ⚠ {issue}" for issue in self._issues) if self._issues else "  None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                      BESS SUMMARY                                ║
╠══════════════════════════════════════════════════════════════════╣
║  CAPACITY                                                        ║
║    Nameplate:                {o.capacity_nameplate_kWh:>12,.2f} kWh              ║
║    Usable (SOC limits):      {o.capacity_usable_kWh:>12,.2f} kWh              ║
║    SOC Window:               {o.SOC_min:.0%} - {o.SOC_max:.0%}                        ║
║                                                                  ║
║  ENERGY FLOWS                                                    ║
║    Charged:                  {o.energy_charged_kWh_day:>12,.2f} kWh/day          ║
║    Discharged:               {o.energy_discharged_kWh_day:>12,.2f} kWh/day          ║
║    Losses:                   {o.losses_kWh_day:>12,.2f} kWh/day          ║
║                                                                  ║
║  POWER RATINGS                                                   ║
║    Max Charge:               {o.max_charge_power_kW:>12,.2f} kW               ║
║    Max Discharge:            {o.max_discharge_power_kW:>12,.2f} kW               ║
║                                                                  ║
║  CYCLING                                                         ║
║    Cycles per Day:           {o.cycles_per_day:>12,.2f}                   ║
╠══════════════════════════════════════════════════════════════════╣
║  ISSUES IDENTIFIED                                               ║
{issues_str}
╚══════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 70)
    print("BESS MODULE VALIDATION")
    print("=" * 70)
    
    # Test with 4 MWh battery
    config = BESSConfig(capacity_nameplate_kWh=4000)
    bess = BESS(config)
    outputs = bess.calculate(excess_power_kWh_day=5000, shortfall_kWh_day=2000)
    
    print(bess.summary())
