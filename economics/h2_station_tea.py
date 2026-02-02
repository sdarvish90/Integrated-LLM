"""
Hydrogen Dispensing Station TEA (H2A/HDSAM Based)

GH2 dispensing station for receiving hydrogen from tube trailer delivery.

Station Components:
- Compressor (250 bar inlet from trailer → 900 bar cascade)
- Low-pressure buffer storage
- High-pressure cascade storage (900 bar)
- Pre-cooling chiller (-40°C for T40 protocol)
- Dispenser (H35/H70)
- Controls and safety systems

This module is for GH2 pathway ONLY. For LH2 pathway, use lh2_delivery_tea.py
which includes the LH2 station (tank, pump, vaporizer → dispenser).

References:
- DOE H2A Model (NREL)
- HDSAM (Hydrogen Delivery Scenario Analysis Model)
- SAE J2601 Fueling Protocol
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum
import numpy as np


class StationCapacity(Enum):
    """Station capacity tiers per H2A"""
    SMALL = 100       # 100 kg/day
    MEDIUM = 450      # 450 kg/day  
    LARGE = 1000      # 1000 kg/day
    HEAVY_DUTY = 1500 # 1500 kg/day (bus/truck depot)


class DispenserType(Enum):
    """Dispenser pressure rating"""
    H35 = 350   # 350 bar (35 MPa) - buses, some trucks
    H70 = 700   # 700 bar (70 MPa) - passenger vehicles
    DUAL = 0    # Both H35 and H70


@dataclass
class H2StationCapexInputs:
    """
    CAPEX inputs for H2 dispensing station (H2A/HDSAM based)
    
    Reference costs are for 1000 kg/day station, scaled using 0.6-0.7 exponents
    """
    
    # ════════════════════════════════════════════════════════════
    # COMPRESSOR (250 bar → 900 bar)
    # ════════════════════════════════════════════════════════════
    
    # Compressor installed cost at reference capacity ($/unit)
    # H2A: ~$300k for 1000 kg/day
    compressor_base_cost: float = 300_000
    compressor_reference_kgday: float = 1000
    compressor_scaling_factor: float = 0.67
    
    # Number of compressors (N+1 redundancy typical)
    num_compressors: int = 2
    
    # ════════════════════════════════════════════════════════════
    # LOW-PRESSURE BUFFER STORAGE (Trailer unloading buffer)
    # ════════════════════════════════════════════════════════════
    
    # LP buffer at ~200-250 bar ($/kg capacity)
    lp_buffer_cost_per_kg: float = 800
    
    # Buffer sizing (hours of throughput)
    lp_buffer_hours: float = 4
    
    # ════════════════════════════════════════════════════════════
    # HIGH-PRESSURE CASCADE STORAGE (900 bar)
    # ════════════════════════════════════════════════════════════
    
    # HP cascade storage ($/kg capacity) - Type IV composite
    # H2A: ~$1000-1500/kg for 900 bar
    hp_storage_cost_per_kg: float = 1200
    
    # Cascade sizing (hours of peak demand)
    hp_storage_hours: float = 8
    
    # Peak demand factor
    peak_factor: float = 2.0
    
    # ════════════════════════════════════════════════════════════
    # PRE-COOLING CHILLER
    # ════════════════════════════════════════════════════════════
    
    # Chiller for T40 protocol (-40°C)
    # H2A: ~$50k base + scaling
    chiller_base_cost: float = 50_000
    chiller_reference_kgday: float = 450
    chiller_scaling_factor: float = 0.50
    
    # ════════════════════════════════════════════════════════════
    # DISPENSERS
    # ════════════════════════════════════════════════════════════
    
    # Dispenser costs (H2A values)
    dispenser_h70_cost: float = 150_000
    dispenser_h35_cost: float = 100_000
    dispenser_dual_cost: float = 200_000
    
    # Number of dispensing positions
    num_dispensers: int = 2
    
    # ════════════════════════════════════════════════════════════
    # CONTROLS & SAFETY
    # ════════════════════════════════════════════════════════════
    
    # Control system (PLC, HMI, safety interlocks)
    controls_cost: float = 75_000
    
    # Safety systems (leak detection, fire suppression, ventilation)
    safety_systems_cost: float = 50_000
    
    # ════════════════════════════════════════════════════════════
    # SITE & INSTALLATION
    # ════════════════════════════════════════════════════════════
    
    # Site preparation and civil works
    site_prep_cost: float = 150_000
    
    # Canopy structure
    canopy_cost: float = 75_000
    
    # Electrical infrastructure
    electrical_cost: float = 50_000
    
    # Installation labor (% of equipment)
    installation_pct: float = 0.15
    
    # ════════════════════════════════════════════════════════════
    # SOFT COSTS
    # ════════════════════════════════════════════════════════════
    
    # Engineering & design (% of equipment)
    engineering_pct: float = 0.10
    
    # Permitting
    permitting_cost: float = 30_000
    
    # Contingency (% of direct costs)
    contingency_pct: float = 0.10


@dataclass
class H2StationOpexInputs:
    """Operating costs for H2 dispensing station"""
    
    # ════════════════════════════════════════════════════════════
    # ELECTRICITY
    # ════════════════════════════════════════════════════════════
    
    # Electricity price ($/kWh)
    electricity_price: float = 0.12
    
    # Compressor SEC (kWh/kg) - 250 bar to 900 bar
    # H2A: ~1.5-2.0 kWh/kg for this pressure range
    sec_compression: float = 1.8
    
    # Chiller SEC (kWh/kg) - pre-cooling to -40°C
    # H2A: ~0.5-1.0 kWh/kg
    sec_cooling: float = 0.8
    
    # BOP/controls (kWh/kg)
    sec_bop: float = 0.2
    
    # ════════════════════════════════════════════════════════════
    # MAINTENANCE
    # ════════════════════════════════════════════════════════════
    
    # Compressor maintenance (% of compressor CAPEX)
    compressor_maintenance_pct: float = 0.05
    
    # Other equipment maintenance (% of other CAPEX)
    other_maintenance_pct: float = 0.02
    
    # ════════════════════════════════════════════════════════════
    # LABOR
    # ════════════════════════════════════════════════════════════
    
    # Station can be unmanned with remote monitoring
    # Labor for periodic inspection/maintenance
    labor_hours_per_week: float = 10
    labor_rate: float = 50  # $/hour
    
    # ════════════════════════════════════════════════════════════
    # OTHER
    # ════════════════════════════════════════════════════════════
    
    # Insurance (% of CAPEX)
    insurance_pct: float = 0.01
    
    # Property tax (% of CAPEX)
    property_tax_pct: float = 0.01
    
    # Rent/lease ($/year) - if not owned
    rent_per_year: float = 36_000
    
    # H2 losses (% of throughput) - venting, leaks
    h2_loss_pct: float = 0.005


@dataclass  
class H2StationPerformanceInputs:
    """Performance parameters"""
    
    # Design capacity (kg/day)
    capacity_kg_day: float = 1000
    
    # Dispenser type
    dispenser_type: DispenserType = DispenserType.H70
    
    # Utilization
    utilization_yr1: float = 0.50
    utilization_mature: float = 0.80
    ramp_years: int = 3
    
    # Operating hours
    hours_per_day: float = 18
    days_per_year: int = 365
    
    # Availability
    availability: float = 0.98


@dataclass
class H2StationFinancialInputs:
    """Financial parameters"""
    
    project_lifetime_years: int = 15
    discount_rate: float = 0.10
    inflation_rate: float = 0.025
    
    # H2 feedstock cost (delivered from tube trailer)
    h2_feedstock_cost: float = 5.00  # $/kg
    
    # Selling price
    h2_selling_price: float = 12.00  # $/kg


@dataclass
class H2StationTEAOutputs:
    """TEA outputs"""
    
    # System
    capacity_kg_day: float = 0
    dispenser_type: str = ""
    
    # CAPEX breakdown
    capex_compressor: float = 0
    capex_lp_storage: float = 0
    capex_hp_storage: float = 0
    capex_chiller: float = 0
    capex_dispensers: float = 0
    capex_controls: float = 0
    capex_safety: float = 0
    capex_site: float = 0
    capex_installation: float = 0
    capex_engineering: float = 0
    capex_permitting: float = 0
    capex_contingency: float = 0
    
    capex_total: float = 0
    capex_per_kgday: float = 0
    
    # OPEX (annual at mature utilization)
    opex_electricity: float = 0
    opex_maintenance: float = 0
    opex_labor: float = 0
    opex_insurance: float = 0
    opex_property_tax: float = 0
    opex_rent: float = 0
    opex_h2_losses: float = 0
    
    opex_total: float = 0
    opex_per_kg: float = 0
    
    # Levelized costs ($/kg dispensed)
    lcod_capex: float = 0
    lcod_opex: float = 0
    lcod_feedstock: float = 0
    lcod_total: float = 0
    
    # Financial
    npv: float = 0
    irr: float = 0
    payback_years: float = 0
    
    # Annual throughput
    throughput_yr1: float = 0
    throughput_mature: float = 0


class H2StationTEA:
    """
    GH2 Dispensing Station TEA (H2A/HDSAM Based)
    
    For stations receiving H2 via tube trailer delivery.
    Station compresses from ~250 bar to 900 bar cascade for dispensing.
    """
    
    def __init__(self,
                 capex: Optional[H2StationCapexInputs] = None,
                 opex: Optional[H2StationOpexInputs] = None,
                 performance: Optional[H2StationPerformanceInputs] = None,
                 financial: Optional[H2StationFinancialInputs] = None):
        
        self.capex = capex or H2StationCapexInputs()
        self.opex = opex or H2StationOpexInputs()
        self.performance = performance or H2StationPerformanceInputs()
        self.financial = financial or H2StationFinancialInputs()
        
        self._outputs: Optional[H2StationTEAOutputs] = None
    
    @property
    def outputs(self) -> H2StationTEAOutputs:
        if self._outputs is None:
            raise ValueError("Call calculate() first")
        return self._outputs
    
    def calculate(self) -> H2StationTEAOutputs:
        """Run H2 station TEA"""
        out = H2StationTEAOutputs()
        cx = self.capex
        ox = self.opex
        perf = self.performance
        fin = self.financial
        
        out.capacity_kg_day = perf.capacity_kg_day
        out.dispenser_type = perf.dispenser_type.name
        
        capacity = perf.capacity_kg_day
        
        # ════════════════════════════════════════════════════════════
        # CAPEX - Equipment with scaling
        # ════════════════════════════════════════════════════════════
        
        # Compressor (scaled)
        scale = capacity / cx.compressor_reference_kgday
        out.capex_compressor = (cx.compressor_base_cost * 
                                (scale ** cx.compressor_scaling_factor) *
                                cx.num_compressors)
        
        # LP Buffer storage
        lp_buffer_kg = capacity / 24 * cx.lp_buffer_hours
        out.capex_lp_storage = lp_buffer_kg * cx.lp_buffer_cost_per_kg
        
        # HP Cascade storage
        peak_hour_kg = capacity / perf.hours_per_day * cx.peak_factor
        hp_storage_kg = peak_hour_kg * cx.hp_storage_hours
        out.capex_hp_storage = hp_storage_kg * cx.hp_storage_cost_per_kg
        
        # Chiller (scaled)
        scale_chiller = capacity / cx.chiller_reference_kgday
        out.capex_chiller = cx.chiller_base_cost * (scale_chiller ** cx.chiller_scaling_factor)
        
        # Dispensers
        if perf.dispenser_type == DispenserType.H70:
            out.capex_dispensers = cx.num_dispensers * cx.dispenser_h70_cost
        elif perf.dispenser_type == DispenserType.H35:
            out.capex_dispensers = cx.num_dispensers * cx.dispenser_h35_cost
        else:
            out.capex_dispensers = cx.num_dispensers * cx.dispenser_dual_cost
        
        # Controls & Safety
        out.capex_controls = cx.controls_cost
        out.capex_safety = cx.safety_systems_cost
        
        # Site
        out.capex_site = cx.site_prep_cost + cx.canopy_cost + cx.electrical_cost
        
        # Equipment subtotal
        equipment = (out.capex_compressor + out.capex_lp_storage + 
                    out.capex_hp_storage + out.capex_chiller +
                    out.capex_dispensers + out.capex_controls + 
                    out.capex_safety)
        
        # Installation
        out.capex_installation = equipment * cx.installation_pct
        
        # Engineering
        out.capex_engineering = equipment * cx.engineering_pct
        
        # Permitting
        out.capex_permitting = cx.permitting_cost
        
        # Subtotal
        subtotal = (equipment + out.capex_site + out.capex_installation + 
                   out.capex_engineering + out.capex_permitting)
        
        # Contingency
        out.capex_contingency = subtotal * cx.contingency_pct
        
        out.capex_total = subtotal + out.capex_contingency
        out.capex_per_kgday = out.capex_total / capacity
        
        # ════════════════════════════════════════════════════════════
        # THROUGHPUT
        # ════════════════════════════════════════════════════════════
        
        out.throughput_yr1 = (capacity * perf.utilization_yr1 * 
                              perf.days_per_year * perf.availability)
        out.throughput_mature = (capacity * perf.utilization_mature * 
                                 perf.days_per_year * perf.availability)
        
        # ════════════════════════════════════════════════════════════
        # OPEX (at mature utilization)
        # ════════════════════════════════════════════════════════════
        
        annual_kg = out.throughput_mature
        
        # Electricity
        sec_total = ox.sec_compression + ox.sec_cooling + ox.sec_bop
        out.opex_electricity = annual_kg * sec_total * ox.electricity_price
        
        # Maintenance
        out.opex_maintenance = (out.capex_compressor * ox.compressor_maintenance_pct +
                               (out.capex_total - out.capex_compressor) * ox.other_maintenance_pct)
        
        # Labor
        out.opex_labor = ox.labor_hours_per_week * 52 * ox.labor_rate
        
        # Insurance & Tax
        out.opex_insurance = out.capex_total * ox.insurance_pct
        out.opex_property_tax = out.capex_total * ox.property_tax_pct
        
        # Rent
        out.opex_rent = ox.rent_per_year
        
        # H2 losses
        out.opex_h2_losses = annual_kg * ox.h2_loss_pct * fin.h2_feedstock_cost
        
        out.opex_total = (out.opex_electricity + out.opex_maintenance + 
                         out.opex_labor + out.opex_insurance + 
                         out.opex_property_tax + out.opex_rent + out.opex_h2_losses)
        out.opex_per_kg = out.opex_total / annual_kg if annual_kg > 0 else 0
        
        # ════════════════════════════════════════════════════════════
        # LEVELIZED COST
        # ════════════════════════════════════════════════════════════
        
        crf = self._crf(fin.discount_rate, fin.project_lifetime_years)
        
        out.lcod_capex = (out.capex_total * crf) / out.throughput_mature
        out.lcod_opex = out.opex_per_kg
        out.lcod_feedstock = fin.h2_feedstock_cost * (1 + ox.h2_loss_pct)
        out.lcod_total = out.lcod_capex + out.lcod_opex + out.lcod_feedstock
        
        # ════════════════════════════════════════════════════════════
        # FINANCIAL
        # ════════════════════════════════════════════════════════════
        
        # Simple NPV calculation
        annual_revenue = out.throughput_mature * fin.h2_selling_price
        annual_h2_cost = out.throughput_mature * fin.h2_feedstock_cost * (1 + ox.h2_loss_pct)
        annual_profit = annual_revenue - annual_h2_cost - out.opex_total
        
        # NPV with ramp
        npv = -out.capex_total
        for year in range(1, fin.project_lifetime_years + 1):
            if year <= perf.ramp_years:
                util = perf.utilization_yr1 + (perf.utilization_mature - perf.utilization_yr1) * (year - 1) / perf.ramp_years
            else:
                util = perf.utilization_mature
            
            year_throughput = capacity * util * perf.days_per_year * perf.availability
            year_revenue = year_throughput * fin.h2_selling_price
            year_h2_cost = year_throughput * fin.h2_feedstock_cost * (1 + ox.h2_loss_pct)
            year_opex = out.opex_total * (util / perf.utilization_mature)
            year_profit = year_revenue - year_h2_cost - year_opex
            
            npv += year_profit / ((1 + fin.discount_rate) ** year)
        
        out.npv = npv
        out.irr = self._calculate_irr(out, perf, fin)
        out.payback_years = out.capex_total / annual_profit if annual_profit > 0 else float('inf')
        
        self._outputs = out
        return out
    
    def _crf(self, rate: float, years: int) -> float:
        if rate == 0:
            return 1 / years
        return (rate * (1 + rate) ** years) / ((1 + rate) ** years - 1)
    
    def _calculate_irr(self, out, perf, fin) -> float:
        try:
            cashflows = [-out.capex_total]
            for year in range(1, fin.project_lifetime_years + 1):
                if year <= perf.ramp_years:
                    util = perf.utilization_yr1 + (perf.utilization_mature - perf.utilization_yr1) * (year - 1) / perf.ramp_years
                else:
                    util = perf.utilization_mature
                
                year_throughput = perf.capacity_kg_day * util * perf.days_per_year * perf.availability
                year_revenue = year_throughput * fin.h2_selling_price
                year_h2_cost = year_throughput * fin.h2_feedstock_cost * (1 + self.opex.h2_loss_pct)
                year_opex = out.opex_total * (util / perf.utilization_mature)
                cashflows.append(year_revenue - year_h2_cost - year_opex)
            
            import numpy_financial as npf
            return npf.irr(cashflows)
        except:
            return 0.0
    
    def summary(self) -> str:
        if self._outputs is None:
            return "Call calculate() first"
        
        o = self._outputs
        return f"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║               H2 DISPENSING STATION TEA (GH2 Pathway)                         ║
║                        H2A/HDSAM Based                                        ║
╠═══════════════════════════════════════════════════════════════════════════════╣

  STATION CONFIGURATION
  ─────────────────────────────────────────────────────────────────────────────
    Design Capacity:               {o.capacity_kg_day:>12,.0f} kg/day
    Dispenser Type:                {o.dispenser_type:>12}
    Year 1 Throughput:             {o.throughput_yr1:>12,.0f} kg/year
    Mature Throughput:             {o.throughput_mature:>12,.0f} kg/year

  CAPEX BREAKDOWN
  ─────────────────────────────────────────────────────────────────────────────
    Compressor (250→900 bar):      ${o.capex_compressor:>14,.0f}
    LP Buffer Storage:             ${o.capex_lp_storage:>14,.0f}
    HP Cascade Storage:            ${o.capex_hp_storage:>14,.0f}
    Pre-cooling Chiller:           ${o.capex_chiller:>14,.0f}
    Dispensers:                    ${o.capex_dispensers:>14,.0f}
    Controls:                      ${o.capex_controls:>14,.0f}
    Safety Systems:                ${o.capex_safety:>14,.0f}
    Site & Civil:                  ${o.capex_site:>14,.0f}
    Installation:                  ${o.capex_installation:>14,.0f}
    Engineering:                   ${o.capex_engineering:>14,.0f}
    Permitting:                    ${o.capex_permitting:>14,.0f}
    Contingency:                   ${o.capex_contingency:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL CAPEX:                   ${o.capex_total:>14,.0f}
    $/kg-day capacity:             ${o.capex_per_kgday:>14,.0f}

  ANNUAL OPEX (at mature utilization)
  ─────────────────────────────────────────────────────────────────────────────
    Electricity:                   ${o.opex_electricity:>14,.0f}
    Maintenance:                   ${o.opex_maintenance:>14,.0f}
    Labor:                         ${o.opex_labor:>14,.0f}
    Insurance:                     ${o.opex_insurance:>14,.0f}
    Property Tax:                  ${o.opex_property_tax:>14,.0f}
    Rent:                          ${o.opex_rent:>14,.0f}
    H2 Losses:                     ${o.opex_h2_losses:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL OPEX:                    ${o.opex_total:>14,.0f}
    $/kg dispensed:                ${o.opex_per_kg:>14.2f}

  LEVELIZED COST OF DISPENSED H2
  ─────────────────────────────────────────────────────────────────────────────
    Station CAPEX:                 ${o.lcod_capex:>14.2f}/kg
    Station OPEX:                  ${o.lcod_opex:>14.2f}/kg
    H2 Feedstock:                  ${o.lcod_feedstock:>14.2f}/kg
    ─────────────────────────────────────────────────────────────────────────
    TOTAL LCOD:                    ${o.lcod_total:>14.2f}/kg

  FINANCIAL METRICS
  ─────────────────────────────────────────────────────────────────────────────
    NPV:                           ${o.npv:>14,.0f}
    IRR:                           {o.irr:>14.1%}
    Simple Payback:                {o.payback_years:>14.1f} years

╚═══════════════════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 80)
    print("H2 DISPENSING STATION TEA - 1000 kg/day Example")
    print("=" * 80)
    
    tea = H2StationTEA(
        performance=H2StationPerformanceInputs(
            capacity_kg_day=1000,
            dispenser_type=DispenserType.H70,
        ),
        financial=H2StationFinancialInputs(
            h2_feedstock_cost=5.00,  # Delivered from tube trailer
            h2_selling_price=12.00,
        ),
    )
    
    results = tea.calculate()
    print(tea.summary())
