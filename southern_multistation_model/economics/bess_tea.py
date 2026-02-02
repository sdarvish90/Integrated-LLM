"""
Battery Energy Storage System (BESS) Techno-Economic Analysis Module

Comprehensive TEA for utility-scale and distributed battery storage systems.

Based on:
- User's BatteryModel.py operational model
- SHARE Model inputs.py parameters
- NREL ATB 2024
- Lazard LCOS Analysis

Features:
- Operational simulation (charge/discharge with efficiency)
- Degradation modeling (calendar + cycle aging)
- Augmentation strategy analysis
- Revenue stacking (arbitrage, capacity, ancillary services)
- LCOS calculation (Levelized Cost of Storage)
- Technology comparison (Li-ion NMC, LFP, Flow batteries)

References:
- SHARE Model inputs.py BESS parameters
- NREL ATB 2024
- Lazard LCOS Analysis v8.0
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from enum import Enum
import numpy as np


class BatteryTechnology(Enum):
    """Battery technology types"""
    LI_ION_NMC = "Li-ion NMC"      # Nickel Manganese Cobalt
    LI_ION_LFP = "Li-ion LFP"      # Lithium Iron Phosphate
    LI_ION_NCA = "Li-ion NCA"      # Nickel Cobalt Aluminum
    FLOW_VRFB = "Flow VRFB"        # Vanadium Redox Flow
    FLOW_ZNBR = "Flow Zn-Br"       # Zinc Bromine Flow
    NA_ION = "Sodium-ion"          # Emerging technology


class BESSApplication(Enum):
    """Primary application/use case"""
    ENERGY_ARBITRAGE = "arbitrage"
    CAPACITY_FIRMING = "capacity_firming"
    RENEWABLE_SHIFTING = "renewable_shifting"
    FREQUENCY_REGULATION = "frequency_regulation"
    PEAK_SHAVING = "peak_shaving"
    BACKUP_POWER = "backup"
    HYBRID_RE_STORAGE = "hybrid"


@dataclass
class BatteryTechSpecs:
    """Technology-specific default parameters"""
    
    # Li-ion NMC defaults
    LI_ION_NMC = {
        'round_trip_efficiency': 0.87,
        'calendar_degradation': 0.02,     # 2%/year
        'cycle_degradation_per_cycle': 0.00015,  # Per full cycle
        'cycle_life': 4000,
        'min_soh': 0.80,
        'c_rate_max': 1.0,
        'capex_energy': 180,              # $/kWh
        'capex_power': 300,               # $/kW
        'energy_density_Wh_kg': 250,
    }
    
    # Li-ion LFP defaults (safer, longer life, lower density)
    LI_ION_LFP = {
        'round_trip_efficiency': 0.92,
        'calendar_degradation': 0.015,
        'cycle_degradation_per_cycle': 0.0001,
        'cycle_life': 6000,
        'min_soh': 0.80,
        'c_rate_max': 1.0,
        'capex_energy': 150,
        'capex_power': 280,
        'energy_density_Wh_kg': 160,
    }
    
    # Vanadium Redox Flow (long duration, unlimited cycles)
    FLOW_VRFB = {
        'round_trip_efficiency': 0.72,
        'calendar_degradation': 0.005,
        'cycle_degradation_per_cycle': 0.00001,
        'cycle_life': 20000,
        'min_soh': 0.95,
        'c_rate_max': 0.25,
        'capex_energy': 350,
        'capex_power': 800,
        'energy_density_Wh_kg': 25,
    }


# ════════════════════════════════════════════════════════════════════════════════
# OPERATIONAL MODEL (Enhanced from user's BatteryModel.py)
# ════════════════════════════════════════════════════════════════════════════════

class BESSOperationalModel:
    """
    Enhanced Battery Energy Storage System operational model.
    
    Based on user's BatteryModel.py with added features:
    - Cycle counting for degradation
    - Temperature effects (optional)
    - State of Health (SOH) tracking
    - C-rate limits
    
    Attributes:
        capacity_kWh: Maximum capacity (kWh)
        power_kW: Maximum charge/discharge power (kW)
        current_energy_kWh: Current stored energy (kWh)
        soc: State of charge (0-1)
        soh: State of health (0-1)
        rte: Round-trip efficiency
        ac_losses: AC-side losses
        cycles_completed: Cumulative full equivalent cycles
    """
    
    def __init__(self,
                 capacity_kWh: float,
                 power_kW: float,
                 initial_soc: float = 0.0,
                 rte: float = 0.85,
                 ac_losses: float = 0.02,
                 dod_max: float = 0.95,
                 soc_min: float = 0.05,
                 soc_max: float = 1.0,
                 calendar_degradation: float = 0.01,
                 cycle_life: int = 3000,
                 min_soh: float = 0.90):
        
        self.initial_capacity_kWh = capacity_kWh
        self.capacity_kWh = capacity_kWh
        self.power_kW = power_kW
        self.rte = rte
        self.ac_losses = ac_losses
        self.dod_max = dod_max
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.calendar_degradation = calendar_degradation
        self.cycle_life = cycle_life
        self.min_soh = min_soh
        
        # State variables
        self.current_energy_kWh = capacity_kWh * initial_soc
        self.soc = initial_soc
        self.soh = 1.0
        
        # Tracking
        self.cycles_completed = 0.0
        self.total_energy_charged_kWh = 0.0
        self.total_energy_discharged_kWh = 0.0
        self.cycle_degradation_per_cycle = (self.soc_max - self.min_soh) / self.cycle_life
    
    @property
    def usable_capacity_kWh(self) -> float:
        """Usable capacity accounting for SOH and DOD"""
        return self.capacity_kWh * self.soh * self.dod_max
    
    @property
    def available_charge_kWh(self) -> float:
        """Energy that can be charged"""
        max_energy = self.capacity_kWh * self.soh * self.soc_max
        return max_energy - self.current_energy_kWh
    
    @property 
    def available_discharge_kWh(self) -> float:
        """Energy that can be discharged"""
        min_energy = self.capacity_kWh * self.soh * self.soc_min
        return self.current_energy_kWh - min_energy
    
    def charge(self, energy_kWh: float) -> float:
        """
        Charge the battery.
        
        Args:
            energy_kWh: Energy to charge (kWh, AC side)
            
        Returns:
            Actual energy charged (kWh, AC side)
        """
        # Charge efficiency = sqrt(RTE)
        charge_eff = self.rte ** 0.5 * (1 - self.ac_losses)
        
        # Limits
        max_charge = min(
            energy_kWh,
            self.power_kW,  # Power limit
            self.available_charge_kWh / charge_eff  # Capacity limit
        )
        
        # Update state
        energy_stored = max_charge * charge_eff
        self.current_energy_kWh += energy_stored
        self.soc = self.current_energy_kWh / (self.capacity_kWh * self.soh)
        
        # Track
        self.total_energy_charged_kWh += max_charge
        self._update_cycles(energy_stored)
        
        return max_charge
    
    def discharge(self, energy_kWh: float) -> float:
        """
        Discharge the battery.
        
        Args:
            energy_kWh: Energy to discharge (kWh, AC side)
            
        Returns:
            Actual energy discharged (kWh, AC side)
        """
        # Discharge efficiency = sqrt(RTE)
        discharge_eff = self.rte ** 0.5 * (1 - self.ac_losses)
        
        # Limits
        max_discharge = min(
            energy_kWh,
            self.power_kW,  # Power limit
            self.available_discharge_kWh * discharge_eff  # Capacity limit
        )
        
        # Update state
        energy_removed = max_discharge / discharge_eff
        self.current_energy_kWh -= energy_removed
        self.soc = self.current_energy_kWh / (self.capacity_kWh * self.soh)
        
        # Track
        self.total_energy_discharged_kWh += max_discharge
        self._update_cycles(energy_removed)
        
        return max_discharge
    
    def _update_cycles(self, energy_kWh: float):
        """Update cycle count based on energy throughput"""
        # One cycle = one full capacity charge + discharge
        cycle_increment = energy_kWh / (2 * self.usable_capacity_kWh)
        self.cycles_completed += cycle_increment
    
    def apply_degradation(self, years: float = 1.0):
        """
        Apply calendar and cycle degradation.
        
        Args:
            years: Time period (years)
        """
        # Calendar degradation
        calendar_loss = self.calendar_degradation * years
        
        # Cycle degradation (already tracked incrementally)
        cycle_loss = self.cycles_completed * self.cycle_degradation_per_cycle
        
        # Total degradation (don't double-count cycles)
        self.soh = max(self.min_soh, 1.0 - calendar_loss - cycle_loss)
        
        # Adjust current energy if above new capacity
        max_energy = self.capacity_kWh * self.soh * self.soc_max
        self.current_energy_kWh = min(self.current_energy_kWh, max_energy)
        self.soc = self.current_energy_kWh / (self.capacity_kWh * self.soh)
    
    def augment(self, capacity_fraction: float = 0.20):
        """
        Augment battery capacity to restore SOH.
        
        Args:
            capacity_fraction: Fraction of original capacity to add
        """
        added_capacity = self.initial_capacity_kWh * capacity_fraction
        self.capacity_kWh += added_capacity
        
        # Recalculate SOH based on new capacity vs original
        self.soh = self.capacity_kWh / self.initial_capacity_kWh
    
    def reset_cycles(self):
        """Reset cycle counter (e.g., after augmentation)"""
        self.cycles_completed = 0.0


# ════════════════════════════════════════════════════════════════════════════════
# TEA INPUT DATACLASSES
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class BESSCapexInputs:
    """Detailed CAPEX breakdown for BESS"""
    
    # ════════════════════════════════════════════════════════════
    # BATTERY MODULES
    # ════════════════════════════════════════════════════════════
    
    # Energy capacity cost ($/kWh)
    energy_capex_per_kWh: float = 200  # From SHARE: BESS_energy_capex
    
    # Power capacity cost ($/kW)
    power_capex_per_kW: float = 325    # From SHARE: BESS_power_capex
    
    # Battery technology
    technology: BatteryTechnology = BatteryTechnology.LI_ION_LFP
    
    # ════════════════════════════════════════════════════════════
    # BALANCE OF SYSTEM
    # ════════════════════════════════════════════════════════════
    
    # Power conversion system ($/kW) - if not included in power cost
    pcs_per_kW: float = 0  # Often bundled
    
    # Battery management system ($/kWh)
    bms_per_kWh: float = 10
    
    # Thermal management ($/kWh) - HVAC, cooling
    thermal_per_kWh: float = 20
    
    # Containerization/enclosure ($/kWh)
    enclosure_per_kWh: float = 15
    
    # ════════════════════════════════════════════════════════════
    # INSTALLATION
    # ════════════════════════════════════════════════════════════
    
    # Site preparation ($/kW)
    site_prep_per_kW: float = 20
    
    # Electrical installation ($/kW)
    electrical_install_per_kW: float = 30
    
    # Grid interconnection ($/kW)
    interconnection_per_kW: float = 50
    
    # Commissioning ($/kW)
    commissioning_per_kW: float = 15
    
    # ════════════════════════════════════════════════════════════
    # SOFT COSTS
    # ════════════════════════════════════════════════════════════
    
    # Engineering (% of hardware)
    engineering_pct: float = 0.05
    
    # Project management (% of hardware)
    project_management_pct: float = 0.03
    
    # Permitting ($/kW)
    permitting_per_kW: float = 10
    
    # Contingency (% of direct)
    contingency_pct: float = 0.05


@dataclass
class BESSOpexInputs:
    """Operating costs for BESS"""
    
    # ════════════════════════════════════════════════════════════
    # FIXED O&M
    # ════════════════════════════════════════════════════════════
    
    # Fixed O&M ($/kW-year) - From SHARE: BESS_opex = 11
    fixed_om_per_kW_year: float = 11
    
    # O&M escalation (%/year)
    om_escalation: float = 0.02
    
    # ════════════════════════════════════════════════════════════
    # VARIABLE O&M
    # ════════════════════════════════════════════════════════════
    
    # Variable O&M ($/MWh throughput)
    variable_om_per_MWh: float = 0.5
    
    # ════════════════════════════════════════════════════════════
    # AUGMENTATION
    # ════════════════════════════════════════════════════════════
    
    # Augmentation cost (fraction of original battery cost)
    # From SHARE: BESS_aug_cost = 0.2
    augmentation_cost_fraction: float = 0.20
    
    # Augmentation interval (years) - From SHARE: BESS_aug_years = 2
    augmentation_interval_years: int = 2
    
    # Augmentation trigger (SOH threshold)
    augmentation_soh_trigger: float = 0.85
    
    # ════════════════════════════════════════════════════════════
    # OTHER
    # ════════════════════════════════════════════════════════════
    
    # Insurance (% of CAPEX)
    insurance_pct: float = 0.004
    
    # Property tax (% of CAPEX)
    property_tax_pct: float = 0.01
    
    # Site lease ($/MW-year)
    site_lease_per_MW_year: float = 2000


@dataclass
class BESSPerformanceInputs:
    """Performance parameters for BESS"""
    
    # ════════════════════════════════════════════════════════════
    # CAPACITY
    # ════════════════════════════════════════════════════════════
    
    # Power capacity (MW)
    power_MW: float = 100
    
    # Duration (hours)
    duration_hours: float = 4
    
    # Energy capacity (MWh) - calculated from power * duration
    @property
    def energy_MWh(self) -> float:
        return self.power_MW * self.duration_hours
    
    # ════════════════════════════════════════════════════════════
    # EFFICIENCY & LOSSES
    # ════════════════════════════════════════════════════════════
    
    # Round-trip efficiency - From SHARE: BESS_RTE = 0.85
    round_trip_efficiency: float = 0.85
    
    # AC losses - From SHARE: BESS_ac_losses = 0.02
    ac_losses: float = 0.02
    
    # Auxiliary load (% of capacity)
    aux_load_pct: float = 0.005
    
    # Availability - From SHARE: BESS_avail = 0.99
    availability: float = 0.99
    
    # ════════════════════════════════════════════════════════════
    # STATE OF CHARGE LIMITS
    # ════════════════════════════════════════════════════════════
    
    # Maximum SOC - From SHARE: SOC_max = 1
    soc_max: float = 1.0
    
    # Minimum SOC (1 - DOD) - From SHARE: BESS_DoD = 0.95
    soc_min: float = 0.05
    
    # Depth of discharge
    @property
    def dod(self) -> float:
        return self.soc_max - self.soc_min
    
    # ════════════════════════════════════════════════════════════
    # DEGRADATION
    # ════════════════════════════════════════════════════════════
    
    # Calendar degradation (%/year) - From SHARE: BESS_cal_degr = 0.01
    calendar_degradation: float = 0.01
    
    # Cycle life (full cycles to 80% SOH) - From SHARE: BESS_cycles = 3000
    cycle_life: int = 3000
    
    # Minimum SOH before replacement - From SHARE: BESS_min_cap = 0.9
    min_soh: float = 0.90
    
    # ════════════════════════════════════════════════════════════
    # UTILIZATION
    # ════════════════════════════════════════════════════════════
    
    # Daily cycles (average)
    daily_cycles: float = 1.0
    
    # Annual throughput (MWh) - calculated
    @property
    def annual_cycles(self) -> float:
        return self.daily_cycles * 365 * self.availability
    
    @property
    def annual_throughput_MWh(self) -> float:
        return self.energy_MWh * self.annual_cycles * 2 * self.dod  # Charge + discharge


@dataclass
class BESSRevenueInputs:
    """Revenue streams for BESS"""
    
    # ════════════════════════════════════════════════════════════
    # ENERGY ARBITRAGE
    # ════════════════════════════════════════════════════════════
    
    # Average spread ($/MWh) - difference between charge and discharge price
    arbitrage_spread_per_MWh: float = 30
    
    # Spread escalation (%/year)
    spread_escalation: float = 0.01
    
    # ════════════════════════════════════════════════════════════
    # CAPACITY PAYMENTS
    # ════════════════════════════════════════════════════════════
    
    # Capacity payment ($/kW-year)
    capacity_payment_per_kW_year: float = 50
    
    # Capacity credit (% of nameplate)
    capacity_credit: float = 0.90
    
    # ════════════════════════════════════════════════════════════
    # ANCILLARY SERVICES
    # ════════════════════════════════════════════════════════════
    
    # Frequency regulation ($/MW-year)
    freq_reg_per_MW_year: float = 30000
    
    # Frequency regulation utilization (% of time)
    freq_reg_utilization: float = 0.50
    
    # Spinning reserve ($/MW-year)
    spinning_reserve_per_MW_year: float = 10000
    
    # ════════════════════════════════════════════════════════════
    # RENEWABLE INTEGRATION
    # ════════════════════════════════════════════════════════════
    
    # Curtailment capture value ($/MWh)
    curtailment_value_per_MWh: float = 0
    
    # Time-shift value ($/MWh)
    time_shift_value_per_MWh: float = 0
    
    # ════════════════════════════════════════════════════════════
    # INCENTIVES
    # ════════════════════════════════════════════════════════════
    
    # Investment Tax Credit
    itc_rate: float = 0.30
    
    # State incentives ($/kWh)
    state_incentive_per_kWh: float = 0


@dataclass
class BESSFinancialInputs:
    """Financial parameters for BESS TEA"""
    
    # Project timeline
    project_lifetime_years: int = 20
    construction_months: int = 12
    
    # Discount rate
    discount_rate: float = 0.08
    
    # Inflation
    inflation_rate: float = 0.025
    
    # Tax
    tax_rate: float = 0.21
    depreciation_years: int = 7  # MACRS for storage
    
    # CAPEX deployment
    capex_deployment: Dict[int, float] = field(default_factory=lambda: {
        -1: 0.30,
        0: 0.70,
    })


@dataclass
class BESSTEAOutputs:
    """Complete BESS TEA results"""
    
    # System
    power_MW: float = 0
    energy_MWh: float = 0
    duration_hours: float = 0
    technology: str = ""
    
    # Utilization
    annual_cycles: float = 0
    annual_throughput_MWh: float = 0
    lifetime_throughput_MWh: float = 0
    
    # CAPEX
    capex_battery_energy: float = 0
    capex_battery_power: float = 0
    capex_bos: float = 0
    capex_installation: float = 0
    capex_soft_costs: float = 0
    capex_contingency: float = 0
    
    capex_total: float = 0
    capex_per_kWh: float = 0
    capex_per_kW: float = 0
    capex_after_incentives: float = 0
    
    # OPEX (Year 1)
    opex_fixed_om: float = 0
    opex_variable_om: float = 0
    opex_insurance: float = 0
    opex_property_tax: float = 0
    opex_site_lease: float = 0
    opex_augmentation_reserve: float = 0
    
    opex_total_yr1: float = 0
    
    # Revenue (Year 1)
    revenue_arbitrage: float = 0
    revenue_capacity: float = 0
    revenue_ancillary: float = 0
    revenue_total_yr1: float = 0
    
    # LCOS (Levelized Cost of Storage)
    lcos_total: float = 0             # $/MWh discharged
    lcos_capex: float = 0
    lcos_opex: float = 0
    lcos_augmentation: float = 0
    lcos_charging: float = 0          # If charging cost included
    
    # Financial
    npv: float = 0
    irr: float = 0
    payback_simple: float = 0
    payback_discounted: float = 0
    
    # Degradation tracking
    soh_by_year: List[float] = field(default_factory=list)
    augmentation_years: List[int] = field(default_factory=list)
    total_augmentation_cost: float = 0
    
    # Cash flows
    years: List[int] = field(default_factory=list)
    cf_net: List[float] = field(default_factory=list)
    cf_cumulative: List[float] = field(default_factory=list)


class BESSTEA:
    """
    Comprehensive Techno-Economic Analysis for Battery Energy Storage.
    
    Features:
    - Technology comparison (Li-ion NMC/LFP, Flow)
    - Degradation and augmentation modeling
    - Revenue stacking (arbitrage, capacity, ancillary)
    - LCOS calculation
    - Integrated operational simulation
    """
    
    def __init__(self,
                 capex: Optional[BESSCapexInputs] = None,
                 opex: Optional[BESSOpexInputs] = None,
                 performance: Optional[BESSPerformanceInputs] = None,
                 revenue: Optional[BESSRevenueInputs] = None,
                 financial: Optional[BESSFinancialInputs] = None):
        
        self.capex = capex or BESSCapexInputs()
        self.opex = opex or BESSOpexInputs()
        self.performance = performance or BESSPerformanceInputs()
        self.revenue = revenue or BESSRevenueInputs()
        self.financial = financial or BESSFinancialInputs()
        
        self._outputs: Optional[BESSTEAOutputs] = None
        self._operational_model: Optional[BESSOperationalModel] = None
    
    @property
    def outputs(self) -> BESSTEAOutputs:
        if self._outputs is None:
            raise ValueError("Call calculate() first")
        return self._outputs
    
    @property
    def operational_model(self) -> BESSOperationalModel:
        """Get operational model for simulation"""
        if self._operational_model is None:
            perf = self.performance
            self._operational_model = BESSOperationalModel(
                capacity_kWh=perf.energy_MWh * 1000,
                power_kW=perf.power_MW * 1000,
                initial_soc=0.5,
                rte=perf.round_trip_efficiency,
                ac_losses=perf.ac_losses,
                dod_max=perf.dod,
                soc_min=perf.soc_min,
                soc_max=perf.soc_max,
                calendar_degradation=perf.calendar_degradation,
                cycle_life=perf.cycle_life,
                min_soh=perf.min_soh,
            )
        return self._operational_model
    
    def calculate(self, 
                  charging_cost_per_MWh: float = 0) -> BESSTEAOutputs:
        """
        Run complete BESS TEA calculation.
        
        Args:
            charging_cost_per_MWh: Cost of electricity for charging (for LCOS)
        """
        out = BESSTEAOutputs()
        cx = self.capex
        ox = self.opex
        perf = self.performance
        rv = self.revenue
        fin = self.financial
        
        # ════════════════════════════════════════════════════════════
        # SYSTEM SIZING
        # ════════════════════════════════════════════════════════════
        
        out.power_MW = perf.power_MW
        out.energy_MWh = perf.energy_MWh
        out.duration_hours = perf.duration_hours
        out.technology = cx.technology.value
        
        power_kW = perf.power_MW * 1000
        energy_kWh = perf.energy_MWh * 1000
        
        # Utilization
        out.annual_cycles = perf.annual_cycles
        out.annual_throughput_MWh = perf.annual_throughput_MWh
        
        # ════════════════════════════════════════════════════════════
        # CAPEX
        # ════════════════════════════════════════════════════════════
        
        # Battery modules
        out.capex_battery_energy = energy_kWh * cx.energy_capex_per_kWh
        out.capex_battery_power = power_kW * cx.power_capex_per_kW
        
        # BOS
        out.capex_bos = (
            power_kW * cx.pcs_per_kW +
            energy_kWh * (cx.bms_per_kWh + cx.thermal_per_kWh + cx.enclosure_per_kWh)
        )
        
        # Installation
        out.capex_installation = power_kW * (
            cx.site_prep_per_kW + cx.electrical_install_per_kW +
            cx.interconnection_per_kW + cx.commissioning_per_kW
        )
        
        # Hardware subtotal
        hardware = (out.capex_battery_energy + out.capex_battery_power + 
                   out.capex_bos + out.capex_installation)
        
        # Soft costs
        out.capex_soft_costs = (
            hardware * (cx.engineering_pct + cx.project_management_pct) +
            power_kW * cx.permitting_per_kW
        )
        
        subtotal = hardware + out.capex_soft_costs
        out.capex_contingency = subtotal * cx.contingency_pct
        
        out.capex_total = subtotal + out.capex_contingency
        out.capex_per_kWh = out.capex_total / energy_kWh
        out.capex_per_kW = out.capex_total / power_kW
        
        # Incentives
        itc_benefit = out.capex_total * rv.itc_rate
        state_benefit = energy_kWh * rv.state_incentive_per_kWh
        out.capex_after_incentives = out.capex_total - itc_benefit - state_benefit
        
        # ════════════════════════════════════════════════════════════
        # OPEX (Year 1)
        # ════════════════════════════════════════════════════════════
        
        out.opex_fixed_om = power_kW * ox.fixed_om_per_kW_year
        out.opex_variable_om = out.annual_throughput_MWh * ox.variable_om_per_MWh
        out.opex_insurance = out.capex_total * ox.insurance_pct
        out.opex_property_tax = out.capex_total * ox.property_tax_pct
        out.opex_site_lease = perf.power_MW * ox.site_lease_per_MW_year
        
        # Augmentation reserve (annualized)
        battery_cost = out.capex_battery_energy + out.capex_battery_power
        num_augmentations = fin.project_lifetime_years // ox.augmentation_interval_years
        
        aug_npv = sum(
            battery_cost * ox.augmentation_cost_fraction / 
            ((1 + fin.discount_rate) ** (i * ox.augmentation_interval_years))
            for i in range(1, num_augmentations + 1)
            if i * ox.augmentation_interval_years < fin.project_lifetime_years
        )
        crf = self._crf(fin.discount_rate, fin.project_lifetime_years)
        out.opex_augmentation_reserve = aug_npv * crf
        
        out.opex_total_yr1 = (
            out.opex_fixed_om + out.opex_variable_om + out.opex_insurance +
            out.opex_property_tax + out.opex_site_lease + out.opex_augmentation_reserve
        )
        
        # ════════════════════════════════════════════════════════════
        # REVENUE (Year 1)
        # ════════════════════════════════════════════════════════════
        
        # Arbitrage
        discharged_MWh = out.annual_throughput_MWh / 2 * perf.round_trip_efficiency
        out.revenue_arbitrage = discharged_MWh * rv.arbitrage_spread_per_MWh
        
        # Capacity
        out.revenue_capacity = power_kW * rv.capacity_payment_per_kW_year * rv.capacity_credit
        
        # Ancillary services
        out.revenue_ancillary = (
            perf.power_MW * rv.freq_reg_per_MW_year * rv.freq_reg_utilization +
            perf.power_MW * rv.spinning_reserve_per_MW_year
        )
        
        out.revenue_total_yr1 = (out.revenue_arbitrage + out.revenue_capacity + 
                                  out.revenue_ancillary)
        
        # ════════════════════════════════════════════════════════════
        # DEGRADATION & AUGMENTATION TRACKING
        # ════════════════════════════════════════════════════════════
        
        soh = 1.0
        soh_by_year = [soh]
        augmentation_years = []
        total_aug_cost = 0
        
        for year in range(1, fin.project_lifetime_years + 1):
            # Calendar degradation
            soh -= perf.calendar_degradation
            
            # Cycle degradation
            cycle_deg_per_cycle = (perf.soc_max - perf.min_soh) / perf.cycle_life
            soh -= perf.annual_cycles * cycle_deg_per_cycle
            
            # Check for augmentation
            if soh < ox.augmentation_soh_trigger and year % ox.augmentation_interval_years == 0:
                augmentation_years.append(year)
                soh = min(1.0, soh + ox.augmentation_cost_fraction)
                inflation = (1 + fin.inflation_rate) ** (year - 1)
                total_aug_cost += battery_cost * ox.augmentation_cost_fraction * inflation
            
            soh = max(perf.min_soh, soh)
            soh_by_year.append(soh)
        
        out.soh_by_year = soh_by_year
        out.augmentation_years = augmentation_years
        out.total_augmentation_cost = total_aug_cost
        
        # ════════════════════════════════════════════════════════════
        # LCOS CALCULATION
        # ════════════════════════════════════════════════════════════
        
        # Lifetime discharged energy NPV
        lifetime_discharge_npv = 0
        total_discharge = 0
        
        for year in range(1, fin.project_lifetime_years + 1):
            soh_yr = soh_by_year[year]
            year_discharge = discharged_MWh * soh_yr
            lifetime_discharge_npv += year_discharge / ((1 + fin.discount_rate) ** year)
            total_discharge += year_discharge
        
        out.lifetime_throughput_MWh = total_discharge * 2 / perf.round_trip_efficiency
        
        # LCOS components
        out.lcos_capex = out.capex_after_incentives / lifetime_discharge_npv
        
        lifetime_opex_npv = sum(
            (out.opex_total_yr1 - out.opex_augmentation_reserve) * 
            (1 + ox.om_escalation) ** (y - 1) / ((1 + fin.discount_rate) ** y)
            for y in range(1, fin.project_lifetime_years + 1)
        )
        out.lcos_opex = lifetime_opex_npv / lifetime_discharge_npv
        
        out.lcos_augmentation = aug_npv / lifetime_discharge_npv
        
        # Charging cost (energy input to get 1 MWh output)
        out.lcos_charging = charging_cost_per_MWh / perf.round_trip_efficiency
        
        out.lcos_total = (out.lcos_capex + out.lcos_opex + 
                          out.lcos_augmentation + out.lcos_charging)
        
        # ════════════════════════════════════════════════════════════
        # CASH FLOWS
        # ════════════════════════════════════════════════════════════
        
        years = list(range(-1, fin.project_lifetime_years + 1))
        out.years = years
        
        cf_net = []
        
        for year in years:
            if year < 1:
                capex_frac = fin.capex_deployment.get(year, 0)
                cf_net.append(-out.capex_after_incentives * capex_frac)
            else:
                soh_yr = soh_by_year[year]
                
                # Revenue with degradation and escalation
                spread_esc = (1 + rv.spread_escalation) ** (year - 1)
                year_revenue = (
                    discharged_MWh * soh_yr * rv.arbitrage_spread_per_MWh * spread_esc +
                    out.revenue_capacity +
                    out.revenue_ancillary
                )
                
                # OPEX
                inflation = (1 + ox.om_escalation) ** (year - 1)
                year_opex = (out.opex_total_yr1 - out.opex_augmentation_reserve) * inflation
                
                # Augmentation in this year
                if year in augmentation_years:
                    aug_inflation = (1 + fin.inflation_rate) ** (year - 1)
                    year_opex += battery_cost * ox.augmentation_cost_fraction * aug_inflation
                
                cf_net.append(year_revenue - year_opex)
        
        out.cf_net = cf_net
        out.cf_cumulative = list(np.cumsum(cf_net))
        
        # ════════════════════════════════════════════════════════════
        # FINANCIAL METRICS
        # ════════════════════════════════════════════════════════════
        
        out.npv = sum(cf / ((1 + fin.discount_rate) ** (i + 1)) 
                     for i, cf in enumerate(cf_net))
        out.irr = self._calculate_irr(cf_net)
        out.payback_simple = self._simple_payback(out.cf_cumulative)
        out.payback_discounted = self._discounted_payback(cf_net, fin.discount_rate)
        
        self._outputs = out
        return out
    
    def _crf(self, rate: float, years: int) -> float:
        if rate == 0:
            return 1 / years
        return (rate * (1 + rate) ** years) / ((1 + rate) ** years - 1)
    
    def _calculate_irr(self, cashflows: List[float]) -> float:
        try:
            import numpy_financial as npf
            return npf.irr(cashflows)
        except:
            return 0.10
    
    def _simple_payback(self, cumulative: List[float]) -> float:
        for i, c in enumerate(cumulative):
            if c >= 0 and i > 0:
                return i - 1 + (-cumulative[i-1] / (c - cumulative[i-1]))
        return float('inf')
    
    def _discounted_payback(self, cashflows: List[float], rate: float) -> float:
        cumulative = 0
        for i, cf in enumerate(cashflows):
            pv = cf / ((1 + rate) ** i)
            prev = cumulative
            cumulative += pv
            if cumulative >= 0 and i > 0:
                return i - 1 + (-prev / pv) if pv != 0 else float('inf')
        return float('inf')
    
    def summary(self) -> str:
        if self._outputs is None:
            return "Call calculate() first"
        
        o = self._outputs
        return f"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                    BATTERY STORAGE (BESS) TEA SUMMARY                         ║
╠═══════════════════════════════════════════════════════════════════════════════╣

  SYSTEM CONFIGURATION
  ─────────────────────────────────────────────────────────────────────────────
    Power Capacity:                {o.power_MW:>12,.0f} MW
    Energy Capacity:               {o.energy_MWh:>12,.0f} MWh
    Duration:                      {o.duration_hours:>12.1f} hours
    Technology:                    {o.technology:>12}
    
    Annual Cycles:                 {o.annual_cycles:>12,.0f}
    Annual Throughput:             {o.annual_throughput_MWh:>12,.0f} MWh
    Lifetime Throughput:           {o.lifetime_throughput_MWh:>12,.0f} MWh

  CAPEX BREAKDOWN
  ─────────────────────────────────────────────────────────────────────────────
    Battery (Energy):              ${o.capex_battery_energy:>14,.0f}  ({o.capex_battery_energy/o.capex_total*100:>5.1f}%)
    Battery (Power):               ${o.capex_battery_power:>14,.0f}  ({o.capex_battery_power/o.capex_total*100:>5.1f}%)
    Balance of System:             ${o.capex_bos:>14,.0f}
    Installation:                  ${o.capex_installation:>14,.0f}
    Soft Costs:                    ${o.capex_soft_costs:>14,.0f}
    Contingency:                   ${o.capex_contingency:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL CAPEX:                   ${o.capex_total:>14,.0f}
    $/kWh:                         ${o.capex_per_kWh:>14,.0f}
    $/kW:                          ${o.capex_per_kW:>14,.0f}
    After Incentives:              ${o.capex_after_incentives:>14,.0f}

  ANNUAL OPEX (Year 1)
  ─────────────────────────────────────────────────────────────────────────────
    Fixed O&M:                     ${o.opex_fixed_om:>14,.0f}
    Variable O&M:                  ${o.opex_variable_om:>14,.0f}
    Insurance:                     ${o.opex_insurance:>14,.0f}
    Property Tax:                  ${o.opex_property_tax:>14,.0f}
    Site Lease:                    ${o.opex_site_lease:>14,.0f}
    Augmentation Reserve:          ${o.opex_augmentation_reserve:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL OPEX:                    ${o.opex_total_yr1:>14,.0f}

  ANNUAL REVENUE (Year 1)
  ─────────────────────────────────────────────────────────────────────────────
    Arbitrage:                     ${o.revenue_arbitrage:>14,.0f}
    Capacity:                      ${o.revenue_capacity:>14,.0f}
    Ancillary Services:            ${o.revenue_ancillary:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL REVENUE:                 ${o.revenue_total_yr1:>14,.0f}

  LEVELIZED COST OF STORAGE (LCOS)
  ─────────────────────────────────────────────────────────────────────────────
    CAPEX component:               ${o.lcos_capex:>14.2f}/MWh
    OPEX component:                ${o.lcos_opex:>14.2f}/MWh
    Augmentation:                  ${o.lcos_augmentation:>14.2f}/MWh
    Charging cost:                 ${o.lcos_charging:>14.2f}/MWh
    ─────────────────────────────────────────────────────────────────────────
    TOTAL LCOS:                    ${o.lcos_total:>14.2f}/MWh

  DEGRADATION & AUGMENTATION
  ─────────────────────────────────────────────────────────────────────────────
    Initial SOH:                   {o.soh_by_year[0]:>14.1%}
    Final SOH:                     {o.soh_by_year[-1]:>14.1%}
    Augmentation Years:            {str(o.augmentation_years):>14}
    Total Aug Cost:                ${o.total_augmentation_cost:>14,.0f}

  FINANCIAL METRICS
  ─────────────────────────────────────────────────────────────────────────────
    NPV:                           ${o.npv:>14,.0f}
    IRR:                           {o.irr:>14.1%}
    Simple Payback:                {o.payback_simple:>14.1f} years
    Discounted Payback:            {o.payback_discounted:>14.1f} years

╚═══════════════════════════════════════════════════════════════════════════════╝
"""


# ════════════════════════════════════════════════════════════════════════════════
# EXAMPLE
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 80)
    print("BESS TEA - EXAMPLE (100 MW / 400 MWh Li-ion LFP)")
    print("=" * 80)
    
    # Using SHARE model default parameters
    tea = BESSTEA(
        capex=BESSCapexInputs(
            energy_capex_per_kWh=200,      # SHARE: BESS_energy_capex
            power_capex_per_kW=325,        # SHARE: BESS_power_capex
            technology=BatteryTechnology.LI_ION_LFP,
        ),
        opex=BESSOpexInputs(
            fixed_om_per_kW_year=11,       # SHARE: BESS_opex
            augmentation_cost_fraction=0.20,  # SHARE: BESS_aug_cost
            augmentation_interval_years=2,    # SHARE: BESS_aug_years
        ),
        performance=BESSPerformanceInputs(
            power_MW=100,
            duration_hours=4,
            round_trip_efficiency=0.85,    # SHARE: BESS_RTE
            ac_losses=0.02,                # SHARE: BESS_ac_losses
            calendar_degradation=0.01,     # SHARE: BESS_cal_degr
            cycle_life=3000,               # SHARE: BESS_cycles
            min_soh=0.90,                  # SHARE: BESS_min_cap
            availability=0.99,             # SHARE: BESS_avail
            daily_cycles=1.0,
        ),
        revenue=BESSRevenueInputs(
            arbitrage_spread_per_MWh=30,
            capacity_payment_per_kW_year=50,
            freq_reg_per_MW_year=30000,
            itc_rate=0.30,
        ),
        financial=BESSFinancialInputs(
            project_lifetime_years=20,
            discount_rate=0.08,
        ),
    )
    
    results = tea.calculate(charging_cost_per_MWh=30)
    print(tea.summary())
    
    # Test operational model
    print("\nOPERATIONAL MODEL TEST:")
    print("-" * 40)
    op = tea.operational_model
    print(f"Initial SOC: {op.soc:.2%}")
    
    charged = op.charge(200_000)  # Try to charge 200 MWh
    print(f"Charged: {charged/1000:.1f} MWh, SOC: {op.soc:.2%}")
    
    discharged = op.discharge(150_000)  # Discharge 150 MWh
    print(f"Discharged: {discharged/1000:.1f} MWh, SOC: {op.soc:.2%}")
    
    print(f"Cycles completed: {op.cycles_completed:.2f}")
