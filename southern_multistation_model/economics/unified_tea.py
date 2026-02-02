"""
Unified Techno-Economic Analysis (TEA) Framework
=================================================

This module provides a comprehensive TEA framework compatible with the SHARE model
for green hydrogen and ammonia projects. It handles:

- Component-level CAPEX/OPEX for all major systems
- Multi-year cash flow with degradation, augmentation, and refurbishment
- LCOE, LCOH, LCOA calculations using NPV methodology
- IRR, NPV, payback period calculations
- CAPEX deployment scheduling across construction period

Systems covered:
1. Solar PV
2. Wind
3. BESS (Battery Energy Storage)
4. Electrolyzer (PEM/Alkaline)
5. H2 Storage & Compression
6. Haber-Bosch (NH3 synthesis)
7. NH3 Storage
8. Grid Connection
9. Water Supply
10. TriGen (MCFC - for comparison)

Based on SHARE_model_v1.py structure with enhancements.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Literal
from enum import Enum
import numpy as np
import math


# ============================================================
# ENUMS AND CONSTANTS
# ============================================================

class ElectrolyzerType(Enum):
    PEM = "PEM"
    ALKALINE = "Alkaline"
    SOEC = "SOEC"


class DepreciationMethod(Enum):
    STRAIGHT_LINE = "straight_line"
    MACRS_5 = "MACRS_5"
    MACRS_7 = "MACRS_7"


# MACRS Tables
MACRS_TABLES = {
    "MACRS_5": [0.2000, 0.3200, 0.1920, 0.1152, 0.1152, 0.0576],
    "MACRS_7": [0.1429, 0.2449, 0.1749, 0.1249, 0.0893, 0.0892, 0.0893, 0.0446],
}


# ============================================================
# INPUT DATACLASSES - Matching SHARE Model Structure
# ============================================================

@dataclass
class GeneralInputs:
    """General project parameters matching inputs.py"""
    
    # Project timeline
    project_lifetime: int = 30
    hours_per_year: int = 8760
    start_date: str = "01/01/2028"
    construction_start: str = "01/07/2025"
    
    # Financial
    discount_rate: float = 0.02
    inflation: float = 0.023
    project_overhead: float = 0.092
    taxes: float = 0.07
    depreciation_years: int = 20
    
    # CAPEX deployment schedule (year: fraction)
    # Year -2: 8%, Year -1: 60%, Year 0: 32%
    capex_deployment: Dict[int, float] = field(default_factory=lambda: {
        -2: 0.08,
        -1: 0.60,
        0: 0.32,
    })
    
    @property
    def construction_period_months(self) -> int:
        """Calculate construction period in months"""
        from datetime import datetime
        start_op = datetime.strptime(self.start_date, "%d/%m/%Y")
        const_start = datetime.strptime(self.construction_start, "%d/%m/%Y")
        return int((start_op - const_start).days / 30.437)


@dataclass
class SolarInputs:
    """Solar PV system inputs"""
    
    # Capacity
    capacity_MW_AC: float = 400
    DC_AC_ratio: float = 1.15
    
    @property
    def capacity_MW_DC(self) -> float:
        return self.capacity_MW_AC * self.DC_AC_ratio
    
    # Performance
    availability_factor: float = 0.99
    ac_losses: float = 0.01
    shading_losses: float = 0.0
    annual_degradation: float = 0.0055  # 0.55%/year
    
    # CAPEX ($/kWp DC)
    capex_per_kWp: float = 590
    
    # OPEX
    fixed_opex_per_kW_year: float = 27  # $/kW/yr
    variable_opex_per_kWh: float = 0.0004  # $/kWh
    
    # OPEX escalation (fraction of CAPEX)
    opex_frac_start: float = 0.020  # Years 1-2 (warranty)
    opex_frac_mid: float = 0.025   # Years 3-12
    opex_frac_end: float = 0.030   # Years 13+
    warranty_years: int = 2
    midlife_year: int = 12
    
    # Augmentation
    augmentation_cost_per_kWp: float = 550  # $/kWp
    
    # PPA (if applicable)
    ppa_price: float = 42.0  # $/MWh


@dataclass
class WindInputs:
    """Wind system inputs"""
    
    # Capacity
    capacity_MW: float = 400
    
    # Performance
    availability_factor: float = 1.0
    ac_losses: float = 0.01
    wake_losses: float = 0.0
    annual_degradation: float = 0.0055
    
    # Inter-annual variability
    iav_sigma: float = 0.08  # Lognormal sigma for annual variability
    seasonal_sigma: float = 0.05
    use_seasonal_iav: bool = True
    
    # CAPEX ($/kW)
    capex_per_kW: float = 1000
    
    # OPEX
    variable_opex_per_kWh: float = 0.00057
    
    # OPEX escalation (fraction of CAPEX)
    opex_frac_start: float = 0.025
    opex_frac_mid: float = 0.032
    opex_frac_end: float = 0.040
    warranty_years: int = 2
    midlife_year: int = 10
    
    # PPA
    ppa_price: float = 30.0  # $/MWh


@dataclass
class BESSInputs:
    """Battery Energy Storage System inputs"""
    
    # Capacity
    power_MW: float = 0
    duration_hours: float = 0
    
    @property
    def energy_MWh(self) -> float:
        return self.power_MW * self.duration_hours
    
    # Performance
    round_trip_efficiency: float = 0.85
    depth_of_discharge: float = 0.95
    availability: float = 0.99
    ac_losses: float = 0.02
    
    # Degradation
    min_capacity_fraction: float = 0.90  # Replace when below this
    calendar_degradation_per_year: float = 0.01
    cycle_life: int = 3000
    
    @property
    def cycle_degradation(self) -> float:
        return (1 - self.min_capacity_fraction) / self.cycle_life
    
    # CAPEX
    energy_capex_per_kWh: float = 200  # $/kWh
    power_capex_per_kW: float = 325    # $/kW
    
    # OPEX
    opex_per_kW_year: float = 11  # $/kW/yr
    
    # Augmentation
    augmentation_cost_fraction: float = 0.20  # Fraction of CAPEX
    augmentation_interval_years: int = 2


@dataclass
class ElectrolyzerInputs:
    """Electrolyzer system inputs - matching SHARE model"""
    
    # Type and capacity
    technology: ElectrolyzerType = ElectrolyzerType.ALKALINE
    capacity_MW: float = 400
    
    # Performance
    specific_energy_consumption: float = 52  # kWh/kg H2 (initial)
    availability: float = 0.875
    stack_lifetime_hours: float = 80000
    
    # Degradation
    annual_degradation: float = 0.01  # 1%/year efficiency loss
    min_SOH: float = 0.90  # Minimum state of health before replacement
    
    # Water consumption
    feed_water_m3_per_tH2: float = 8.82
    cooling_water_m3_per_tH2: float = 0.0
    waste_water_consumption_kWh_m3: float = 2.2
    waste_water_flowrate_m3_per_tH2: float = 42
    
    # CAPEX ($/kW)
    capex_per_kW: float = 663  # EPC cost
    
    # OPEX (fraction of CAPEX)
    opex_fraction: float = 0.04
    
    # Augmentation (stack replacement)
    stack_replacement_cost_fraction: float = 0.50  # 50% of CAPEX
    augmentation_interval_years: int = 2  # Check every N years
    
    @property
    def stack_life_years(self) -> float:
        """Approximate stack life in years at typical utilization"""
        # Assume ~8000 operating hours/year
        return self.stack_lifetime_hours / 8000


@dataclass
class H2StorageInputs:
    """Hydrogen storage inputs"""
    
    # Capacity
    capacity_tonnes: float = 22
    
    @property
    def capacity_MWh(self) -> float:
        """Convert to MWh using specific energy"""
        return self.capacity_tonnes * 1000 * 52 / 1000  # Assuming 52 kWh/kg
    
    # Performance
    SOC_min: float = 0.0
    SOC_max: float = 1.0
    initial_SOC: float = 0.0
    self_discharge_rate: float = 0.0  # Very low for compressed gas
    
    # CAPEX ($/kg capacity)
    capex_per_kg: float = 500
    
    # OPEX (fraction of CAPEX)
    opex_fraction: float = 0.03


@dataclass
class H2CompressionInputs:
    """Hydrogen compression inputs"""
    
    # Pressure range
    inlet_pressure_bar: float = 30
    outlet_pressure_bar: float = 60
    
    # Performance
    specific_energy_kWh_per_kg: float = 1.0  # Depends on pressure ratio
    
    # CAPEX
    capex_USD: float = 1500000  # Fixed cost
    
    # OPEX (fraction of CAPEX)
    opex_fraction: float = 0.02


@dataclass
class H2BOPInputs:
    """Hydrogen Balance of Plant inputs"""
    
    # CAPEX ($/kW of electrolyzer)
    capex_per_kW: float = 0
    
    # OPEX ($/MW/yr)
    opex_per_MW_year: float = 0


@dataclass
class HaberBoschInputs:
    """Haber-Bosch ammonia synthesis inputs"""
    
    # Capacity
    capacity_MW: float = 0  # Will be calculated from electrolyzer
    
    @property
    def capacity_tonnes_per_hour(self) -> float:
        return self.capacity_MW / self.energy_consumption_kWh_per_kg
    
    # Performance
    energy_consumption_kWh_per_kg: float = 0.7  # kWh/kg NH3
    H2_to_NH3_ratio: float = 0.182  # tonnes H2 per tonne NH3
    min_load_fraction: float = 0.30  # Minimum turndown
    availability: float = 0.96
    lifetime_hours: float = 80000
    
    # Ramp rates (fraction of capacity per hour)
    ramp_up_rate: float = 0.20
    ramp_down_rate: float = 0.20
    
    # Water consumption
    cooling_water_m3_per_tNH3: float = 5.88
    
    # CAPEX ($/tonne/yr capacity)
    capex_per_tonne_year: float = 925
    
    # OPEX (fraction of CAPEX)
    opex_fraction: float = 0.02
    
    # Augmentation
    augmentation_cost_fraction: float = 0.02  # of CAPEX


@dataclass
class NH3StorageInputs:
    """Ammonia storage inputs"""
    
    # Capacity
    capacity_m3: float = 0  # Will be calculated
    
    # Properties
    density_t_per_m3: float = 0.68
    
    @property
    def capacity_tonnes(self) -> float:
        return self.capacity_m3 * self.density_t_per_m3
    
    # CAPEX ($/m3)
    capex_per_m3: float = 1000
    
    # OPEX (fraction of CAPEX)
    opex_fraction: float = 0.03
    
    # Fixed OPEX
    fixed_opex_per_year: float = 8  # $/yr


@dataclass
class GridInputs:
    """Grid connection inputs"""
    
    # Capacity
    import_capacity_MW: float = 100
    export_capacity_MW: float = 100
    
    # Performance
    transmission_losses: float = 0.035
    
    # CAPEX
    capex_USD: float = 0  # Fixed grid connection cost
    
    # OPEX ($/yr)
    opex_per_year: float = 0
    
    # Tariffs
    import_tariff_per_MWh: float = 0  # $/MWh
    export_revenue_per_MWh: float = 50  # $/MWh
    
    # Indexation
    tariff_escalation: float = 0.01


@dataclass
class WaterInputs:
    """Water supply inputs"""
    
    # Unit costs
    cost_per_m3: float = 1.36  # $/m3
    cost_per_tH2: float = 12   # $/tonne H2 (derived)
    
    # Variable OPEX
    H2_water_variable_opex: float = 5.29  # $/year
    NH3_water_variable_opex: float = 3.53  # $/year


@dataclass
class RevenueInputs:
    """Revenue and product price inputs"""
    
    # Ammonia prices
    green_NH3_price: float = 900  # $/tonne
    grey_NH3_price: float = 500   # $/tonne
    
    # Hydrogen prices (if selling directly)
    green_H2_price: float = 6000  # $/tonne ($6/kg)
    grey_H2_price: float = 2000   # $/tonne


@dataclass
class TriGenInputs:
    """TriGen MCFC system inputs (for comparison)"""
    
    # Capacity
    rated_capacity_kW: float = 1400
    NG_input_scf_h: float = 10700
    
    # Performance
    electrical_efficiency: float = 0.50
    overall_efficiency: float = 0.80
    H2_coproduction_share: float = 0.223
    
    # H2 output
    LHV_H2_kWh_kg: float = 33.33
    
    # Availability
    availability: float = 0.92
    
    # Stack
    stack_life_years: float = 7
    stack_replacement_fraction: float = 0.40
    
    # CAPEX ($/kW)
    capex_per_kW: float = 5000
    
    # OPEX
    fixed_opex_per_kW_year: float = 50
    variable_opex_per_kWh: float = 0.005
    
    # Fuel
    ng_price_per_MMBtu: float = 4.00


# ============================================================
# SCALING FACTORS (Economies of Scale)
# ============================================================

SCALING_FACTORS = {
    'electrolyzer': 0.75,
    'HB_reactor': 0.70,
    'air_separation': 0.65,
    'compressors': 0.67,
    'heat_exchangers': 0.68,
    'power_electronics': 0.82,
    'pv_modules': 0.90,
    'balance_of_plant': 0.85,
}

REFERENCE_DATA = {
    'electrolyzer': {
        'ref_capacity_MW': 100,
        'ref_cost_M$': 66.3,
    },
    'HB_reactor': {
        'ref_capacity_tpd_NH3': 300,
        'ref_cost_M$': 50,
    },
}


# ============================================================
# MAIN TEA CLASS
# ============================================================

@dataclass
class TEAResults:
    """Complete TEA results"""
    
    # CAPEX breakdown
    capex_solar: float = 0
    capex_wind: float = 0
    capex_bess: float = 0
    capex_electrolyzer: float = 0
    capex_h2_bop: float = 0
    capex_h2_storage: float = 0
    capex_h2_compressor: float = 0
    capex_hb: float = 0
    capex_nh3_storage: float = 0
    capex_grid: float = 0
    capex_trigen: float = 0
    capex_total: float = 0
    
    # OPEX (Year 1)
    opex_solar: float = 0
    opex_wind: float = 0
    opex_bess: float = 0
    opex_electrolyzer: float = 0
    opex_h2_storage: float = 0
    opex_h2_compressor: float = 0
    opex_hb: float = 0
    opex_nh3_storage: float = 0
    opex_grid: float = 0
    opex_water: float = 0
    opex_trigen_fuel: float = 0
    opex_total: float = 0
    
    # Production (annual averages)
    annual_electricity_MWh: float = 0
    annual_H2_tonnes: float = 0
    annual_NH3_tonnes: float = 0
    
    # Capacity factors
    electrolyzer_CF: float = 0
    HB_CF: float = 0
    
    # Levelized costs
    LCOE: float = 0  # $/MWh
    LCOH: float = 0  # $/tonne H2
    LCOA: float = 0  # $/tonne NH3
    
    # Financial metrics
    NPV: float = 0
    IRR: float = 0
    payback_simple: float = 0
    payback_discounted: float = 0
    
    # Cash flows
    years: List[int] = field(default_factory=list)
    annual_revenues: List[float] = field(default_factory=list)
    annual_costs: List[float] = field(default_factory=list)
    annual_cashflows: List[float] = field(default_factory=list)
    cumulative_cashflows: List[float] = field(default_factory=list)


class UnifiedTEA:
    """
    Unified Techno-Economic Analysis matching SHARE model methodology.
    
    Features:
    - NPV-based LCOE, LCOH, LCOA calculations
    - Multi-year cash flow with degradation
    - Augmentation and refurbishment costs
    - CAPEX deployment scheduling
    - IRR and payback calculations
    """
    
    def __init__(self,
                 general: Optional[GeneralInputs] = None,
                 solar: Optional[SolarInputs] = None,
                 wind: Optional[WindInputs] = None,
                 bess: Optional[BESSInputs] = None,
                 electrolyzer: Optional[ElectrolyzerInputs] = None,
                 h2_storage: Optional[H2StorageInputs] = None,
                 h2_compression: Optional[H2CompressionInputs] = None,
                 h2_bop: Optional[H2BOPInputs] = None,
                 haber_bosch: Optional[HaberBoschInputs] = None,
                 nh3_storage: Optional[NH3StorageInputs] = None,
                 grid: Optional[GridInputs] = None,
                 water: Optional[WaterInputs] = None,
                 revenue: Optional[RevenueInputs] = None,
                 trigen: Optional[TriGenInputs] = None):
        
        self.general = general or GeneralInputs()
        self.solar = solar or SolarInputs()
        self.wind = wind or WindInputs()
        self.bess = bess or BESSInputs()
        self.electrolyzer = electrolyzer or ElectrolyzerInputs()
        self.h2_storage = h2_storage or H2StorageInputs()
        self.h2_compression = h2_compression or H2CompressionInputs()
        self.h2_bop = h2_bop or H2BOPInputs()
        self.haber_bosch = haber_bosch or HaberBoschInputs()
        self.nh3_storage = nh3_storage or NH3StorageInputs()
        self.grid = grid or GridInputs()
        self.water = water or WaterInputs()
        self.revenue = revenue or RevenueInputs()
        self.trigen = trigen  # Optional, for comparison
        
        self._results: Optional[TEAResults] = None
        self._issues: List[str] = []
    
    @property
    def results(self) -> TEAResults:
        if self._results is None:
            raise ValueError("TEA not calculated. Call calculate() first.")
        return self._results
    
    @property
    def issues(self) -> List[str]:
        return self._issues
    
    def calculate(self,
                  annual_solar_MWh: float = 0,
                  annual_wind_MWh: float = 0,
                  annual_H2_tonnes: float = 0,
                  annual_NH3_tonnes: float = 0,
                  annual_grid_import_MWh: float = 0,
                  annual_grid_export_MWh: float = 0,
                  annual_BESS_throughput_MWh: float = 0,
                  capacity_factor_H2: float = 0.5,
                  capacity_factor_NH3: float = 0.5,
                  include_trigen: bool = False) -> TEAResults:
        """
        Run complete TEA calculation.
        
        Args:
            annual_solar_MWh: Annual gross solar production
            annual_wind_MWh: Annual gross wind production
            annual_H2_tonnes: Annual H2 production (tonnes)
            annual_NH3_tonnes: Annual NH3 production (tonnes)
            annual_grid_import_MWh: Annual grid imports
            annual_grid_export_MWh: Annual grid exports
            annual_BESS_throughput_MWh: Annual BESS throughput
            capacity_factor_H2: Electrolyzer capacity factor
            capacity_factor_NH3: HB capacity factor
            include_trigen: Include TriGen in the analysis
        """
        self._issues = []
        out = TEAResults()
        g = self.general
        
        # ════════════════════════════════════════════════════════════
        # 1. CALCULATE CAPEX
        # ════════════════════════════════════════════════════════════
        
        # Solar CAPEX
        out.capex_solar = self.solar.capacity_MW_DC * 1000 * self.solar.capex_per_kWp
        
        # Wind CAPEX
        out.capex_wind = self.wind.capacity_MW * 1000 * self.wind.capex_per_kW
        
        # BESS CAPEX
        out.capex_bess = (
            self.bess.energy_MWh * 1000 * self.bess.energy_capex_per_kWh +
            self.bess.power_MW * 1000 * self.bess.power_capex_per_kW
        )
        
        # Electrolyzer CAPEX
        out.capex_electrolyzer = self.electrolyzer.capacity_MW * 1000 * self.electrolyzer.capex_per_kW
        
        # H2 BOP CAPEX
        out.capex_h2_bop = self.electrolyzer.capacity_MW * 1000 * self.h2_bop.capex_per_kW
        
        # H2 Storage CAPEX
        out.capex_h2_storage = self.h2_storage.capacity_tonnes * 1000 * self.h2_storage.capex_per_kg
        
        # H2 Compression CAPEX
        out.capex_h2_compressor = self.h2_compression.capex_USD
        
        # Haber-Bosch CAPEX
        # Calculate HB capacity from electrolyzer
        if self.haber_bosch.capacity_MW == 0:
            # Auto-calculate based on electrolyzer
            elec_H2_rate = self.electrolyzer.capacity_MW / (self.electrolyzer.specific_energy_consumption / 1000)  # kg/h
            hb_nh3_rate = elec_H2_rate / self.haber_bosch.H2_to_NH3_ratio  # kg NH3/h
            hb_capacity_tpy = hb_nh3_rate * 8760 / 1000  # tonnes/year
        else:
            hb_capacity_tpy = self.haber_bosch.capacity_MW / self.haber_bosch.energy_consumption_kWh_per_kg * 8760 / 1000
        
        out.capex_hb = hb_capacity_tpy * self.haber_bosch.capex_per_tonne_year
        
        # NH3 Storage CAPEX
        out.capex_nh3_storage = self.nh3_storage.capacity_m3 * self.nh3_storage.capex_per_m3
        
        # Grid CAPEX
        out.capex_grid = self.grid.capex_USD
        
        # TriGen CAPEX (if included)
        if include_trigen and self.trigen:
            out.capex_trigen = self.trigen.rated_capacity_kW * self.trigen.capex_per_kW
        
        # Total CAPEX
        out.capex_total = (
            out.capex_solar + out.capex_wind + out.capex_bess +
            out.capex_electrolyzer + out.capex_h2_bop + out.capex_h2_storage +
            out.capex_h2_compressor + out.capex_hb + out.capex_nh3_storage +
            out.capex_grid + out.capex_trigen
        )
        
        # ════════════════════════════════════════════════════════════
        # 2. CALCULATE ANNUAL OPEX (Year 1)
        # ════════════════════════════════════════════════════════════
        
        # Solar OPEX
        out.opex_solar = (
            self.solar.capacity_MW_AC * 1000 * self.solar.fixed_opex_per_kW_year +
            annual_solar_MWh * 1000 * self.solar.variable_opex_per_kWh
        )
        
        # Wind OPEX
        out.opex_wind = annual_wind_MWh * 1000 * self.wind.variable_opex_per_kWh
        
        # BESS OPEX
        out.opex_bess = self.bess.power_MW * 1000 * self.bess.opex_per_kW_year
        
        # Electrolyzer OPEX
        out.opex_electrolyzer = out.capex_electrolyzer * self.electrolyzer.opex_fraction
        
        # H2 Storage OPEX
        out.opex_h2_storage = out.capex_h2_storage * self.h2_storage.opex_fraction
        
        # H2 Compression OPEX
        out.opex_h2_compressor = out.capex_h2_compressor * self.h2_compression.opex_fraction
        
        # HB OPEX
        out.opex_hb = out.capex_hb * self.haber_bosch.opex_fraction
        
        # NH3 Storage OPEX
        out.opex_nh3_storage = (
            out.capex_nh3_storage * self.nh3_storage.opex_fraction +
            self.nh3_storage.fixed_opex_per_year
        )
        
        # Grid OPEX
        out.opex_grid = self.grid.opex_per_year
        
        # Water OPEX
        out.opex_water = (
            annual_H2_tonnes * self.water.cost_per_tH2 +
            annual_NH3_tonnes * self.haber_bosch.cooling_water_m3_per_tNH3 * self.water.cost_per_m3
        )
        
        # TriGen fuel OPEX (if included)
        if include_trigen and self.trigen:
            # Convert scf/h to MMBtu/year
            annual_ng_MMBtu = self.trigen.NG_input_scf_h * 8760 * self.trigen.availability / 1000
            out.opex_trigen_fuel = annual_ng_MMBtu * self.trigen.ng_price_per_MMBtu
        
        # Total OPEX
        out.opex_total = (
            out.opex_solar + out.opex_wind + out.opex_bess +
            out.opex_electrolyzer + out.opex_h2_storage + out.opex_h2_compressor +
            out.opex_hb + out.opex_nh3_storage + out.opex_grid +
            out.opex_water + out.opex_trigen_fuel
        )
        
        # ════════════════════════════════════════════════════════════
        # 3. MULTI-YEAR NPV CALCULATIONS (SHARE methodology)
        # ════════════════════════════════════════════════════════════
        
        years = list(range(-2, g.project_lifetime + 1))  # -2, -1, 0, 1, ..., 30
        out.years = years
        
        # Initialize arrays
        n_years = len(years)
        revenues = np.zeros(n_years)
        costs = np.zeros(n_years)
        cashflows = np.zeros(n_years)
        
        # NPV factors
        npv_factors = np.array([1 / (1 + g.discount_rate) ** y for y in years])
        
        # Production arrays (with degradation)
        H2_prod_by_year = np.zeros(n_years)
        NH3_prod_by_year = np.zeros(n_years)
        
        for i, year in enumerate(years):
            if year < 1:
                # Construction years - only CAPEX deployment
                capex_frac = g.capex_deployment.get(year, 0)
                cashflows[i] = -out.capex_total * capex_frac
                continue
            
            # Operating years (1 to project_lifetime)
            year_idx = year - 1  # 0-indexed
            
            # Degradation factors
            solar_degr = (1 - self.solar.annual_degradation) ** year_idx
            wind_degr = (1 - self.wind.annual_degradation) ** year_idx
            elec_degr = (1 - self.electrolyzer.annual_degradation) ** year_idx
            
            # Escalation factors
            inflation_factor = (1 + g.inflation) ** year_idx
            
            # Production with degradation
            year_solar = annual_solar_MWh * solar_degr
            year_wind = annual_wind_MWh * wind_degr
            year_H2 = annual_H2_tonnes * elec_degr
            year_NH3 = annual_NH3_tonnes * elec_degr
            
            H2_prod_by_year[i] = year_H2
            NH3_prod_by_year[i] = year_NH3
            
            # Revenue
            year_revenue = (
                year_NH3 * self.revenue.green_NH3_price +
                annual_grid_export_MWh * self.grid.export_revenue_per_MWh * inflation_factor
            )
            revenues[i] = year_revenue
            
            # Costs (OPEX + fuel + grid import)
            year_opex = out.opex_total * inflation_factor
            
            # PPA costs (if applicable)
            ppa_cost = (
                year_solar * self.solar.ppa_price +
                year_wind * self.wind.ppa_price
            )
            
            # Grid import cost
            grid_cost = annual_grid_import_MWh * self.grid.import_tariff_per_MWh * inflation_factor
            
            # Augmentation costs (check thresholds)
            aug_cost = 0
            
            # Electrolyzer stack replacement
            if year % self.electrolyzer.augmentation_interval_years == 0 and year > 1:
                # Check if SOH dropped below threshold
                cumulative_hours = year * 8760 * capacity_factor_H2
                if cumulative_hours >= self.electrolyzer.stack_lifetime_hours:
                    aug_cost += out.capex_electrolyzer * self.electrolyzer.stack_replacement_cost_fraction
            
            # HB refurbishment
            hb_aug_interval = int(g.project_lifetime * 8760 / self.haber_bosch.lifetime_hours)
            if hb_aug_interval > 0 and year % hb_aug_interval == 0 and year < g.project_lifetime:
                aug_cost += out.capex_hb * self.haber_bosch.augmentation_cost_fraction
            
            year_costs = year_opex + ppa_cost + grid_cost + aug_cost
            costs[i] = year_costs
            
            # Cash flow
            cashflows[i] = year_revenue - year_costs
        
        out.annual_revenues = revenues.tolist()
        out.annual_costs = costs.tolist()
        out.annual_cashflows = cashflows.tolist()
        out.cumulative_cashflows = np.cumsum(cashflows).tolist()
        
        # ════════════════════════════════════════════════════════════
        # 4. CALCULATE LCOE, LCOH, LCOA (NPV methodology)
        # ════════════════════════════════════════════════════════════
        
        # NPV of production
        H2_prod_NPV = sum(H2_prod_by_year[i] / (1 + g.discount_rate) ** years[i] 
                         for i in range(len(years)) if years[i] >= 1)
        NH3_prod_NPV = sum(NH3_prod_by_year[i] / (1 + g.discount_rate) ** years[i] 
                          for i in range(len(years)) if years[i] >= 1)
        
        # NPV of costs by category
        solar_cost_NPV = out.capex_solar + sum(
            out.opex_solar * (1 + g.inflation) ** (y-1) / (1 + g.discount_rate) ** y
            for y in range(1, g.project_lifetime + 1)
        )
        
        wind_cost_NPV = out.capex_wind + sum(
            out.opex_wind * (1 + g.inflation) ** (y-1) / (1 + g.discount_rate) ** y
            for y in range(1, g.project_lifetime + 1)
        )
        
        bess_cost_NPV = out.capex_bess + sum(
            out.opex_bess * (1 + g.inflation) ** (y-1) / (1 + g.discount_rate) ** y
            for y in range(1, g.project_lifetime + 1)
        )
        
        # Sum of costs for LCOE (RES + BESS + Grid)
        gross_energy_NPV = sum(
            (annual_solar_MWh + annual_wind_MWh) * (1 - self.solar.annual_degradation) ** (y-1) / 
            (1 + g.discount_rate) ** y
            for y in range(1, g.project_lifetime + 1)
        )
        
        direct_energy_NPV = gross_energy_NPV + sum(
            annual_BESS_throughput_MWh / (1 + g.discount_rate) ** y
            for y in range(1, g.project_lifetime + 1)
        )
        
        sum_cost_LCOE = solar_cost_NPV + wind_cost_NPV + bess_cost_NPV + out.capex_grid
        
        # LCOE
        if direct_energy_NPV > 0:
            out.LCOE = sum_cost_LCOE / direct_energy_NPV
        
        # Sum of costs for LCOH (LCOE costs + H2 production)
        h2_cost_NPV = (
            out.capex_electrolyzer + out.capex_h2_bop + out.capex_h2_storage +
            out.capex_h2_compressor +
            sum((out.opex_electrolyzer + out.opex_h2_storage + out.opex_h2_compressor + out.opex_water) *
                (1 + g.inflation) ** (y-1) / (1 + g.discount_rate) ** y
                for y in range(1, g.project_lifetime + 1))
        )
        
        sum_cost_LCOH = sum_cost_LCOE + h2_cost_NPV
        
        # LCOH
        if H2_prod_NPV > 0:
            out.LCOH = sum_cost_LCOH / H2_prod_NPV
        
        # Sum of costs for LCOA (LCOH costs + NH3 production)
        nh3_cost_NPV = (
            out.capex_hb + out.capex_nh3_storage +
            sum((out.opex_hb + out.opex_nh3_storage) *
                (1 + g.inflation) ** (y-1) / (1 + g.discount_rate) ** y
                for y in range(1, g.project_lifetime + 1))
        )
        
        sum_cost_LCOA = sum_cost_LCOH + nh3_cost_NPV
        
        # LCOA
        if NH3_prod_NPV > 0:
            out.LCOA = sum_cost_LCOA / NH3_prod_NPV
        
        # ════════════════════════════════════════════════════════════
        # 5. FINANCIAL METRICS
        # ════════════════════════════════════════════════════════════
        
        # NPV
        out.NPV = sum(cashflows[i] / (1 + g.discount_rate) ** years[i] for i in range(len(years)))
        
        # IRR
        out.IRR = self._calculate_irr(cashflows.tolist())
        
        # Payback
        out.payback_simple = self._calculate_simple_payback(out.cumulative_cashflows)
        out.payback_discounted = self._calculate_discounted_payback(cashflows.tolist(), years, g.discount_rate)
        
        # Production summary
        out.annual_electricity_MWh = annual_solar_MWh + annual_wind_MWh
        out.annual_H2_tonnes = annual_H2_tonnes
        out.annual_NH3_tonnes = annual_NH3_tonnes
        out.electrolyzer_CF = capacity_factor_H2
        out.HB_CF = capacity_factor_NH3
        
        self._results = out
        return out
    
    def _calculate_irr(self, cashflows: List[float], max_iter: int = 1000) -> float:
        """Calculate IRR using Newton-Raphson method."""
        try:
            import numpy_financial as npf
            return npf.irr(cashflows)
        except:
            # Fallback implementation
            irr = 0.10
            for _ in range(max_iter):
                npv = sum(cf / ((1 + irr) ** i) for i, cf in enumerate(cashflows))
                dnpv = sum(-i * cf / ((1 + irr) ** (i + 1)) for i, cf in enumerate(cashflows) if i > 0)
                
                if abs(dnpv) < 1e-10:
                    break
                
                new_irr = irr - npv / dnpv
                if abs(new_irr - irr) < 1e-7:
                    return new_irr
                
                irr = max(-0.99, min(10, new_irr))
            
            return irr
    
    def _calculate_simple_payback(self, cumulative: List[float]) -> float:
        """Calculate simple payback period."""
        for i, cf in enumerate(cumulative):
            if cf >= 0 and i > 0:
                prev = cumulative[i - 1]
                if cf != prev:
                    fraction = -prev / (cf - prev)
                    return (i - 1) + fraction
        return float('inf')
    
    def _calculate_discounted_payback(self, cashflows: List[float], years: List[int], 
                                      rate: float) -> float:
        """Calculate discounted payback period."""
        cumulative = 0
        for i, (cf, year) in enumerate(zip(cashflows, years)):
            pv = cf / ((1 + rate) ** year) if year >= 0 else cf
            prev_cum = cumulative
            cumulative += pv
            if cumulative >= 0 and i > 0 and year >= 1:
                if pv != 0:
                    fraction = -prev_cum / pv
                    return year - 1 + fraction
        return float('inf')
    
    def summary(self) -> str:
        """Generate formatted summary report."""
        if self._results is None:
            return "TEA not calculated. Call calculate() first."
        
        r = self._results
        g = self.general
        
        return f"""
╔══════════════════════════════════════════════════════════════════════════════════════════╗
║                         UNIFIED TEA SUMMARY (SHARE Model Compatible)                     ║
╠══════════════════════════════════════════════════════════════════════════════════════════╣

  PROJECT PARAMETERS
  ────────────────────────────────────────────────────────────────────────────────────────
    Lifetime:                      {g.project_lifetime:>12} years
    Discount Rate:                 {g.discount_rate:>12.1%}
    Inflation:                     {g.inflation:>12.1%}
    Depreciation:                  {g.depreciation_years:>12} years

  CAPEX BREAKDOWN
  ────────────────────────────────────────────────────────────────────────────────────────
    Solar PV:                      ${r.capex_solar:>14,.0f}
    Wind:                          ${r.capex_wind:>14,.0f}
    BESS:                          ${r.capex_bess:>14,.0f}
    Electrolyzer:                  ${r.capex_electrolyzer:>14,.0f}
    H2 BOP:                        ${r.capex_h2_bop:>14,.0f}
    H2 Storage:                    ${r.capex_h2_storage:>14,.0f}
    H2 Compressor:                 ${r.capex_h2_compressor:>14,.0f}
    Haber-Bosch:                   ${r.capex_hb:>14,.0f}
    NH3 Storage:                   ${r.capex_nh3_storage:>14,.0f}
    Grid:                          ${r.capex_grid:>14,.0f}
    TriGen:                        ${r.capex_trigen:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────────────
    TOTAL CAPEX:                   ${r.capex_total:>14,.0f}

  ANNUAL OPEX (Year 1)
  ────────────────────────────────────────────────────────────────────────────────────────
    Solar:                         ${r.opex_solar:>14,.0f}
    Wind:                          ${r.opex_wind:>14,.0f}
    BESS:                          ${r.opex_bess:>14,.0f}
    Electrolyzer:                  ${r.opex_electrolyzer:>14,.0f}
    H2 Storage:                    ${r.opex_h2_storage:>14,.0f}
    H2 Compressor:                 ${r.opex_h2_compressor:>14,.0f}
    Haber-Bosch:                   ${r.opex_hb:>14,.0f}
    NH3 Storage:                   ${r.opex_nh3_storage:>14,.0f}
    Grid:                          ${r.opex_grid:>14,.0f}
    Water:                         ${r.opex_water:>14,.0f}
    TriGen Fuel:                   ${r.opex_trigen_fuel:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────────────
    TOTAL OPEX:                    ${r.opex_total:>14,.0f} /yr

  PRODUCTION (Annual Average)
  ────────────────────────────────────────────────────────────────────────────────────────
    Electricity (RES):             {r.annual_electricity_MWh:>14,.0f} MWh/yr
    Hydrogen:                      {r.annual_H2_tonnes:>14,.0f} tonnes/yr
    Ammonia:                       {r.annual_NH3_tonnes:>14,.0f} tonnes/yr
    Electrolyzer CF:               {r.electrolyzer_CF:>14.1%}
    Haber-Bosch CF:                {r.HB_CF:>14.1%}

  LEVELIZED COSTS
  ────────────────────────────────────────────────────────────────────────────────────────
    LCOE:                          ${r.LCOE:>14.2f} /MWh
    LCOH:                          ${r.LCOH:>14.0f} /tonne H2
    LCOH (per kg):                 ${r.LCOH/1000:>14.2f} /kg H2
    LCOA:                          ${r.LCOA:>14.0f} /tonne NH3

  FINANCIAL METRICS
  ────────────────────────────────────────────────────────────────────────────────────────
    NPV:                           ${r.NPV:>14,.0f}
    IRR:                           {r.IRR:>14.1%}
    Simple Payback:                {r.payback_simple:>14.1f} years
    Discounted Payback:            {r.payback_discounted:>14.1f} years

╚══════════════════════════════════════════════════════════════════════════════════════════╝
"""


# ============================================================
# EXAMPLE USAGE
# ============================================================

if __name__ == "__main__":
    print("=" * 90)
    print("UNIFIED TEA - EXAMPLE (SHARE Model Compatible)")
    print("=" * 90)
    
    # Example: 400 MW electrolyzer green H2/NH3 project
    tea = UnifiedTEA(
        general=GeneralInputs(
            project_lifetime=30,
            discount_rate=0.02,
            inflation=0.023,
        ),
        solar=SolarInputs(
            capacity_MW_AC=400,
            capex_per_kWp=590,
            ppa_price=42.0,
        ),
        wind=WindInputs(
            capacity_MW=400,
            capex_per_kW=1000,
            ppa_price=30.0,
        ),
        electrolyzer=ElectrolyzerInputs(
            capacity_MW=400,
            specific_energy_consumption=52,
            capex_per_kW=663,
        ),
        h2_storage=H2StorageInputs(
            capacity_tonnes=22,
            capex_per_kg=500,
        ),
        haber_bosch=HaberBoschInputs(
            capacity_MW=43.6,  # ~80% of electrolyzer ratio
            capex_per_tonne_year=925,
        ),
        nh3_storage=NH3StorageInputs(
            capacity_m3=50000,
            capex_per_m3=1000,
        ),
        revenue=RevenueInputs(
            green_NH3_price=900,
        ),
    )
    
    # Run calculation with example production values
    results = tea.calculate(
        annual_solar_MWh=800000,
        annual_wind_MWh=1200000,
        annual_H2_tonnes=50000,
        annual_NH3_tonnes=275000,
        annual_grid_export_MWh=100000,
        capacity_factor_H2=0.65,
        capacity_factor_NH3=0.60,
    )
    
    print(tea.summary())
    
    # Print first 10 years of cash flows
    print("\nANNUAL CASH FLOWS (First 10 years):")
    print(f"{'Year':<6} {'Revenue':>15} {'Costs':>15} {'Cash Flow':>15} {'Cumulative':>15}")
    print("-" * 70)
    for i in range(min(13, len(results.years))):
        print(f"{results.years[i]:<6} ${results.annual_revenues[i]:>14,.0f} "
              f"${results.annual_costs[i]:>14,.0f} ${results.annual_cashflows[i]:>14,.0f} "
              f"${results.cumulative_cashflows[i]:>14,.0f}")
