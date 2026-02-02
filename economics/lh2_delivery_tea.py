"""
LH2 Delivery and Station TEA

Complete liquid hydrogen pathway from delivery to dispensing.

LH2 Delivery:
- LH2 tanker truck lease/costs
- Trucking logistics
- Boil-off losses during transport

LH2 Station (directly connects to dispenser):
- Cryogenic storage tank
- LH2 pump (3 bar → 875 bar)  
- Vaporizer (LH2 → GH2 at high pressure)
- Controls and safety
- Dispenser (no additional compression needed!)

Key advantage: LH2 pump raises pressure BEFORE vaporization,
so output is already at dispensing pressure - no compressor needed!

References:
- DOE H2A Model (NREL)
- HDSAM (Hydrogen Delivery Scenario Analysis Model)
- Linde, Air Liquide LH2 station designs
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum
import numpy as np


class LH2TankerSize(Enum):
    """LH2 tanker truck sizes"""
    SMALL = "small"       # ~1500 kg
    STANDARD = "standard" # ~4000 kg
    LARGE = "large"       # ~5500 kg


@dataclass
class LH2TankerSpecs:
    """LH2 tanker specifications"""
    
    SMALL = {
        'capacity_kg': 1500,
        'purchase_cost': 600_000,
        'lease_per_month': 15_000,
    }
    
    STANDARD = {
        'capacity_kg': 4000,
        'purchase_cost': 900_000,
        'lease_per_month': 22_000,
    }
    
    LARGE = {
        'capacity_kg': 5500,
        'purchase_cost': 1_100_000,
        'lease_per_month': 28_000,
    }


@dataclass
class LH2DeliveryInputs:
    """Inputs for LH2 tanker delivery"""
    
    # ════════════════════════════════════════════════════════════
    # DEMAND
    # ════════════════════════════════════════════════════════════
    
    station_demand_kg_day: float = 1000
    operating_days: int = 350
    
    # ════════════════════════════════════════════════════════════
    # TANKER
    # ════════════════════════════════════════════════════════════
    
    tanker_size: LH2TankerSize = LH2TankerSize.STANDARD
    usable_capacity_pct: float = 0.95  # Higher than GH2 trailers
    lease_tanker: bool = True
    
    # ════════════════════════════════════════════════════════════
    # TRACTOR
    # ════════════════════════════════════════════════════════════
    
    tractor_cost: float = 150_000
    tractor_lease_per_month: float = 3_500
    lease_tractor: bool = True
    
    # ════════════════════════════════════════════════════════════
    # ROUTE
    # ════════════════════════════════════════════════════════════
    
    delivery_distance_km: float = 150  # LH2 can travel further
    average_speed_kmh: float = 60
    loading_time_h: float = 1.0
    unloading_time_h: float = 2.0  # Faster than GH2 cascade fill
    
    # ════════════════════════════════════════════════════════════
    # OPERATING COSTS
    # ════════════════════════════════════════════════════════════
    
    diesel_price: float = 1.30
    fuel_consumption_L_km: float = 0.50  # Heavier than GH2 trailer
    driver_rate: float = 40
    driver_overhead_hours: float = 1.0
    
    # ════════════════════════════════════════════════════════════
    # LOSSES
    # ════════════════════════════════════════════════════════════
    
    # Boil-off during transport (%/day)
    boiloff_rate_per_day: float = 0.003  # 0.3%/day typical
    
    # Average days in transit
    transit_days: float = 0.5
    
    # Transfer losses (%)
    transfer_loss_pct: float = 0.01
    
    # ════════════════════════════════════════════════════════════
    # MAINTENANCE
    # ════════════════════════════════════════════════════════════
    
    tanker_maintenance_per_year: float = 20_000
    tractor_maintenance_per_km: float = 0.12
    
    # ════════════════════════════════════════════════════════════
    # FINANCIAL
    # ════════════════════════════════════════════════════════════
    
    # LH2 cost at liquefaction plant ($/kg)
    lh2_cost_at_plant: float = 4.50  # Higher than GH2 due to liquefaction
    
    discount_rate: float = 0.10
    lifetime_years: int = 15
    insurance_pct: float = 0.02


@dataclass
class LH2StationInputs:
    """Inputs for LH2 station equipment (tank, pump, vaporizer → dispenser)"""
    
    # ════════════════════════════════════════════════════════════
    # CRYOGENIC STORAGE TANK
    # ════════════════════════════════════════════════════════════
    
    # Vacuum-insulated cryogenic tank
    # Cost per kg of LH2 storage capacity
    cryo_tank_cost_per_kg: float = 400
    
    # Storage sizing (days of demand)
    storage_days: float = 3
    
    # Tank boil-off rate (%/day)
    tank_boiloff_per_day: float = 0.002  # 0.2%/day for good tanks
    
    # ════════════════════════════════════════════════════════════
    # LH2 PUMP
    # ════════════════════════════════════════════════════════════
    
    # Cryogenic pump (3 bar → 875 bar)
    # This is the key advantage - pump liquid, not gas!
    pump_base_cost: float = 250_000
    pump_reference_kgday: float = 500
    pump_scaling_factor: float = 0.60
    
    # Pump redundancy
    num_pumps: int = 2
    
    # Pump specific energy (kWh/kg)
    # Much lower than gas compression!
    pump_sec_kWh_kg: float = 0.5
    
    # ════════════════════════════════════════════════════════════
    # VAPORIZER
    # ════════════════════════════════════════════════════════════
    
    # Ambient air or heated vaporizer
    vaporizer_cost_per_kW: float = 300
    
    # Heat duty (kWh_th/kg) - latent + sensible heat
    # ~0.45 MJ/kg latent + ~4 MJ/kg sensible = ~1.2 kWh/kg
    vaporizer_duty_kWh_kg: float = 1.2
    
    # Vaporizer type (ambient or heated)
    # Ambient uses no energy but needs large surface area
    use_ambient_vaporizer: bool = True
    
    # Electric heater efficiency (if not ambient)
    heater_efficiency: float = 0.95
    
    # ════════════════════════════════════════════════════════════
    # DISPENSER
    # ════════════════════════════════════════════════════════════
    
    # H70 dispenser (no pre-cooling needed - gas is already cold!)
    dispenser_cost: float = 120_000  # Slightly cheaper, no chiller
    num_dispensers: int = 2
    
    # Pre-cooling may still be needed for fast fills
    precooler_cost: float = 30_000
    
    # ════════════════════════════════════════════════════════════
    # CONTROLS & SAFETY
    # ════════════════════════════════════════════════════════════
    
    controls_cost: float = 100_000
    safety_systems_cost: float = 75_000  # Cryogenic safety critical
    
    # ════════════════════════════════════════════════════════════
    # SITE
    # ════════════════════════════════════════════════════════════
    
    site_prep_cost: float = 150_000
    electrical_cost: float = 40_000
    canopy_cost: float = 75_000
    
    # ════════════════════════════════════════════════════════════
    # SOFT COSTS
    # ════════════════════════════════════════════════════════════
    
    installation_pct: float = 0.15
    engineering_pct: float = 0.10
    permitting_cost: float = 40_000
    contingency_pct: float = 0.10
    
    # ════════════════════════════════════════════════════════════
    # OPEX
    # ════════════════════════════════════════════════════════════
    
    electricity_price: float = 0.12
    maintenance_pct: float = 0.03
    labor_hours_per_week: float = 10
    labor_rate: float = 50
    insurance_pct: float = 0.01
    property_tax_pct: float = 0.01
    rent_per_year: float = 36_000
    
    # ════════════════════════════════════════════════════════════
    # FINANCIAL
    # ════════════════════════════════════════════════════════════
    
    discount_rate: float = 0.10
    lifetime_years: int = 15
    h2_selling_price: float = 12.00


@dataclass
class LH2PathwayOutputs:
    """Complete LH2 pathway outputs"""
    
    # ════════════════════════════════════════════════════════════
    # DELIVERY
    # ════════════════════════════════════════════════════════════
    
    tanker_capacity_kg: float = 0
    trips_per_year: int = 0
    delivery_cost_per_kg: float = 0
    
    # Delivery cost breakdown
    delivery_tanker_cost: float = 0
    delivery_tractor_cost: float = 0
    delivery_fuel_cost: float = 0
    delivery_driver_cost: float = 0
    delivery_maintenance_cost: float = 0
    delivery_boiloff_cost: float = 0
    delivery_total_annual: float = 0
    
    # ════════════════════════════════════════════════════════════
    # STATION CAPEX
    # ════════════════════════════════════════════════════════════
    
    capex_cryo_tank: float = 0
    capex_pump: float = 0
    capex_vaporizer: float = 0
    capex_dispenser: float = 0
    capex_precooler: float = 0
    capex_controls: float = 0
    capex_safety: float = 0
    capex_site: float = 0
    capex_installation: float = 0
    capex_engineering: float = 0
    capex_permitting: float = 0
    capex_contingency: float = 0
    
    capex_station_total: float = 0
    capex_per_kgday: float = 0
    
    # ════════════════════════════════════════════════════════════
    # STATION OPEX
    # ════════════════════════════════════════════════════════════
    
    opex_electricity: float = 0
    opex_maintenance: float = 0
    opex_labor: float = 0
    opex_insurance: float = 0
    opex_property_tax: float = 0
    opex_rent: float = 0
    opex_boiloff: float = 0
    
    opex_station_total: float = 0
    opex_per_kg: float = 0
    
    # ════════════════════════════════════════════════════════════
    # TOTAL PATHWAY
    # ════════════════════════════════════════════════════════════
    
    lcod_lh2_feedstock: float = 0
    lcod_delivery: float = 0
    lcod_station_capex: float = 0
    lcod_station_opex: float = 0
    lcod_total: float = 0
    
    # Financial
    npv: float = 0
    irr: float = 0


class LH2DeliveryTEA:
    """
    LH2 Delivery and Station TEA
    
    Complete pathway: LH2 tanker → Cryo tank → Pump → Vaporizer → Dispenser
    """
    
    def __init__(self,
                 delivery: Optional[LH2DeliveryInputs] = None,
                 station: Optional[LH2StationInputs] = None):
        
        self.delivery = delivery or LH2DeliveryInputs()
        self.station = station or LH2StationInputs()
        self._outputs: Optional[LH2PathwayOutputs] = None
    
    @property
    def outputs(self) -> LH2PathwayOutputs:
        if self._outputs is None:
            raise ValueError("Call calculate() first")
        return self._outputs
    
    def calculate(self) -> LH2PathwayOutputs:
        """Calculate complete LH2 pathway costs"""
        out = LH2PathwayOutputs()
        dlv = self.delivery
        stn = self.station
        
        capacity = dlv.station_demand_kg_day
        annual_demand = capacity * dlv.operating_days
        
        # ════════════════════════════════════════════════════════════
        # DELIVERY CALCULATIONS
        # ════════════════════════════════════════════════════════════
        
        specs = getattr(LH2TankerSpecs, dlv.tanker_size.name)
        out.tanker_capacity_kg = specs['capacity_kg'] * dlv.usable_capacity_pct
        
        # Trips needed
        trips_per_day = capacity / out.tanker_capacity_kg
        out.trips_per_year = int(np.ceil(trips_per_day * dlv.operating_days))
        
        # Round trip
        round_trip_km = 2 * dlv.delivery_distance_km
        travel_time = round_trip_km / dlv.average_speed_kmh
        round_trip_hours = travel_time + dlv.loading_time_h + dlv.unloading_time_h
        
        # Fleet sizing
        available_hours = 10
        trips_per_tanker_day = available_hours / round_trip_hours
        tankers_needed = int(np.ceil(trips_per_day / trips_per_tanker_day))
        tractors_needed = tankers_needed
        
        crf = self._crf(dlv.discount_rate, dlv.lifetime_years)
        
        # Tanker cost
        if dlv.lease_tanker:
            out.delivery_tanker_cost = tankers_needed * specs['lease_per_month'] * 12
        else:
            out.delivery_tanker_cost = tankers_needed * specs['purchase_cost'] * crf
        
        # Tractor cost
        if dlv.lease_tractor:
            out.delivery_tractor_cost = tractors_needed * dlv.tractor_lease_per_month * 12
        else:
            out.delivery_tractor_cost = tractors_needed * dlv.tractor_cost * crf
        
        # Fuel
        annual_km = out.trips_per_year * round_trip_km
        out.delivery_fuel_cost = annual_km * dlv.fuel_consumption_L_km * dlv.diesel_price
        
        # Driver
        driver_hours = out.trips_per_year * (round_trip_hours + dlv.driver_overhead_hours)
        out.delivery_driver_cost = driver_hours * dlv.driver_rate
        
        # Maintenance
        out.delivery_maintenance_cost = (tankers_needed * dlv.tanker_maintenance_per_year +
                                         annual_km * dlv.tractor_maintenance_per_km)
        
        # Boil-off during transit
        boiloff_pct = dlv.boiloff_rate_per_day * dlv.transit_days + dlv.transfer_loss_pct
        lh2_lost_kg = annual_demand * boiloff_pct / (1 - boiloff_pct)
        out.delivery_boiloff_cost = lh2_lost_kg * dlv.lh2_cost_at_plant
        
        out.delivery_total_annual = (out.delivery_tanker_cost + out.delivery_tractor_cost +
                                     out.delivery_fuel_cost + out.delivery_driver_cost +
                                     out.delivery_maintenance_cost + out.delivery_boiloff_cost)
        
        out.delivery_cost_per_kg = out.delivery_total_annual / annual_demand
        
        # ════════════════════════════════════════════════════════════
        # STATION CAPEX
        # ════════════════════════════════════════════════════════════
        
        # Cryogenic tank
        storage_kg = capacity * stn.storage_days
        out.capex_cryo_tank = storage_kg * stn.cryo_tank_cost_per_kg
        
        # LH2 Pump (scaled)
        scale = capacity / stn.pump_reference_kgday
        out.capex_pump = stn.pump_base_cost * (scale ** stn.pump_scaling_factor) * stn.num_pumps
        
        # Vaporizer
        vaporizer_kW = (capacity / 24) * stn.vaporizer_duty_kWh_kg  # Average hourly load
        out.capex_vaporizer = vaporizer_kW * stn.vaporizer_cost_per_kW
        
        # Dispenser
        out.capex_dispenser = stn.num_dispensers * stn.dispenser_cost
        
        # Pre-cooler
        out.capex_precooler = stn.precooler_cost
        
        # Controls & Safety
        out.capex_controls = stn.controls_cost
        out.capex_safety = stn.safety_systems_cost
        
        # Site
        out.capex_site = stn.site_prep_cost + stn.electrical_cost + stn.canopy_cost
        
        # Equipment subtotal
        equipment = (out.capex_cryo_tank + out.capex_pump + out.capex_vaporizer +
                    out.capex_dispenser + out.capex_precooler + out.capex_controls +
                    out.capex_safety)
        
        # Installation & soft costs
        out.capex_installation = equipment * stn.installation_pct
        out.capex_engineering = equipment * stn.engineering_pct
        out.capex_permitting = stn.permitting_cost
        
        subtotal = equipment + out.capex_site + out.capex_installation + out.capex_engineering + out.capex_permitting
        out.capex_contingency = subtotal * stn.contingency_pct
        
        out.capex_station_total = subtotal + out.capex_contingency
        out.capex_per_kgday = out.capex_station_total / capacity
        
        # ════════════════════════════════════════════════════════════
        # STATION OPEX
        # ════════════════════════════════════════════════════════════
        
        # Electricity (pump only if ambient vaporizer)
        annual_electricity_kWh = annual_demand * stn.pump_sec_kWh_kg
        if not stn.use_ambient_vaporizer:
            annual_electricity_kWh += annual_demand * stn.vaporizer_duty_kWh_kg / stn.heater_efficiency
        out.opex_electricity = annual_electricity_kWh * stn.electricity_price
        
        # Maintenance
        out.opex_maintenance = out.capex_station_total * stn.maintenance_pct
        
        # Labor
        out.opex_labor = stn.labor_hours_per_week * 52 * stn.labor_rate
        
        # Insurance & Tax
        out.opex_insurance = out.capex_station_total * stn.insurance_pct
        out.opex_property_tax = out.capex_station_total * stn.property_tax_pct
        
        # Rent
        out.opex_rent = stn.rent_per_year
        
        # Tank boil-off
        avg_storage_kg = storage_kg / 2  # Average inventory
        daily_boiloff_kg = avg_storage_kg * stn.tank_boiloff_per_day
        annual_boiloff_kg = daily_boiloff_kg * 365
        out.opex_boiloff = annual_boiloff_kg * dlv.lh2_cost_at_plant
        
        out.opex_station_total = (out.opex_electricity + out.opex_maintenance +
                                  out.opex_labor + out.opex_insurance +
                                  out.opex_property_tax + out.opex_rent + out.opex_boiloff)
        out.opex_per_kg = out.opex_station_total / annual_demand
        
        # ════════════════════════════════════════════════════════════
        # TOTAL PATHWAY LCOD
        # ════════════════════════════════════════════════════════════
        
        crf_station = self._crf(stn.discount_rate, stn.lifetime_years)
        
        # Account for all losses
        total_loss_pct = boiloff_pct + (annual_boiloff_kg / annual_demand)
        lh2_purchased_per_kg_dispensed = 1 / (1 - total_loss_pct)
        
        out.lcod_lh2_feedstock = dlv.lh2_cost_at_plant * lh2_purchased_per_kg_dispensed
        out.lcod_delivery = out.delivery_cost_per_kg
        out.lcod_station_capex = (out.capex_station_total * crf_station) / annual_demand
        out.lcod_station_opex = out.opex_per_kg
        
        out.lcod_total = (out.lcod_lh2_feedstock + out.lcod_delivery + 
                         out.lcod_station_capex + out.lcod_station_opex)
        
        # ════════════════════════════════════════════════════════════
        # FINANCIAL
        # ════════════════════════════════════════════════════════════
        
        annual_revenue = annual_demand * stn.h2_selling_price
        annual_lh2_cost = annual_demand * lh2_purchased_per_kg_dispensed * dlv.lh2_cost_at_plant
        annual_profit = annual_revenue - annual_lh2_cost - out.delivery_total_annual - out.opex_station_total
        
        out.npv = -out.capex_station_total
        for year in range(1, stn.lifetime_years + 1):
            out.npv += annual_profit / ((1 + stn.discount_rate) ** year)
        
        self._outputs = out
        return out
    
    def _crf(self, rate: float, years: int) -> float:
        if rate == 0:
            return 1 / years
        return (rate * (1 + rate) ** years) / ((1 + rate) ** years - 1)
    
    def summary(self) -> str:
        if self._outputs is None:
            return "Call calculate() first"
        
        o = self._outputs
        dlv = self.delivery
        stn = self.station
        
        return f"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                    LH2 DELIVERY & STATION TEA                                 ║
║           (Cryo Tank → Pump → Vaporizer → Dispenser)                          ║
╠═══════════════════════════════════════════════════════════════════════════════╣

  DEMAND
  ─────────────────────────────────────────────────────────────────────────────
    Station Capacity:              {dlv.station_demand_kg_day:>12,.0f} kg/day
    Delivery Distance:             {dlv.delivery_distance_km:>12,.0f} km (one-way)

  LH2 DELIVERY
  ─────────────────────────────────────────────────────────────────────────────
    Tanker Capacity:               {o.tanker_capacity_kg:>12,.0f} kg
    Trips per Year:                {o.trips_per_year:>12,}
    
    Annual Costs:
      Tanker:                      ${o.delivery_tanker_cost:>14,.0f}
      Tractor:                     ${o.delivery_tractor_cost:>14,.0f}
      Fuel:                        ${o.delivery_fuel_cost:>14,.0f}
      Driver:                      ${o.delivery_driver_cost:>14,.0f}
      Maintenance:                 ${o.delivery_maintenance_cost:>14,.0f}
      Boil-off/Losses:             ${o.delivery_boiloff_cost:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    DELIVERY TOTAL:                ${o.delivery_total_annual:>14,.0f}
    Delivery $/kg:                 ${o.delivery_cost_per_kg:>14.2f}

  LH2 STATION CAPEX
  ─────────────────────────────────────────────────────────────────────────────
    Cryogenic Tank:                ${o.capex_cryo_tank:>14,.0f}
    LH2 Pump (3→875 bar):          ${o.capex_pump:>14,.0f}
    Vaporizer:                     ${o.capex_vaporizer:>14,.0f}
    Dispensers:                    ${o.capex_dispenser:>14,.0f}
    Pre-cooler:                    ${o.capex_precooler:>14,.0f}
    Controls:                      ${o.capex_controls:>14,.0f}
    Safety Systems:                ${o.capex_safety:>14,.0f}
    Site & Civil:                  ${o.capex_site:>14,.0f}
    Installation:                  ${o.capex_installation:>14,.0f}
    Engineering:                   ${o.capex_engineering:>14,.0f}
    Permitting:                    ${o.capex_permitting:>14,.0f}
    Contingency:                   ${o.capex_contingency:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    STATION CAPEX TOTAL:           ${o.capex_station_total:>14,.0f}
    $/kg-day:                      ${o.capex_per_kgday:>14,.0f}

  LH2 STATION ANNUAL OPEX
  ─────────────────────────────────────────────────────────────────────────────
    Electricity:                   ${o.opex_electricity:>14,.0f}
    Maintenance:                   ${o.opex_maintenance:>14,.0f}
    Labor:                         ${o.opex_labor:>14,.0f}
    Insurance:                     ${o.opex_insurance:>14,.0f}
    Property Tax:                  ${o.opex_property_tax:>14,.0f}
    Rent:                          ${o.opex_rent:>14,.0f}
    Tank Boil-off:                 ${o.opex_boiloff:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    STATION OPEX TOTAL:            ${o.opex_station_total:>14,.0f}
    $/kg:                          ${o.opex_per_kg:>14.2f}

  LEVELIZED COST OF DISPENSED H2
  ─────────────────────────────────────────────────────────────────────────────
    LH2 Feedstock:                 ${o.lcod_lh2_feedstock:>14.2f}/kg
    Delivery:                      ${o.lcod_delivery:>14.2f}/kg
    Station CAPEX:                 ${o.lcod_station_capex:>14.2f}/kg
    Station OPEX:                  ${o.lcod_station_opex:>14.2f}/kg
    ─────────────────────────────────────────────────────────────────────────
    TOTAL LCOD:                    ${o.lcod_total:>14.2f}/kg

  FINANCIAL
  ─────────────────────────────────────────────────────────────────────────────
    NPV:                           ${o.npv:>14,.0f}

╚═══════════════════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    print("=" * 80)
    print("LH2 DELIVERY & STATION TEA - 1000 kg/day Example")
    print("=" * 80)
    
    tea = LH2DeliveryTEA(
        delivery=LH2DeliveryInputs(
            station_demand_kg_day=1000,
            delivery_distance_km=150,
            lh2_cost_at_plant=4.50,
        ),
        station=LH2StationInputs(
            h2_selling_price=12.00,
        ),
    )
    
    results = tea.calculate()
    print(tea.summary())
