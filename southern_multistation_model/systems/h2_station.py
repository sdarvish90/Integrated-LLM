"""
H2 Station Module (Compressor + Chiller + Dispenser)

This module handles the hydrogen dispensing station:
- Receives H2 from LP Buffer (on-site production) and/or GH2 Trailer
- Compresses to 900 bar
- Pre-cools to -40°C for fast filling
- Dispenses to vehicles

CRITICAL REVIEW NOTES:
1. Two inlet pressure scenarios with different SEC:
   - From LP Buffer (20-30 bar): SEC = 8 kWh/kg
   - From GH2 Trailer (250 bar): SEC = 1.5 kWh/kg
2. The SEC values are reasonable for multi-stage compression to 900 bar.
3. Pre-cooling to -40°C is required for SAE J2601 fast-fill protocol.
4. H2 losses of 2% are on the low end; industry typically sees 2-10%.
"""

from dataclasses import dataclass
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import H2StationConfig


@dataclass
class H2StationOutputs:
    """Output results from H2 Station calculations."""
    
    # Throughput
    H2_input_kg_day: float  # Total H2 input
    H2_from_buffer_kg_day: float  # H2 from on-site production (LP buffer)
    H2_from_GH2_trailer_kg_day: float  # H2 from gaseous trailer
    H2_dispensed_kg_day: float  # H2 actually dispensed (after losses)
    H2_loss_kg_day: float  # Losses
    
    # Compressor
    compressor_power_buffer_kWh_day: float  # Energy for buffer H2
    compressor_power_trailer_kWh_day: float  # Energy for trailer H2
    compressor_power_total_kWh_day: float  # Total compression energy
    compressor_waste_heat_kWh_day: float  # Heat rejection from compressor
    
    # Chiller
    precool_load_kW: float  # Thermal cooling load
    chiller_power_kWh_day: float  # Electrical power for chiller
    
    # Total station
    BOP_power_kWh_day: float  # Balance of plant
    total_power_kWh_day: float  # Total electrical consumption
    
    # For fuel cell (excess H2)
    H2_excess_for_FC_kg_day: float  # Excess H2 that can go to fuel cell


class H2Station:
    """
    Hydrogen Dispensing Station.
    
    Handles compression, cooling, and dispensing of hydrogen to vehicles.
    Can receive H2 from multiple sources:
    - LP Buffer: On-site production at 20-30 bar
    - GH2 Trailer: Delivered gaseous H2 at ~250 bar
    - LH2 pathway: Handled separately (already at high pressure)
    
    Key components:
    - Multi-stage compressor (to 900 bar)
    - Pre-cooling chiller (to -40°C)
    - High-pressure storage cascade
    - Dispenser with T40 protocol
    """
    
    def __init__(self, config: Optional[H2StationConfig] = None):
        self.config = config or H2StationConfig()
        self._outputs: Optional[H2StationOutputs] = None
        self._issues: list = []
    
    @property
    def outputs(self) -> H2StationOutputs:
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    @property
    def issues(self) -> list:
        return self._issues
    
    def calculate(self,
                  H2_from_buffer_kg_day: float = 0,
                  H2_from_GH2_trailer_kg_day: float = 0,
                  H2_station_demand_kg_day: float = 1000,
                  fuel_cell_enabled: bool = False) -> H2StationOutputs:
        """
        Run H2 station calculations.
        
        Args:
            H2_from_buffer_kg_day: H2 from on-site production (LP buffer, 20-30 bar)
            H2_from_GH2_trailer_kg_day: H2 from gaseous trailer delivery (250 bar)
            H2_station_demand_kg_day: Target dispensing demand
            fuel_cell_enabled: Whether excess H2 goes to fuel cell
        
        Returns:
            H2StationOutputs with all calculated values.
        """
        self._issues = []
        cfg = self.config
        
        # ============================================================
        # H2 MASS BALANCE
        # ============================================================
        
        # Total H2 input
        H2_input_kg_day = H2_from_buffer_kg_day + H2_from_GH2_trailer_kg_day
        
        # Handle zero input
        if H2_input_kg_day == 0:
            self._outputs = self._zero_output()
            return self._outputs
        
        # Losses
        H2_loss_kg_day = H2_input_kg_day * cfg.H2_loss_fraction
        H2_available_kg_day = H2_input_kg_day * (1 - cfg.H2_loss_fraction)
        
        # Determine dispensed vs excess
        if H2_available_kg_day >= H2_station_demand_kg_day:
            H2_dispensed_kg_day = H2_station_demand_kg_day
            H2_excess_kg_day = H2_available_kg_day - H2_station_demand_kg_day
        else:
            H2_dispensed_kg_day = H2_available_kg_day
            H2_excess_kg_day = 0
            self._issues.append(
                f"H2 supply ({H2_available_kg_day:.1f} kg/day) is less than demand "
                f"({H2_station_demand_kg_day:.1f} kg/day). Station will be undersupplied."
            )
        
        # Excess to fuel cell (only from buffer, and only if FC enabled)
        if fuel_cell_enabled and H2_excess_kg_day > 0:
            # Only buffer excess goes to FC (not trailer H2)
            buffer_fraction = H2_from_buffer_kg_day / H2_input_kg_day if H2_input_kg_day > 0 else 0
            H2_excess_for_FC_kg_day = H2_excess_kg_day * buffer_fraction
        else:
            H2_excess_for_FC_kg_day = 0
        
        # ============================================================
        # COMPRESSOR ENERGY
        # ============================================================
        
        # H2 from buffer (low pressure) - higher compression work
        # Flow rate accounting for losses (compress before losses occur)
        H2_from_buffer_to_compress = H2_from_buffer_kg_day * (1 - cfg.H2_loss_fraction)
        compressor_power_buffer_kWh_day = H2_from_buffer_to_compress * cfg.SEC_comp_20_to_900
        
        # H2 from trailer (higher pressure) - lower compression work
        H2_from_trailer_to_compress = H2_from_GH2_trailer_kg_day * (1 - cfg.H2_loss_fraction)
        compressor_power_trailer_kWh_day = H2_from_trailer_to_compress * cfg.SEC_comp_250_to_900
        
        # Total compression energy
        compressor_power_total_kWh_day = compressor_power_buffer_kWh_day + compressor_power_trailer_kWh_day
        
        # VALIDATION: Check SEC reasonableness
        # Theoretical minimum for isothermal compression from 30 bar to 900 bar:
        # W = RT * ln(P2/P1) = 8.314 * 300 * ln(900/30) / 2.016 = ~4.3 kWh/kg
        # Real compressors at ~50-60% efficiency: 7-9 kWh/kg - matches!
        if cfg.SEC_comp_20_to_900 < 4:
            self._issues.append(
                f"SEC from buffer ({cfg.SEC_comp_20_to_900} kWh/kg) is below theoretical "
                f"minimum (~4.3 kWh/kg for 30→900 bar). Check assumption."
            )
        
        # Compressor waste heat (first-order: all shaft power becomes heat)
        compressor_waste_heat_kWh_day = compressor_power_total_kWh_day
        
        # ============================================================
        # CHILLER / PRE-COOLING
        # ============================================================
        
        # Pre-cooling load for T40 protocol
        # Excel C33: =C32*C17 where C32 is flow rate, C17 is SEC_precool
        H2_to_dispense_kg_h = H2_dispensed_kg_day / cfg.hours_per_day
        precool_load_kW = H2_to_dispense_kg_h * cfg.SEC_precool_kWh_th_per_kg
        
        # Chiller electrical power (thermal load / COP)
        # Excel C34: =C33/C18*24
        chiller_power_kWh_day = (precool_load_kW / cfg.COP_chiller_dispenser) * cfg.hours_per_day
        
        # ============================================================
        # TOTAL STATION POWER
        # ============================================================
        
        # BOP power (controls, lighting, etc.)
        BOP_power_kWh_day = cfg.BOP_power_kWh_day
        
        # Total
        total_power_kWh_day = (compressor_power_total_kWh_day + 
                               chiller_power_kWh_day + 
                               BOP_power_kWh_day)
        
        # ============================================================
        # BUILD OUTPUT
        # ============================================================
        
        self._outputs = H2StationOutputs(
            H2_input_kg_day=H2_input_kg_day,
            H2_from_buffer_kg_day=H2_from_buffer_kg_day,
            H2_from_GH2_trailer_kg_day=H2_from_GH2_trailer_kg_day,
            H2_dispensed_kg_day=H2_dispensed_kg_day,
            H2_loss_kg_day=H2_loss_kg_day,
            compressor_power_buffer_kWh_day=compressor_power_buffer_kWh_day,
            compressor_power_trailer_kWh_day=compressor_power_trailer_kWh_day,
            compressor_power_total_kWh_day=compressor_power_total_kWh_day,
            compressor_waste_heat_kWh_day=compressor_waste_heat_kWh_day,
            precool_load_kW=precool_load_kW,
            chiller_power_kWh_day=chiller_power_kWh_day,
            BOP_power_kWh_day=BOP_power_kWh_day,
            total_power_kWh_day=total_power_kWh_day,
            H2_excess_for_FC_kg_day=H2_excess_for_FC_kg_day,
        )
        
        return self._outputs
    
    def _zero_output(self) -> H2StationOutputs:
        """Return zero outputs when no H2 input."""
        return H2StationOutputs(
            H2_input_kg_day=0, H2_from_buffer_kg_day=0,
            H2_from_GH2_trailer_kg_day=0, H2_dispensed_kg_day=0,
            H2_loss_kg_day=0,
            compressor_power_buffer_kWh_day=0,
            compressor_power_trailer_kWh_day=0,
            compressor_power_total_kWh_day=0,
            compressor_waste_heat_kWh_day=0,
            precool_load_kW=0, chiller_power_kWh_day=0,
            BOP_power_kWh_day=0, total_power_kWh_day=0,
            H2_excess_for_FC_kg_day=0,
        )
    
    def summary(self) -> str:
        """Return formatted summary."""
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"  ⚠ {issue}" for issue in self._issues) if self._issues else "  None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                   H2 STATION SUMMARY                             ║
╠══════════════════════════════════════════════════════════════════╣
║  H2 MASS BALANCE                                                 ║
║    From LP Buffer:           {o.H2_from_buffer_kg_day:>12,.2f} kg/day            ║
║    From GH2 Trailer:         {o.H2_from_GH2_trailer_kg_day:>12,.2f} kg/day            ║
║    Total Input:              {o.H2_input_kg_day:>12,.2f} kg/day            ║
║    Losses:                   {o.H2_loss_kg_day:>12,.2f} kg/day            ║
║    Dispensed:                {o.H2_dispensed_kg_day:>12,.2f} kg/day            ║
║    Excess for FC:            {o.H2_excess_for_FC_kg_day:>12,.2f} kg/day            ║
║                                                                  ║
║  COMPRESSOR                                                      ║
║    Energy (from buffer):     {o.compressor_power_buffer_kWh_day:>12,.2f} kWh/day          ║
║    Energy (from trailer):    {o.compressor_power_trailer_kWh_day:>12,.2f} kWh/day          ║
║    Total Compression:        {o.compressor_power_total_kWh_day:>12,.2f} kWh/day          ║
║    Waste Heat:               {o.compressor_waste_heat_kWh_day:>12,.2f} kWh_th/day       ║
║                                                                  ║
║  CHILLER                                                         ║
║    Pre-cool Load:            {o.precool_load_kW:>12,.2f} kW_th            ║
║    Chiller Power:            {o.chiller_power_kWh_day:>12,.2f} kWh/day          ║
║                                                                  ║
║  TOTAL STATION                                                   ║
║    BOP Power:                {o.BOP_power_kWh_day:>12,.2f} kWh/day          ║
║    Total Power:              {o.total_power_kWh_day:>12,.2f} kWh/day          ║
╠══════════════════════════════════════════════════════════════════╣
║  ISSUES IDENTIFIED                                               ║
{issues_str}
╚══════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 70)
    print("H2 STATION MODULE VALIDATION")
    print("=" * 70)
    
    # Test with buffer only (on-site production)
    station = H2Station()
    outputs = station.calculate(
        H2_from_buffer_kg_day=522.2,  # From TriGen
        H2_from_GH2_trailer_kg_day=0,
        H2_station_demand_kg_day=1000,
        fuel_cell_enabled=False
    )
    
    print(station.summary())
    
    # Compare with Excel (H2_Station sheet)
    print("\nComparison with Excel (H2_Station sheet):")
    print("-" * 60)
    print(f"Total power: {outputs.total_power_kWh_day:.2f} kWh/day (Excel C41: 4502.28)")
