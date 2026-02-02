"""
Station Integration Module

This module orchestrates all subsystems and performs the integrated
mass and energy balance for the complete station.

The integration follows these steps:
1. Calculate H2 production (TriGen, SMR, Electrolyzer)
2. Calculate H2 delivery (LH2, GH2 trailer)
3. Determine H2 station operation (compression, dispensing)
4. Calculate fuel cell output (if enabled, from excess H2)
5. Calculate electricity balance (generation vs consumption)
6. Determine grid import/export and EV charging supply
7. Calculate CNG station (independent but shares NG and electricity)
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import StationConfig
from systems.trigen import TriGen
from systems.smr import SMR
from systems.electrolyzer import Electrolyzer
from systems.lh2_delivery import LH2Delivery
from systems.h2_station import H2Station
from systems.fuel_cell import FuelCell
from systems.battery import BESS
from systems.cng_station import CNGStation
from systems.ev_charger import EVCharger


@dataclass
class StationOutputs:
    """Aggregated outputs from the entire station."""
    
    # H2 Balance
    H2_from_trigen_kg_day: float = 0
    H2_from_smr_kg_day: float = 0
    H2_from_electrolyzer_kg_day: float = 0
    H2_from_LH2_delivery_kg_day: float = 0
    H2_from_GH2_trailer_kg_day: float = 0
    H2_total_produced_kg_day: float = 0
    H2_to_station_kg_day: float = 0
    H2_to_fuel_cell_kg_day: float = 0
    H2_dispensed_kg_day: float = 0
    
    # NG Balance
    NG_to_trigen_scf_day: float = 0
    NG_to_smr_scf_day: float = 0
    NG_to_CNG_station_scf_day: float = 0
    NG_aux_heaters_scf_day: float = 0
    NG_total_scf_day: float = 0
    
    # Electricity Balance (kWh/day)
    elec_from_trigen: float = 0
    elec_from_fuel_cell: float = 0
    elec_from_bess: float = 0
    elec_from_grid: float = 0
    elec_to_electrolyzer: float = 0
    elec_to_H2_station: float = 0
    elec_to_LH2_pump: float = 0
    elec_to_CNG_station: float = 0
    elec_to_EV_charger: float = 0
    elec_to_bess: float = 0
    elec_total_generation: float = 0
    elec_total_consumption: float = 0
    elec_net_grid: float = 0  # Positive = import, negative = export
    
    # Heat Balance (kWh_th/day)
    heat_from_trigen: float = 0
    heat_from_fuel_cell: float = 0
    heat_to_smr: float = 0
    heat_to_vaporizer: float = 0
    heat_excess: float = 0
    
    # KPIs
    total_energy_input_kWh_day: float = 0
    total_energy_output_kWh_day: float = 0
    levelized_energy_consumption: float = 0  # Input/Output ratio
    
    # Issues collected from all systems
    all_issues: List[str] = field(default_factory=list)


class Station:
    """
    Integrated Multi-Energy Station.
    
    Combines:
    - TriGen (MCFC): NG → Electricity + H2 + Heat
    - SMR: NG + Heat → H2
    - Electrolyzer: Electricity + Water → H2
    - LH2 Delivery: LH2 trailer → pump → vaporizer → dispenser
    - H2 Station: Compression + chilling + dispensing
    - Fuel Cell: Excess H2 → Electricity + Heat
    - Battery: Load leveling
    - CNG Station: NG compression and dispensing
    - EV Charger: DC fast charging
    """
    
    def __init__(self, config: Optional[StationConfig] = None):
        self.config = config or StationConfig()
        
        # Initialize subsystems
        self.trigen = TriGen(self.config.trigen)
        self.smr = SMR(self.config.smr)
        self.electrolyzer = Electrolyzer(self.config.electrolyzer)
        self.lh2_delivery = LH2Delivery(self.config.lh2_delivery)
        self.h2_station = H2Station(self.config.h2_station)
        self.fuel_cell = FuelCell(self.config.fuel_cell)
        self.bess = BESS(self.config.bess)
        self.cng_station = CNGStation(self.config.cng_station)
        self.ev_charger = EVCharger(self.config.ev_charger)
        
        self._outputs: Optional[StationOutputs] = None
    
    @property
    def outputs(self) -> StationOutputs:
        if self._outputs is None:
            raise ValueError("Outputs not calculated. Call calculate() first.")
        return self._outputs
    
    def calculate(self,
                  # TriGen
                  trigen_NG_scf_h: Optional[float] = None,
                  # SMR
                  smr_H2_tpd: float = 0,
                  # Electrolyzer
                  electrolyzer_H2_tpd: float = 0,
                  # LH2 Delivery
                  lh2_throughput_tpd: float = 0,
                  # GH2 Trailer
                  gh2_trailer_kg_day: float = 0,
                  # CNG
                  cng_trucks_per_day: Optional[int] = None,
                  # EV Charger
                  ev_demand_kWh_day: Optional[float] = None,
                  # Fuel cell
                  fuel_cell_enabled: Optional[bool] = None) -> StationOutputs:
        """
        Run integrated station calculations.
        
        Args:
            trigen_NG_scf_h: TriGen NG input (scf/h). None = use config.
            smr_H2_tpd: SMR H2 production target (tpd). 0 = disabled.
            electrolyzer_H2_tpd: Electrolyzer H2 production (tpd). 0 = disabled.
            lh2_throughput_tpd: LH2 delivery throughput (tpd). 0 = disabled.
            gh2_trailer_kg_day: GH2 trailer delivery (kg/day). 0 = disabled.
            cng_trucks_per_day: CNG trucks to fuel. None = use config.
            ev_demand_kWh_day: EV charging demand. None = use config.
            fuel_cell_enabled: Whether FC is active. None = use config.
        
        Returns:
            StationOutputs with all balances and KPIs.
        """
        cfg = self.config
        all_issues = []
        
        # Resolve defaults
        fc_enabled = fuel_cell_enabled if fuel_cell_enabled is not None else cfg.fuel_cell_enabled
        
        # ============================================================
        # STEP 1: TRIGEN CALCULATION
        # ============================================================
        
        trigen_out = self.trigen.calculate(NG_input_scf_h=trigen_NG_scf_h)
        all_issues.extend(self.trigen._issues)
        
        # Heat available for other systems
        heat_available_kW = trigen_out.Q_available_for_export_kW
        
        # ============================================================
        # STEP 2: SMR CALCULATION (uses TriGen heat)
        # ============================================================
        
        # Allocate heat to SMR first
        heat_to_smr_kW = heat_available_kW * 0.5 if smr_H2_tpd > 0 else 0  # Split available heat
        
        smr_out = self.smr.calculate(
            H2_rate_tpd=smr_H2_tpd,
            Q_from_trigen_kW=heat_to_smr_kW
        )
        all_issues.extend(self.smr._issues)
        
        # Remaining heat after SMR
        heat_after_smr_kW = heat_available_kW - heat_to_smr_kW + smr_out.Q_surplus_kW
        
        # ============================================================
        # STEP 3: ELECTROLYZER CALCULATION
        # ============================================================
        
        elec_out = self.electrolyzer.calculate(H2_rate_tpd=electrolyzer_H2_tpd)
        all_issues.extend(self.electrolyzer._issues)
        
        # ============================================================
        # STEP 4: LH2 DELIVERY (uses remaining TriGen heat)
        # ============================================================
        
        # LH2 vaporizer gets remaining heat
        heat_to_vaporizer_kW = heat_after_smr_kW
        
        lh2_out = self.lh2_delivery.calculate(
            throughput_tpd=lh2_throughput_tpd,
            Q_from_trigen_kW=heat_to_vaporizer_kW
        )
        all_issues.extend(self.lh2_delivery._issues)
        
        # ============================================================
        # STEP 5: H2 STATION (from buffer and trailer)
        # ============================================================
        
        # H2 to buffer (on-site production)
        H2_to_buffer_kg_day = (trigen_out.H2_output_kg_day + 
                               smr_out.H2_output_kg_day + 
                               elec_out.H2_output_kg_day)
        
        # Target demand
        H2_demand = cfg.H2_station_demand_kg_day
        
        # Determine excess (before station, for fuel cell)
        # Fuel cell draws from LP buffer if buffer exceeds demand threshold
        if fc_enabled and H2_to_buffer_kg_day > H2_demand:
            H2_excess_for_fc = H2_to_buffer_kg_day - H2_demand
        else:
            H2_excess_for_fc = 0
        
        # H2 actually going to station compression
        H2_buffer_to_station = H2_to_buffer_kg_day - H2_excess_for_fc
        
        h2_station_out = self.h2_station.calculate(
            H2_from_buffer_kg_day=H2_buffer_to_station,
            H2_from_GH2_trailer_kg_day=gh2_trailer_kg_day,
            H2_station_demand_kg_day=H2_demand,
            fuel_cell_enabled=False  # We handle FC separately
        )
        all_issues.extend(self.h2_station._issues)
        
        # LH2 pathway goes directly to dispenser (separate HP storage)
        # Total dispensed = H2 station + LH2 pathway
        total_H2_dispensed = h2_station_out.H2_dispensed_kg_day + lh2_out.H2_output_kg_day
        
        # ============================================================
        # STEP 6: FUEL CELL (from excess buffer H2)
        # ============================================================
        
        fc_out = self.fuel_cell.calculate(H2_input_kg_day=H2_excess_for_fc)
        all_issues.extend(self.fuel_cell._issues)
        
        # ============================================================
        # STEP 7: CNG STATION (independent)
        # ============================================================
        
        cng_out = self.cng_station.calculate(trucks_per_day=cng_trucks_per_day)
        all_issues.extend(self.cng_station._issues)
        
        # ============================================================
        # STEP 8: ELECTRICITY BALANCE
        # ============================================================
        
        # Generation
        elec_gen_trigen = trigen_out.electricity_kWh_day
        elec_gen_fc = fc_out.electricity_net_kWh_day
        
        # Consumption
        elec_con_electrolyzer = elec_out.electricity_kWh_day
        elec_con_h2_station = h2_station_out.total_power_kWh_day
        elec_con_lh2 = lh2_out.electricity_kWh_day
        elec_con_cng = cng_out.total_electricity_kWh_day
        
        # Station load (excluding EV)
        station_load = (elec_con_electrolyzer + elec_con_h2_station + 
                       elec_con_lh2 + elec_con_cng)
        
        # Net before EV and battery
        net_generation = elec_gen_trigen + elec_gen_fc
        excess_for_ev_bess = net_generation - station_load
        
        # ============================================================
        # STEP 9: BATTERY DISPATCH
        # ============================================================
        
        if excess_for_ev_bess > 0:
            # Excess power - can charge battery and supply EV
            bess_out = self.bess.calculate(
                excess_power_kWh_day=excess_for_ev_bess,
                shortfall_kWh_day=0
            )
            available_for_ev = excess_for_ev_bess - bess_out.energy_charged_kWh_day
        else:
            # Shortfall - discharge battery
            bess_out = self.bess.calculate(
                excess_power_kWh_day=0,
                shortfall_kWh_day=-excess_for_ev_bess
            )
            available_for_ev = bess_out.energy_discharged_kWh_day
        all_issues.extend(self.bess._issues)
        
        # ============================================================
        # STEP 10: EV CHARGER
        # ============================================================
        
        ev_demand = ev_demand_kWh_day if ev_demand_kWh_day is not None else cfg.ev_charger.daily_demand_kWh
        
        ev_out = self.ev_charger.calculate(
            daily_demand_kWh=ev_demand,
            available_from_trigen_kWh=max(0, available_for_ev),
            available_from_fc_kWh=0,  # Already counted in trigen excess
            available_from_bess_kWh=bess_out.energy_discharged_kWh_day if excess_for_ev_bess < 0 else 0
        )
        all_issues.extend(self.ev_charger._issues)
        
        # Final grid balance
        total_generation = elec_gen_trigen + elec_gen_fc + bess_out.energy_discharged_kWh_day
        total_consumption = station_load + ev_demand + bess_out.energy_charged_kWh_day
        grid_import = max(0, total_consumption - total_generation + bess_out.energy_charged_kWh_day)
        
        # ============================================================
        # STEP 11: AGGREGATE OUTPUTS
        # ============================================================
        
        # NG balance
        NG_total = (trigen_out.NG_input_scf_day + 
                   smr_out.NG_input_scf_day + 
                   smr_out.NG_aux_heater_scf_h * 24 +
                   lh2_out.NG_aux_heater_scf_h * 24 +
                   cng_out.total_NG_consumption_scf_day)
        
        # Heat balance
        heat_from_trigen_kWh = trigen_out.Q_available_for_export_kW_day
        heat_from_fc_kWh = fc_out.heat_output_kWh_day
        heat_to_smr_kWh = heat_to_smr_kW * 24
        heat_to_vap_kWh = min(heat_to_vaporizer_kW * 24, lh2_out.vaporizer_duty_kW * 24)
        heat_excess_kWh = max(0, heat_from_trigen_kWh + heat_from_fc_kWh - heat_to_smr_kWh - heat_to_vap_kWh)
        
        # KPIs
        # Energy input: NG (HHV) + Grid electricity + delivered H2 (HHV)
        NG_HHV_kWh_per_scf = 0.2847  # From Excel conversions
        H2_HHV_kWh_per_kg = 39.4
        
        energy_in_NG = NG_total * NG_HHV_kWh_per_scf
        energy_in_grid = grid_import
        energy_in_H2_delivered = (lh2_out.H2_throughput_kg_day + gh2_trailer_kg_day) * H2_HHV_kWh_per_kg
        total_energy_input = energy_in_NG + energy_in_grid + energy_in_H2_delivered
        
        # Energy output: Dispensed H2 + CNG + EV charged
        energy_out_H2 = total_H2_dispensed * 33.33  # LHV for vehicle use
        energy_out_CNG = cng_out.daily_demand_scf * NG_HHV_kWh_per_scf
        energy_out_EV = ev_demand
        total_energy_output = energy_out_H2 + energy_out_CNG + energy_out_EV
        
        LEC = total_energy_input / total_energy_output if total_energy_output > 0 else 0
        
        # ============================================================
        # BUILD FINAL OUTPUT
        # ============================================================
        
        self._outputs = StationOutputs(
            # H2 Balance
            H2_from_trigen_kg_day=trigen_out.H2_output_kg_day,
            H2_from_smr_kg_day=smr_out.H2_output_kg_day,
            H2_from_electrolyzer_kg_day=elec_out.H2_output_kg_day,
            H2_from_LH2_delivery_kg_day=lh2_out.H2_output_kg_day,
            H2_from_GH2_trailer_kg_day=gh2_trailer_kg_day,
            H2_total_produced_kg_day=H2_to_buffer_kg_day + lh2_out.H2_throughput_kg_day + gh2_trailer_kg_day,
            H2_to_station_kg_day=H2_buffer_to_station + gh2_trailer_kg_day,
            H2_to_fuel_cell_kg_day=H2_excess_for_fc,
            H2_dispensed_kg_day=total_H2_dispensed,
            
            # NG Balance
            NG_to_trigen_scf_day=trigen_out.NG_input_scf_day,
            NG_to_smr_scf_day=smr_out.NG_input_scf_day,
            NG_to_CNG_station_scf_day=cng_out.total_NG_consumption_scf_day,
            NG_aux_heaters_scf_day=(smr_out.NG_aux_heater_scf_h + lh2_out.NG_aux_heater_scf_h) * 24,
            NG_total_scf_day=NG_total,
            
            # Electricity Balance
            elec_from_trigen=elec_gen_trigen,
            elec_from_fuel_cell=elec_gen_fc,
            elec_from_bess=bess_out.energy_discharged_kWh_day,
            elec_from_grid=grid_import,
            elec_to_electrolyzer=elec_con_electrolyzer,
            elec_to_H2_station=elec_con_h2_station,
            elec_to_LH2_pump=elec_con_lh2,
            elec_to_CNG_station=elec_con_cng,
            elec_to_EV_charger=ev_demand,
            elec_to_bess=bess_out.energy_charged_kWh_day,
            elec_total_generation=total_generation,
            elec_total_consumption=total_consumption,
            elec_net_grid=grid_import,
            
            # Heat Balance
            heat_from_trigen=heat_from_trigen_kWh,
            heat_from_fuel_cell=heat_from_fc_kWh,
            heat_to_smr=heat_to_smr_kWh,
            heat_to_vaporizer=heat_to_vap_kWh,
            heat_excess=heat_excess_kWh,
            
            # KPIs
            total_energy_input_kWh_day=total_energy_input,
            total_energy_output_kWh_day=total_energy_output,
            levelized_energy_consumption=LEC,
            
            # Issues
            all_issues=all_issues,
        )
        
        return self._outputs
    
    def summary(self) -> str:
        """Return comprehensive station summary."""
        if self._outputs is None:
            return "No calculations performed yet. Call calculate() first."
        
        o = self._outputs
        issues_str = "\n".join(f"    • {issue}" for issue in o.all_issues) if o.all_issues else "    None"
        
        return f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     INTEGRATED STATION SUMMARY                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  ═══ HYDROGEN BALANCE ═══                                                    ║
║    Production:                                                               ║
║      From TriGen:            {o.H2_from_trigen_kg_day:>12,.2f} kg/day                        ║
║      From SMR:               {o.H2_from_smr_kg_day:>12,.2f} kg/day                        ║
║      From Electrolyzer:      {o.H2_from_electrolyzer_kg_day:>12,.2f} kg/day                        ║
║    Delivery:                                                                 ║
║      LH2 (vaporized):        {o.H2_from_LH2_delivery_kg_day:>12,.2f} kg/day                        ║
║      GH2 Trailer:            {o.H2_from_GH2_trailer_kg_day:>12,.2f} kg/day                        ║
║    Consumption:                                                              ║
║      To Fuel Cell:           {o.H2_to_fuel_cell_kg_day:>12,.2f} kg/day                        ║
║      Dispensed:              {o.H2_dispensed_kg_day:>12,.2f} kg/day                        ║
║                                                                              ║
║  ═══ NATURAL GAS BALANCE ═══                                                 ║
║      To TriGen:              {o.NG_to_trigen_scf_day:>12,.0f} scf/day                       ║
║      To SMR:                 {o.NG_to_smr_scf_day:>12,.0f} scf/day                       ║
║      To CNG Station:         {o.NG_to_CNG_station_scf_day:>12,.0f} scf/day                       ║
║      Aux Heaters:            {o.NG_aux_heaters_scf_day:>12,.0f} scf/day                       ║
║      TOTAL NG:               {o.NG_total_scf_day:>12,.0f} scf/day                       ║
║                                                                              ║
║  ═══ ELECTRICITY BALANCE ═══                                                 ║
║    Generation:                                                               ║
║      TriGen:                 {o.elec_from_trigen:>12,.2f} kWh/day                       ║
║      Fuel Cell:              {o.elec_from_fuel_cell:>12,.2f} kWh/day                       ║
║      Battery Discharge:      {o.elec_from_bess:>12,.2f} kWh/day                       ║
║      Grid Import:            {o.elec_from_grid:>12,.2f} kWh/day                       ║
║    Consumption:                                                              ║
║      Electrolyzer:           {o.elec_to_electrolyzer:>12,.2f} kWh/day                       ║
║      H2 Station:             {o.elec_to_H2_station:>12,.2f} kWh/day                       ║
║      LH2 Pump/Vaporizer:     {o.elec_to_LH2_pump:>12,.2f} kWh/day                       ║
║      CNG Station:            {o.elec_to_CNG_station:>12,.2f} kWh/day                       ║
║      EV Charger:             {o.elec_to_EV_charger:>12,.2f} kWh/day                       ║
║      Battery Charge:         {o.elec_to_bess:>12,.2f} kWh/day                       ║
║                                                                              ║
║  ═══ HEAT BALANCE ═══                                                        ║
║      From TriGen:            {o.heat_from_trigen:>12,.2f} kWh_th/day                    ║
║      From Fuel Cell:         {o.heat_from_fuel_cell:>12,.2f} kWh_th/day                    ║
║      To SMR:                 {o.heat_to_smr:>12,.2f} kWh_th/day                    ║
║      To Vaporizer:           {o.heat_to_vaporizer:>12,.2f} kWh_th/day                    ║
║      Excess Heat:            {o.heat_excess:>12,.2f} kWh_th/day                    ║
║                                                                              ║
║  ═══ KEY PERFORMANCE INDICATORS ═══                                          ║
║      Total Energy Input:     {o.total_energy_input_kWh_day:>12,.2f} kWh/day                       ║
║      Total Energy Output:    {o.total_energy_output_kWh_day:>12,.2f} kWh/day                       ║
║      Levelized Energy Consumption (LEC): {o.levelized_energy_consumption:>8,.3f}                        ║
║      (LEC < 1.5 indicates good efficiency)                                   ║
║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  ISSUES IDENTIFIED:                                                          ║
{issues_str}
╚══════════════════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 80)
    print("INTEGRATED STATION TEST")
    print("=" * 80)
    
    # Test with TriGen + LH2 delivery scenario
    station = Station()
    
    outputs = station.calculate(
        trigen_NG_scf_h=10700,  # TriGen at 10,700 scf/h
        smr_H2_tpd=0,           # SMR disabled
        electrolyzer_H2_tpd=0,  # Electrolyzer disabled
        lh2_throughput_tpd=0.5, # LH2 delivery at 0.5 tpd
        gh2_trailer_kg_day=0,   # No GH2 trailer
        cng_trucks_per_day=25,  # 25 CNG trucks
        ev_demand_kWh_day=20000,# 20 MWh EV demand
        fuel_cell_enabled=False # No fuel cell
    )
    
    print(station.summary())
