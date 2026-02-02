#!/usr/bin/env python3
"""
Station Model - Main Entry Point

This script demonstrates how to use the station model package.
Run this file to see example calculations matching the Excel model.

Usage:
    python main.py
    
Or import as a module:
    from station_model.core import Station
    from station_model.config import StationConfig
"""

from config import StationConfig, TriGenConfig
from core.integration import Station
from systems import (
    TriGen, SMR, Electrolyzer, LH2Delivery, 
    H2Station, FuelCell, BESS, CNGStation, EVCharger
)


def run_individual_system_tests():
    """Test each system module independently."""
    
    print("=" * 80)
    print("INDIVIDUAL SYSTEM VALIDATION")
    print("=" * 80)
    
    # TriGen
    print("\n--- TriGen (10,700 scf/h NG) ---")
    trigen = TriGen()
    trigen.calculate(NG_input_scf_h=10700)
    print(f"  H2: {trigen.outputs.H2_output_kg_day:.2f} kg/day")
    print(f"  Electricity: {trigen.outputs.electricity_kWh_day:.2f} kWh/day")
    print(f"  Heat for export: {trigen.outputs.Q_available_for_export_kW_day:.2f} kWh_th/day")
    
    # SMR
    print("\n--- SMR (0.5 tpd H2) ---")
    smr = SMR()
    smr.calculate(H2_rate_tpd=0.5, Q_from_trigen_kW=414)
    print(f"  H2: {smr.outputs.H2_output_kg_day:.2f} kg/day")
    print(f"  NG: {smr.outputs.NG_input_scf_day:.0f} scf/day")
    print(f"  CO2 intensity: {smr.outputs.CO2_intensity_kg_per_kgH2:.2f} kgCO2/kgH2")
    if smr.issues:
        print(f"  Issues: {smr.issues}")
    
    # Electrolyzer
    print("\n--- Electrolyzer (0.5 tpd H2) ---")
    elec = Electrolyzer()
    elec.calculate(H2_rate_tpd=0.5)
    print(f"  H2: {elec.outputs.H2_output_kg_day:.2f} kg/day")
    print(f"  Electricity: {elec.outputs.electricity_kWh_day:.2f} kWh/day")
    print(f"  Efficiency (HHV): {elec.outputs.efficiency_HHV:.1%}")
    
    # LH2 Delivery
    print("\n--- LH2 Delivery (0.5 tpd) ---")
    lh2 = LH2Delivery()
    lh2.calculate(throughput_tpd=0.5, Q_from_trigen_kW=414)
    print(f"  H2 output: {lh2.outputs.H2_output_kg_day:.2f} kg/day")
    print(f"  Pump power: {lh2.outputs.pump_power_kW:.2f} kW")
    print(f"  Vaporizer duty: {lh2.outputs.vaporizer_duty_kW:.2f} kW_th")
    if lh2.issues:
        print(f"  Issues: {lh2.issues}")
    
    # H2 Station
    print("\n--- H2 Station (522 kg/day from buffer) ---")
    h2s = H2Station()
    h2s.calculate(H2_from_buffer_kg_day=522, H2_station_demand_kg_day=1000)
    print(f"  Dispensed: {h2s.outputs.H2_dispensed_kg_day:.2f} kg/day")
    print(f"  Compressor energy: {h2s.outputs.compressor_power_total_kWh_day:.2f} kWh/day")
    print(f"  Total power: {h2s.outputs.total_power_kWh_day:.2f} kWh/day")
    
    # CNG Station
    print("\n--- CNG Station (25 trucks) ---")
    cng = CNGStation()
    cng.calculate(trucks_per_day=25)
    print(f"  NG consumption: {cng.outputs.total_NG_consumption_scf_day:.0f} scf/day")
    print(f"  Electricity: {cng.outputs.total_electricity_kWh_day:.2f} kWh/day")
    
    # Fuel Cell
    print("\n--- Fuel Cell (100 kg/day H2) ---")
    fc = FuelCell()
    fc.calculate(H2_input_kg_day=100)
    print(f"  Net electricity: {fc.outputs.electricity_net_kWh_day:.2f} kWh/day")
    print(f"  Heat output: {fc.outputs.heat_output_kWh_day:.2f} kWh_th/day")


def run_integrated_station_test():
    """Test the integrated station model."""
    
    print("\n" + "=" * 80)
    print("INTEGRATED STATION TEST")
    print("=" * 80)
    
    # Create station with default config
    station = Station()
    
    # Scenario: TriGen + LH2 delivery + CNG + EV
    outputs = station.calculate(
        trigen_NG_scf_h=10700,   # TriGen at design point
        smr_H2_tpd=0,            # SMR disabled
        electrolyzer_H2_tpd=0,   # Electrolyzer disabled
        lh2_throughput_tpd=0.5,  # 500 kg/day LH2 delivery
        gh2_trailer_kg_day=0,    # No GH2 trailer
        cng_trucks_per_day=25,   # 25 CNG trucks
        ev_demand_kWh_day=20000, # 20 MWh/day EV charging
        fuel_cell_enabled=False  # No fuel cell
    )
    
    print(station.summary())


def run_scenario_comparison():
    """Compare different station configurations."""
    
    print("\n" + "=" * 80)
    print("SCENARIO COMPARISON")
    print("=" * 80)
    
    scenarios = [
        {
            "name": "TriGen Only",
            "trigen_NG_scf_h": 10700,
            "smr_H2_tpd": 0,
            "electrolyzer_H2_tpd": 0,
            "lh2_throughput_tpd": 0,
            "gh2_trailer_kg_day": 500,
            "fuel_cell_enabled": False,
        },
        {
            "name": "TriGen + SMR",
            "trigen_NG_scf_h": 10700,
            "smr_H2_tpd": 0.5,
            "electrolyzer_H2_tpd": 0,
            "lh2_throughput_tpd": 0,
            "gh2_trailer_kg_day": 0,
            "fuel_cell_enabled": False,
        },
        {
            "name": "TriGen + Electrolyzer",
            "trigen_NG_scf_h": 10700,
            "smr_H2_tpd": 0,
            "electrolyzer_H2_tpd": 0.5,
            "lh2_throughput_tpd": 0,
            "gh2_trailer_kg_day": 0,
            "fuel_cell_enabled": False,
        },
        {
            "name": "TriGen + LH2 Delivery",
            "trigen_NG_scf_h": 10700,
            "smr_H2_tpd": 0,
            "electrolyzer_H2_tpd": 0,
            "lh2_throughput_tpd": 0.5,
            "gh2_trailer_kg_day": 0,
            "fuel_cell_enabled": False,
        },
    ]
    
    print(f"\n{'Scenario':<25} {'H2 Disp':<12} {'NG Total':<15} {'Grid Import':<15} {'LEC':<10}")
    print("-" * 80)
    
    for scenario in scenarios:
        name = scenario.pop("name")
        station = Station()
        outputs = station.calculate(
            cng_trucks_per_day=25,
            ev_demand_kWh_day=20000,
            **scenario
        )
        
        print(f"{name:<25} {outputs.H2_dispensed_kg_day:<12,.0f} {outputs.NG_total_scf_day:<15,.0f} "
              f"{outputs.elec_from_grid:<15,.0f} {outputs.levelized_energy_consumption:<10.3f}")


if __name__ == "__main__":
    # Run all tests
    run_individual_system_tests()
    run_integrated_station_test()
    run_scenario_comparison()
    
    print("\n" + "=" * 80)
    print("All tests completed!")
    print("=" * 80)
