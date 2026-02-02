"""
Configuration and default parameters for Station Model.
Values sourced from Calculator-Final_V1_4.xlsx
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TriGenConfig:
    """
    Configuration for TriGen MCFC System (SureSource 3000).
    
    All default values from Excel TriGen sheet.
    """
    # Model identification
    model: str = "SureSource 3000 (MCFC)"
    
    # Operating inputs
    NG_input_scf_per_h: float = 10700  # Natural gas input (max: 21,840 scf/h)
    
    # Efficiency parameters (LHV basis)
    electrical_eff_LHV: float = 0.50  # FCE spec
    overall_eff_LHV: float = 0.80  # FCE spec
    aux_load_fraction: float = 0.03  # Fraction consumed by auxiliaries
    
    # Fuel properties
    LHV_NG_BTU_per_scf: float = 1037  # Pipeline NG
    NG_CH4_mol_frac: float = 0.95  # Methane fraction
    LHV_H2_kWh_per_kg: float = 33.33  # H2 LHV
    
    # Co-production parameters
    H2_coprod_share: float = 0.223  # 0-0.3 typical for PSA/shift
    
    # Reformer parameters
    steam_to_carbon_ratio: float = 2.5  # mol/mol, anode S/C
    cathode_excess_air_lambda: float = 1.0  # Stoichiometric
    air_O2_mol_frac: float = 0.21  # Dry air O2 fraction
    
    # Molecular weights (kg/kmol)
    MW_air: float = 28.97
    MW_H2O: float = 18.015
    MW_CH4: float = 16.04
    MW_H2: float = 2.016
    
    # Plant specifications
    plant_rated_kW: float = 2800  # AC rating for SureSource 3000
    plant_turndown_fraction: float = 0.5  # tau
    
    # Operating temperatures (°C)
    T_amb: float = 25  # Ambient
    T_stack_in: float = 650  # Stack/exhaust hot-side
    
    # Operating pressures (bar)
    P_anode: float = 1.5
    P_cathode: float = 1.5
    
    # Specific heats (kJ/kg-K)
    Cp_NG: float = 2.2  # Natural gas (approx CH4)
    Cp_air: float = 1.0  # Air
    Cp_steam: float = 2.0  # Steam (superheated)
    Cp_water: float = 4.18  # Water
    
    # Heat exchanger efficiencies
    epsilon_NG: float = 0.80  # NG preheater
    epsilon_air: float = 0.75  # Air preheater
    epsilon_steam: float = 0.85  # Steam superheater
    epsilon_exhaust: float = 0.80  # Exhaust to vaporizer HX
    
    # Steam properties at 1.5 bar
    T_sat_1_5bar: float = 111.4  # °C, saturation temperature
    latent_heat_1_5bar: float = 2235  # kJ/kg
    
    # AC/DC conversion
    AC_DC_conversion_rate: float = 0.94


@dataclass
class SMRConfig:
    """
    Configuration for Steam Methane Reformer.
    
    All default values from Excel Electrolyzer_SMR sheet (SMR section).
    """
    # Design capacity
    design_H2_rate_tpd: float = 0.5  # ton/day H2 nominal rating
    
    # Stoichiometry
    kg_CH4_per_kg_H2: float = 3.0  # H2 energy/CH4 energy * 75% eff
    PSA_off_gas_fraction: float = 0.25  # PSA purge fraction
    NG_CH4_mol_frac: float = 0.95  # Methane fraction
    steam_to_carbon_SMR: float = 3.0  # Steam kmol per kmol CH4
    
    # Density
    rho_CH4_kg_per_scf: float = 0.02007  # ρCH4,scf
    
    # Temperatures (°C)
    T_amb: float = 25
    T_preheat_feed: float = 500
    T_sat_steam: float = 200  # Saturated steam assumption
    
    # Specific heats (kJ/kg-K)
    Cp_CH4: float = 2.2
    Cp_steam: float = 2.0
    
    # Energy requirements
    kWh_th_per_kg_steam_generation: float = 2.1  # Water->steam duty lumped
    reforming_kWh_per_kg_H2: float = 9.0  # Tube radiant duty
    recoverable_heat_fraction: float = 0.25  # Low/mid grade heat recovery
    
    # Molecular weights (kg/kmol)
    MW_CH4: float = 16.04
    MW_H2: float = 2.016
    MW_CO2: float = 44.01
    MW_H2O: float = 18.015
    
    # Fuel properties
    LHV_NG_BTU_per_scf: float = 915
    
    # Additional heater
    additional_heater_efficiency: float = 0.85


@dataclass
class ElectrolyzerConfig:
    """
    Configuration for PEM Electrolyzer.
    
    All default values from Excel Electrolyzer_SMR sheet (Electrolyzer section).
    """
    # Design capacity
    design_H2_rate_tpd: float = 0.5  # ton/day H2 nominal rating
    
    # Energy consumption
    PEM_kWh_DC_per_kg_H2_core: float = 52  # Stack + core BOP
    water_treatment_overhead_frac: float = 0.02  # Additional DC load for DI water
    
    # Water consumption
    water_kg_per_kg_H2: float = 10  # Deionized water incl. purge/makeup
    
    # Heat properties
    H2_HHV_kWh_per_kg: float = 39  # For heat rejection estimate
    
    # Waste heat recovery
    waste_heat_intensity_kWh_per_kg_H2: float = 14.04  # DC in minus H2 HHV
    recovery_fraction: float = 0.4
    
    # Conversions
    m3_to_gallon: float = 264.172
    
    # Buffer
    water_buffer_hours: float = 4


@dataclass
class H2StationConfig:
    """
    Configuration for H2 Station (Compressor + Chiller + Dispenser).
    
    All default values from Excel H2_Station sheet.
    """
    # Operating hours
    hours_per_day: float = 24
    
    # Losses
    H2_loss_fraction: float = 0.02  # 2-10% typical
    
    # Pressures (bar)
    P_suction_buffer: float = 20  # Buffer storage pressure
    P_suction_GH2_trailer: float = 250  # GH2 trailer pressure
    P_discharge: float = 900  # Compressor discharge
    
    # Compressor specific energy (kWh/kg)
    SEC_comp_20_to_900: float = 8.0  # From buffer (20 bar)
    SEC_comp_250_to_900: float = 1.5  # From GH2 trailer (250 bar)
    
    # Chiller/Dispenser
    SEC_precool_kWh_th_per_kg: float = 1.0  # Specific precooling load
    COP_chiller_dispenser: float = 2.5  # Chiller COP
    dispenser_aux_kW: float = 2  # Auxiliary power
    
    # Balance of plant
    BOP_power_kWh_day: float = 120


@dataclass
class LH2DeliveryConfig:
    """
    Configuration for LH2 Pump + Vaporizer pathway.
    
    All default values from Excel LH2_Pump_Vaporizer sheet.
    """
    # Throughput
    throughput_tpd: float = 0.5  # ton/day
    
    # Pressures (bar)
    P_suction: float = 3  # Storage tank/line pressure
    P_discharge: float = 700  # Delivery set-point
    
    # LH2 properties
    LH2_density_kg_m3: float = 70  # Near 20K
    latent_heat_MJ_per_kg: float = 0.45  # At 20K
    gas_Cp_avg_kJ_per_kgK: float = 14  # Avg from 20K to 300K
    
    # Temperatures (K)
    T_in_after_pump: float = 20  # LH2 temp pre-vaporizer
    T_out_delivery: float = 300  # Delivery gas temperature
    
    # Pump efficiency
    pump_efficiency: float = 0.65  # Isentropic-to-shaft
    
    # Auxiliary loads (kW)
    BOP_aux_loads_kW: float = 5  # Controls, valves, pumps
    precooler_electric_kW: float = 20  # Dispenser chiller
    
    # Losses
    loss_fraction: float = 0.02  # Boil-off/transfer losses
    
    # Additional heater
    additional_heater_efficiency: float = 0.85
    LHV_NG_BTU_per_scf: float = 1030


@dataclass
class FuelCellConfig:
    """
    Configuration for PEM Fuel Cell.
    
    All default values from Excel BESS_FC_DC sheet.
    """
    # Efficiency (LHV basis)
    eta_fc_el: float = 0.5  # Electrical efficiency
    eta_fc_th: float = 0.3  # Recoverable thermal efficiency
    
    # H2 properties
    LHV_H2_kWh_per_kg: float = 33.33
    
    # BOP
    fc_BOP_fraction: float = 0.02  # Parasitic fraction of FC output
    
    # Air parameters
    lambda_air: float = 2.0  # Stoichiometric ratio
    x_O2_air_mass: float = 0.232  # O2 mass fraction
    x_N2_air_mass: float = 0.768  # N2 mass fraction
    Cp_N2_kJ_per_kgK: float = 1.04
    
    # Temperatures (°C)
    T_fc_exhaust: float = 80
    T_amb: float = 25
    
    # Steam properties
    h_fg_steam_kJ_per_kg: float = 2257  # At 1 bar, 100°C


@dataclass
class BESSConfig:
    """
    Configuration for Battery Energy Storage System.
    
    All default values from Excel BESS_FC_DC sheet.
    """
    # Capacity
    capacity_nameplate_kWh: float = 0  # User input
    
    # State of charge limits
    SOC_min_frac: float = 0.2
    SOC_max_frac: float = 0.9
    
    # Efficiency
    roundtrip_efficiency: float = 0.9


@dataclass
class CNGStationConfig:
    """
    Configuration for CNG Station.
    
    All default values from Excel CNG_Station sheet.
    """
    # Demand
    trucks_per_day: int = 25
    fuel_per_truck_unit: str = "GGE"  # GGE or DGE
    fuel_per_truck_amount: float = 80
    
    # Peak parameters
    peak_window_trucks_fraction: float = 0.60  # Share during peak
    peak_window_hours: float = 4
    target_fill_time_minutes: float = 12
    active_hoses_during_peak: int = 2
    
    # Station parameters
    station_availability_target: float = 0.98
    NG_inlet_pressure_psig: float = 60
    LP_buffer_volume_scf: float = 5000
    utility_max_flow_scfh: float = 15000
    HP_storage_total_scf: float = 20000
    
    # Compressor energy (kWh per unit delivered)
    compressor_energy_per_GGE: float = 0.6
    compressor_energy_per_DGE: float = 0.68
    
    # Dryer
    dryer_type: str = "PSA"  # PSA or TSA
    dryer_purge_fraction: float = 0.02  # PSA: 2%, TSA: 0
    
    # Losses
    blowdown_vent_losses: float = 0.005
    fugitive_leaks: float = 0.002
    
    # Auxiliary loads
    aftercooler_fan_power_kW: float = 5
    aftercooler_run_hours_per_day: float = 16
    aux_controls_building_kWh_day: float = 10
    dispenser_active_kW: float = 1.5
    dispenser_idle_kW: float = 0.1
    dispenser_active_hours_per_hose: float = 2.5
    
    # Conversions
    scf_per_GGE: float = 114
    scf_per_DGE: float = 128.7
    NG_HHV_BTU_per_scf: float = 1000


@dataclass
class EVChargerConfig:
    """
    Configuration for DC Fast Charger.
    """
    # Demand
    daily_demand_kWh: float = 20000  # 20 MWh/day


@dataclass 
class StationConfig:
    """
    Master configuration combining all subsystems.
    """
    trigen: TriGenConfig = field(default_factory=TriGenConfig)
    smr: SMRConfig = field(default_factory=SMRConfig)
    electrolyzer: ElectrolyzerConfig = field(default_factory=ElectrolyzerConfig)
    h2_station: H2StationConfig = field(default_factory=H2StationConfig)
    lh2_delivery: LH2DeliveryConfig = field(default_factory=LH2DeliveryConfig)
    fuel_cell: FuelCellConfig = field(default_factory=FuelCellConfig)
    bess: BESSConfig = field(default_factory=BESSConfig)
    cng_station: CNGStationConfig = field(default_factory=CNGStationConfig)
    ev_charger: EVChargerConfig = field(default_factory=EVChargerConfig)
    
    # System-level settings
    H2_station_demand_kg_day: float = 1000  # Target H2 demand
    fuel_cell_enabled: bool = False  # Whether FC is included
