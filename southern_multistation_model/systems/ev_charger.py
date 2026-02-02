"""
EV Charger (DCFC) Module

This module handles DC Fast Charging for electric vehicles.
Simple demand model - draws from electricity bus.

CRITICAL REVIEW NOTES:
1. The Excel model treats EV charging as a simple demand (20 MWh/day default).
2. In reality, EV charging has significant time-of-day variation.
3. This module could be enhanced with load profiles and demand charges.
4. Excess power from TriGen/FC/BESS can offset grid draw.
"""

from dataclasses import dataclass
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EVChargerConfig


@dataclass
class EVChargerOutputs:
    """Output results from EV Charger calculations."""
    
    # Demand
    daily_demand_kWh: float
    average_power_kW: float  # 24-hour average
    
    # Supply sources
    from_trigen_kWh: float
    from_fuel_cell_kWh: float
    from_bess_kWh: float
    from_grid_kWh: float
    
    # Utilization
    self_supply_fraction: float  # Fraction from on-site generation


class EVCharger:
    """
    DC Fast Charging System for Electric Vehicles.
    
    Simple model: specified daily demand in kWh.
    Supply priority:
    1. Excess TriGen power
    2. Fuel cell output
    3. Battery discharge
    4. Grid import
    
    Future enhancements could include:
    - Hourly load profiles
    - Multiple charger ports
    - Demand charge optimization
    - V2G (vehicle-to-grid) capability
    """
    
    def __init__(self, config: Optional[EVChargerConfig] = None):
        self.config = config or EVChargerConfig()
        self._outputs: Optional[EVChargerOutputs] = None
        self._issues: list = []
    
    @property
    def outputs(self) -> EVChargerOutputs:
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> list:
        return self._issues
    
    def calculate(self,
                  daily_demand_kWh: Optional[float] = None,
                  available_from_trigen_kWh: float = 0,
                  available_from_fc_kWh: float = 0,
                  available_from_bess_kWh: float = 0) -> EVChargerOutputs:
        """
        Run EV charger energy balance.
        
        Args:
            daily_demand_kWh: EV charging demand. If None, uses config.
            available_from_trigen_kWh: Excess power from TriGen
            available_from_fc_kWh: Power from fuel cell
            available_from_bess_kWh: Available battery discharge
        
        Returns:
            EVChargerOutputs with supply breakdown.
        """
        self._issues = []
        cfg = self.config
        
        # Get demand
        demand = daily_demand_kWh if daily_demand_kWh is not None else cfg.daily_demand_kWh
        
        # Handle zero demand
        if demand == 0:
            self._outputs = self._zero_output()
            return self._outputs
        
        # ============================================================
        # SUPPLY ALLOCATION
        # ============================================================
        
        remaining_demand = demand
        
        # 1. TriGen excess
        from_trigen = min(available_from_trigen_kWh, remaining_demand)
        remaining_demand -= from_trigen
        
        # 2. Fuel cell
        from_fc = min(available_from_fc_kWh, remaining_demand)
        remaining_demand -= from_fc
        
        # 3. Battery
        from_bess = min(available_from_bess_kWh, remaining_demand)
        remaining_demand -= from_bess
        
        # 4. Grid (whatever's left)
        from_grid = remaining_demand
        
        # ============================================================
        # METRICS
        # ============================================================
        
        average_power = demand / 24
        self_supply = from_trigen + from_fc + from_bess
        self_supply_fraction = self_supply / demand if demand > 0 else 0
        
        # VALIDATION
        if from_grid > 0.8 * demand:
            self._issues.append(
                f"Grid supplies {from_grid/demand:.0%} of EV charging demand. "
                f"Consider increasing on-site generation or battery capacity."
            )
        
        # ============================================================
        # BUILD OUTPUT
        # ============================================================
        
        self._outputs = EVChargerOutputs(
            daily_demand_kWh=demand,
            average_power_kW=average_power,
            from_trigen_kWh=from_trigen,
            from_fuel_cell_kWh=from_fc,
            from_bess_kWh=from_bess,
            from_grid_kWh=from_grid,
            self_supply_fraction=self_supply_fraction,
        )
        
        return self._outputs
    
    def _zero_output(self) -> EVChargerOutputs:
        """Return zero outputs when no demand."""
        return EVChargerOutputs(
            daily_demand_kWh=0, average_power_kW=0,
            from_trigen_kWh=0, from_fuel_cell_kWh=0,
            from_bess_kWh=0, from_grid_kWh=0,
            self_supply_fraction=0,
        )
    
    def summary(self) -> str:
        """Return formatted summary."""
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"  ⚠ {issue}" for issue in self._issues) if self._issues else "  None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                   EV CHARGER SUMMARY                             ║
╠══════════════════════════════════════════════════════════════════╣
║  DEMAND                                                          ║
║    Daily Demand:             {o.daily_demand_kWh:>12,.2f} kWh/day          ║
║    Average Power:            {o.average_power_kW:>12,.2f} kW               ║
║                                                                  ║
║  SUPPLY SOURCES                                                  ║
║    From TriGen:              {o.from_trigen_kWh:>12,.2f} kWh/day          ║
║    From Fuel Cell:           {o.from_fuel_cell_kWh:>12,.2f} kWh/day          ║
║    From Battery:             {o.from_bess_kWh:>12,.2f} kWh/day          ║
║    From Grid:                {o.from_grid_kWh:>12,.2f} kWh/day          ║
║                                                                  ║
║  METRICS                                                         ║
║    Self-Supply Fraction:     {o.self_supply_fraction:>12,.1%}                 ║
╠══════════════════════════════════════════════════════════════════╣
║  ISSUES IDENTIFIED                                               ║
{issues_str}
╚══════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 70)
    print("EV CHARGER MODULE VALIDATION")
    print("=" * 70)
    
    ev = EVCharger()
    outputs = ev.calculate(
        daily_demand_kWh=20000,
        available_from_trigen_kWh=15000,
        available_from_fc_kWh=0,
        available_from_bess_kWh=2000
    )
    
    print(ev.summary())
