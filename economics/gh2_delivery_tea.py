"""
GH2 Tube Trailer Delivery TEA

Costs for delivering gaseous hydrogen via tube trailer.

Delivery Components:
- Tube trailer lease/purchase
- Tractor costs
- Driver labor
- Fuel
- Maintenance
- Loading terminal costs (at production site)

Output: Delivered H2 cost ($/kg) at the station inlet (250 bar)

This module covers DELIVERY ONLY. Station costs are in h2_station_tea.py.

References:
- DOE H2A Model (NREL)
- HDSAM (Hydrogen Delivery Scenario Analysis Model)
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum
import numpy as np


class TrailerType(Enum):
    """GH2 tube trailer types"""
    STEEL_180BAR = "steel_180bar"       # Steel tubes, 180 bar, ~300 kg
    COMPOSITE_250BAR = "composite_250bar" # Composite, 250 bar, ~500-700 kg
    COMPOSITE_500BAR = "composite_500bar" # High pressure composite, ~1000 kg


@dataclass
class TrailerSpecs:
    """Tube trailer specifications by type"""
    
    STEEL_180BAR = {
        'capacity_kg': 300,
        'pressure_bar': 180,
        'purchase_cost': 300_000,
        'lease_per_month': 8_000,
        'tare_weight_kg': 15_000,
    }
    
    COMPOSITE_250BAR = {
        'capacity_kg': 600,
        'pressure_bar': 250,
        'purchase_cost': 800_000,
        'lease_per_month': 18_000,
        'tare_weight_kg': 12_000,
    }
    
    COMPOSITE_500BAR = {
        'capacity_kg': 1000,
        'pressure_bar': 500,
        'purchase_cost': 1_200_000,
        'lease_per_month': 28_000,
        'tare_weight_kg': 14_000,
    }


@dataclass
class GH2DeliveryInputs:
    """Inputs for GH2 tube trailer delivery"""
    
    # ════════════════════════════════════════════════════════════
    # DEMAND
    # ════════════════════════════════════════════════════════════
    
    # Station demand (kg/day)
    station_demand_kg_day: float = 1000
    
    # Operating days per year
    operating_days: int = 350
    
    # ════════════════════════════════════════════════════════════
    # TRAILER
    # ════════════════════════════════════════════════════════════
    
    trailer_type: TrailerType = TrailerType.COMPOSITE_250BAR
    
    # Usable capacity (% of nameplate - can't fully empty)
    usable_capacity_pct: float = 0.90
    
    # Own or lease
    lease_trailer: bool = True
    
    # Number of spare trailers (for swap operations)
    num_spare_trailers: int = 1
    
    # ════════════════════════════════════════════════════════════
    # TRACTOR
    # ════════════════════════════════════════════════════════════
    
    # Tractor purchase cost
    tractor_cost: float = 150_000
    
    # Tractor lease ($/month)
    tractor_lease_per_month: float = 3_500
    
    # Lease tractor?
    lease_tractor: bool = True
    
    # ════════════════════════════════════════════════════════════
    # ROUTE
    # ════════════════════════════════════════════════════════════
    
    # One-way distance (km)
    delivery_distance_km: float = 80
    
    # Average speed (km/h)
    average_speed_kmh: float = 50
    
    # Loading time at production site (hours)
    loading_time_h: float = 1.5
    
    # Unloading time at station (hours) - cascade fill
    unloading_time_h: float = 3.0
    
    # ════════════════════════════════════════════════════════════
    # OPERATING COSTS
    # ════════════════════════════════════════════════════════════
    
    # Diesel price ($/L)
    diesel_price: float = 1.30
    
    # Fuel consumption (L/km) - loaded
    fuel_consumption_loaded: float = 0.45
    
    # Fuel consumption (L/km) - empty return
    fuel_consumption_empty: float = 0.35
    
    # Driver hourly rate ($/h)
    driver_rate: float = 40
    
    # Driver hours per trip (including wait, paperwork)
    driver_overhead_hours: float = 1.0
    
    # ════════════════════════════════════════════════════════════
    # MAINTENANCE
    # ════════════════════════════════════════════════════════════
    
    # Trailer maintenance ($/year)
    trailer_maintenance_per_year: float = 10_000
    
    # Tractor maintenance ($/km)
    tractor_maintenance_per_km: float = 0.12
    
    # ════════════════════════════════════════════════════════════
    # TERMINAL (at production site)
    # ════════════════════════════════════════════════════════════
    
    # Loading terminal CAPEX (compressor, manifold, controls)
    # Shared across multiple stations - allocate fraction
    terminal_capex: float = 500_000
    terminal_share_pct: float = 0.25  # This station's share
    
    # Terminal OPEX ($/year for this station's share)
    terminal_opex_per_year: float = 15_000
    
    # ════════════════════════════════════════════════════════════
    # FINANCIAL
    # ════════════════════════════════════════════════════════════
    
    # H2 production cost at terminal ($/kg)
    h2_production_cost: float = 3.00
    
    # Discount rate
    discount_rate: float = 0.10
    
    # Project lifetime
    lifetime_years: int = 15
    
    # Insurance (% of owned assets)
    insurance_pct: float = 0.02
    
    # Transfer losses (%)
    transfer_loss_pct: float = 0.01


@dataclass
class GH2DeliveryOutputs:
    """GH2 delivery TEA outputs"""
    
    # Fleet sizing
    trailer_capacity_kg: float = 0
    trips_per_day: float = 0
    trips_per_year: int = 0
    num_trailers_required: int = 0
    num_tractors_required: int = 0
    
    # Trip details
    round_trip_km: float = 0
    round_trip_hours: float = 0
    
    # Annual costs
    cost_trailer: float = 0       # Lease or amortized purchase
    cost_tractor: float = 0       # Lease or amortized purchase
    cost_fuel: float = 0
    cost_driver: float = 0
    cost_maintenance: float = 0
    cost_terminal: float = 0
    cost_insurance: float = 0
    cost_h2_production: float = 0
    cost_losses: float = 0
    
    cost_total_annual: float = 0
    
    # Per-kg costs
    delivery_cost_per_kg: float = 0    # Delivery only (excl. production)
    delivered_cost_per_kg: float = 0   # Total including production
    
    # Breakdown
    cost_breakdown: Dict[str, float] = field(default_factory=dict)


class GH2DeliveryTEA:
    """
    GH2 Tube Trailer Delivery TEA
    
    Calculates cost to deliver H2 from production site to station via tube trailer.
    """
    
    def __init__(self, inputs: Optional[GH2DeliveryInputs] = None):
        self.inputs = inputs or GH2DeliveryInputs()
        self._outputs: Optional[GH2DeliveryOutputs] = None
    
    @property
    def outputs(self) -> GH2DeliveryOutputs:
        if self._outputs is None:
            raise ValueError("Call calculate() first")
        return self._outputs
    
    def calculate(self) -> GH2DeliveryOutputs:
        """Calculate GH2 delivery costs"""
        out = GH2DeliveryOutputs()
        inp = self.inputs
        
        # Get trailer specs
        specs = getattr(TrailerSpecs, inp.trailer_type.name)
        
        # ════════════════════════════════════════════════════════════
        # FLEET SIZING
        # ════════════════════════════════════════════════════════════
        
        out.trailer_capacity_kg = specs['capacity_kg'] * inp.usable_capacity_pct
        
        # Trips needed per day
        out.trips_per_day = inp.station_demand_kg_day / out.trailer_capacity_kg
        out.trips_per_year = int(np.ceil(out.trips_per_day * inp.operating_days))
        
        # Round trip details
        out.round_trip_km = 2 * inp.delivery_distance_km
        travel_time = out.round_trip_km / inp.average_speed_kmh
        out.round_trip_hours = travel_time + inp.loading_time_h + inp.unloading_time_h
        
        # Trips per day per trailer (assuming 10-12 hour driver shifts)
        available_hours = 10
        trips_per_trailer_day = available_hours / out.round_trip_hours
        
        # Trailers needed for demand
        trailers_for_demand = int(np.ceil(out.trips_per_day / trips_per_trailer_day))
        out.num_trailers_required = trailers_for_demand + inp.num_spare_trailers
        
        # Tractors (one per active route)
        out.num_tractors_required = trailers_for_demand
        
        # ════════════════════════════════════════════════════════════
        # TRAILER COSTS
        # ════════════════════════════════════════════════════════════
        
        if inp.lease_trailer:
            out.cost_trailer = (out.num_trailers_required * 
                               specs['lease_per_month'] * 12)
        else:
            # Amortized purchase
            crf = self._crf(inp.discount_rate, inp.lifetime_years)
            out.cost_trailer = (out.num_trailers_required * 
                               specs['purchase_cost'] * crf)
        
        # ════════════════════════════════════════════════════════════
        # TRACTOR COSTS
        # ════════════════════════════════════════════════════════════
        
        if inp.lease_tractor:
            out.cost_tractor = (out.num_tractors_required * 
                               inp.tractor_lease_per_month * 12)
        else:
            crf = self._crf(inp.discount_rate, inp.lifetime_years)
            out.cost_tractor = (out.num_tractors_required * 
                               inp.tractor_cost * crf)
        
        # ════════════════════════════════════════════════════════════
        # FUEL COSTS
        # ════════════════════════════════════════════════════════════
        
        annual_km = out.trips_per_year * out.round_trip_km
        
        # Average fuel consumption (loaded one way, empty return)
        avg_fuel = (inp.fuel_consumption_loaded + inp.fuel_consumption_empty) / 2
        out.cost_fuel = annual_km * avg_fuel * inp.diesel_price
        
        # ════════════════════════════════════════════════════════════
        # DRIVER COSTS
        # ════════════════════════════════════════════════════════════
        
        driver_hours_per_trip = out.round_trip_hours + inp.driver_overhead_hours
        annual_driver_hours = out.trips_per_year * driver_hours_per_trip
        out.cost_driver = annual_driver_hours * inp.driver_rate
        
        # ════════════════════════════════════════════════════════════
        # MAINTENANCE
        # ════════════════════════════════════════════════════════════
        
        out.cost_maintenance = (
            out.num_trailers_required * inp.trailer_maintenance_per_year +
            annual_km * inp.tractor_maintenance_per_km
        )
        
        # ════════════════════════════════════════════════════════════
        # TERMINAL COSTS
        # ════════════════════════════════════════════════════════════
        
        crf = self._crf(inp.discount_rate, inp.lifetime_years)
        terminal_capex_annual = inp.terminal_capex * inp.terminal_share_pct * crf
        out.cost_terminal = terminal_capex_annual + inp.terminal_opex_per_year
        
        # ════════════════════════════════════════════════════════════
        # INSURANCE
        # ════════════════════════════════════════════════════════════
        
        owned_assets = 0
        if not inp.lease_trailer:
            owned_assets += out.num_trailers_required * specs['purchase_cost']
        if not inp.lease_tractor:
            owned_assets += out.num_tractors_required * inp.tractor_cost
        
        out.cost_insurance = owned_assets * inp.insurance_pct
        
        # ════════════════════════════════════════════════════════════
        # H2 PRODUCTION COST
        # ════════════════════════════════════════════════════════════
        
        annual_h2_kg = inp.station_demand_kg_day * inp.operating_days
        # Account for losses - need to purchase more
        h2_purchased_kg = annual_h2_kg / (1 - inp.transfer_loss_pct)
        out.cost_h2_production = h2_purchased_kg * inp.h2_production_cost
        
        # ════════════════════════════════════════════════════════════
        # LOSSES
        # ════════════════════════════════════════════════════════════
        
        lost_kg = h2_purchased_kg * inp.transfer_loss_pct
        out.cost_losses = lost_kg * inp.h2_production_cost
        
        # ════════════════════════════════════════════════════════════
        # TOTALS
        # ════════════════════════════════════════════════════════════
        
        # Delivery costs only (excluding H2 production)
        delivery_costs = (out.cost_trailer + out.cost_tractor + out.cost_fuel +
                         out.cost_driver + out.cost_maintenance + out.cost_terminal +
                         out.cost_insurance)
        
        out.cost_total_annual = delivery_costs + out.cost_h2_production
        
        # Per-kg costs
        out.delivery_cost_per_kg = delivery_costs / annual_h2_kg
        out.delivered_cost_per_kg = out.cost_total_annual / annual_h2_kg
        
        # Breakdown
        out.cost_breakdown = {
            'H2 Production': out.cost_h2_production / annual_h2_kg,
            'Trailer': out.cost_trailer / annual_h2_kg,
            'Tractor': out.cost_tractor / annual_h2_kg,
            'Fuel': out.cost_fuel / annual_h2_kg,
            'Driver': out.cost_driver / annual_h2_kg,
            'Maintenance': out.cost_maintenance / annual_h2_kg,
            'Terminal': out.cost_terminal / annual_h2_kg,
            'Insurance': out.cost_insurance / annual_h2_kg,
        }
        
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
        inp = self.inputs
        
        return f"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                    GH2 TUBE TRAILER DELIVERY TEA                              ║
╠═══════════════════════════════════════════════════════════════════════════════╣

  DEMAND & ROUTE
  ─────────────────────────────────────────────────────────────────────────────
    Station Demand:                {inp.station_demand_kg_day:>12,.0f} kg/day
    Delivery Distance:             {inp.delivery_distance_km:>12,.0f} km (one-way)
    Round Trip:                    {o.round_trip_km:>12,.0f} km
    Round Trip Time:               {o.round_trip_hours:>12.1f} hours

  FLEET
  ─────────────────────────────────────────────────────────────────────────────
    Trailer Type:                  {inp.trailer_type.value:>16}
    Trailer Capacity (usable):     {o.trailer_capacity_kg:>12,.0f} kg
    Trips per Day:                 {o.trips_per_day:>12.1f}
    Trips per Year:                {o.trips_per_year:>12,}
    Trailers Required:             {o.num_trailers_required:>12}
    Tractors Required:             {o.num_tractors_required:>12}

  ANNUAL COSTS
  ─────────────────────────────────────────────────────────────────────────────
    H2 Production:                 ${o.cost_h2_production:>14,.0f}
    Trailer ({'Lease' if inp.lease_trailer else 'Own'}):                  ${o.cost_trailer:>14,.0f}
    Tractor ({'Lease' if inp.lease_tractor else 'Own'}):                  ${o.cost_tractor:>14,.0f}
    Fuel:                          ${o.cost_fuel:>14,.0f}
    Driver:                        ${o.cost_driver:>14,.0f}
    Maintenance:                   ${o.cost_maintenance:>14,.0f}
    Terminal:                      ${o.cost_terminal:>14,.0f}
    Insurance:                     ${o.cost_insurance:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL ANNUAL:                  ${o.cost_total_annual:>14,.0f}

  COST BREAKDOWN ($/kg delivered)
  ─────────────────────────────────────────────────────────────────────────────"""
        
        breakdown_str = ""
        for item, cost in o.cost_breakdown.items():
            breakdown_str += f"\n    {item + ':':28} ${cost:>10.2f}/kg"
        
        return self.summary.__doc__ + f"""{breakdown_str}
    ─────────────────────────────────────────────────────────────────────────
    DELIVERY COST (excl. H2):      ${o.delivery_cost_per_kg:>14.2f}/kg
    TOTAL DELIVERED COST:          ${o.delivered_cost_per_kg:>14.2f}/kg

╚═══════════════════════════════════════════════════════════════════════════════╝
"""

    def print_summary(self):
        """Print formatted summary"""
        o = self._outputs
        inp = self.inputs
        
        print(f"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                    GH2 TUBE TRAILER DELIVERY TEA                              ║
╠═══════════════════════════════════════════════════════════════════════════════╣

  DEMAND & ROUTE
  ─────────────────────────────────────────────────────────────────────────────
    Station Demand:                {inp.station_demand_kg_day:>12,.0f} kg/day
    Delivery Distance:             {inp.delivery_distance_km:>12,.0f} km (one-way)
    Round Trip:                    {o.round_trip_km:>12,.0f} km
    Round Trip Time:               {o.round_trip_hours:>12.1f} hours

  FLEET
  ─────────────────────────────────────────────────────────────────────────────
    Trailer Type:                  {inp.trailer_type.value:>20}
    Trailer Capacity (usable):     {o.trailer_capacity_kg:>12,.0f} kg
    Trips per Day:                 {o.trips_per_day:>12.1f}
    Trips per Year:                {o.trips_per_year:>12,}
    Trailers Required:             {o.num_trailers_required:>12}
    Tractors Required:             {o.num_tractors_required:>12}

  ANNUAL COSTS
  ─────────────────────────────────────────────────────────────────────────────
    H2 Production:                 ${o.cost_h2_production:>14,.0f}
    Trailer ({'Lease' if inp.lease_trailer else 'Own'}):                  ${o.cost_trailer:>14,.0f}
    Tractor ({'Lease' if inp.lease_tractor else 'Own'}):                  ${o.cost_tractor:>14,.0f}
    Fuel:                          ${o.cost_fuel:>14,.0f}
    Driver:                        ${o.cost_driver:>14,.0f}
    Maintenance:                   ${o.cost_maintenance:>14,.0f}
    Terminal:                      ${o.cost_terminal:>14,.0f}
    Insurance:                     ${o.cost_insurance:>14,.0f}
    ─────────────────────────────────────────────────────────────────────────
    TOTAL ANNUAL:                  ${o.cost_total_annual:>14,.0f}

  COST BREAKDOWN ($/kg delivered)
  ─────────────────────────────────────────────────────────────────────────────""")
        
        for item, cost in o.cost_breakdown.items():
            print(f"    {item + ':':28} ${cost:>10.2f}/kg")
        
        print(f"""    ─────────────────────────────────────────────────────────────────────────
    DELIVERY COST (excl. H2):      ${o.delivery_cost_per_kg:>14.2f}/kg
    TOTAL DELIVERED COST:          ${o.delivered_cost_per_kg:>14.2f}/kg

╚═══════════════════════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    print("=" * 80)
    print("GH2 TUBE TRAILER DELIVERY TEA - 1000 kg/day Example")
    print("=" * 80)
    
    tea = GH2DeliveryTEA(GH2DeliveryInputs(
        station_demand_kg_day=1000,
        delivery_distance_km=80,
        trailer_type=TrailerType.COMPOSITE_250BAR,
        h2_production_cost=3.00,
    ))
    
    results = tea.calculate()
    tea.print_summary()
