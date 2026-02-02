"""
Economics subpackage - Techno-Economic Analysis modules.

Provides:
1. Simple TEA - Quick calculations for TriGen
2. Detailed TEA - Granular component-level analysis for TriGen
3. Unified TEA - SHARE model compatible framework for all systems
4. Electrolyzer TEA - Detailed PEM/Alkaline/SOEC analysis
5. TriGen Integrated TEA - MCFC analysis with green H2 comparison
6. Scenario Optimizer - Multi-dimensional sensitivity and optimization
7. Solar TEA - Utility-scale and distributed PV analysis
8. Wind TEA - Onshore and offshore wind analysis
9. BESS TEA - Battery storage with operational model
10. H2 Station TEA - GH2 dispensing station (compressor, storage, chiller, dispenser)
11. GH2 Delivery TEA - Tube trailer delivery costs
12. LH2 Delivery TEA - LH2 delivery + station (tank, pump, vaporizer, dispenser)
"""

from .trigen_tea import TriGenTEA, TriGenTEAOutputs, TriGenCostInputs, run_trigen_tea_from_mass_balance
from .trigen_tea_detailed import TriGenDetailedTEA, TriGenDetailedTEAOutputs, TriGenCapexInputs, TriGenOpexInputs, TriGenRevenueInputs, TriGenFinancingInputs, TriGenEmissionsInputs, DepreciationMethod, CostAllocationMethod
from .unified_tea import UnifiedTEA, TEAResults, GeneralInputs, SolarInputs, WindInputs, BESSInputs, ElectrolyzerInputs, H2StorageInputs, H2CompressionInputs, H2BOPInputs, HaberBoschInputs, NH3StorageInputs, GridInputs, WaterInputs, RevenueInputs, TriGenInputs, ElectrolyzerType, SCALING_FACTORS, REFERENCE_DATA
from .electrolyzer_tea import ElectrolyzerTEA, ElectrolyzerTEAOutputs, ElectrolyzerCapexInputs, ElectrolyzerOpexInputs, ElectrolyzerPerformanceInputs, ElectrolyzerFinancialInputs, ElectrolyzerTechnology, ElectrolyzerTechSpecs
from .trigen_tea_integrated import TriGenIntegratedTEA, TriGenTEAOutputs as TriGenIntegratedOutputs, TriGenSystemInputs, TriGenModel
from .scenario_optimizer import TEAScenarioOptimizer, OptimizationConfig, OptimizationResults, OptimizationMetric, ScenarioResult, ScenarioStatus
from .solar_tea import SolarTEA, SolarTEAOutputs, SolarCapexInputs, SolarOpexInputs, SolarPerformanceInputs, SolarFinancialInputs, SolarConfiguration, SolarModuleType
from .wind_tea import WindTEA, WindTEAOutputs, WindCapexInputs, WindOpexInputs, WindPerformanceInputs, WindFinancialInputs, WindType, TurbineClass
from .bess_tea import BESSTEA, BESSTEAOutputs, BESSCapexInputs, BESSOpexInputs, BESSPerformanceInputs, BESSRevenueInputs, BESSFinancialInputs, BESSOperationalModel, BatteryTechnology, BatteryTechSpecs, BESSApplication
from .h2_station_tea import H2StationTEA, H2StationTEAOutputs, H2StationCapexInputs, H2StationOpexInputs, H2StationPerformanceInputs, H2StationFinancialInputs, StationCapacity, DispenserType
from .gh2_delivery_tea import GH2DeliveryTEA, GH2DeliveryInputs, GH2DeliveryOutputs, TrailerType, TrailerSpecs
from .lh2_delivery_tea import LH2DeliveryTEA, LH2DeliveryInputs, LH2StationInputs, LH2PathwayOutputs, LH2TankerSize, LH2TankerSpecs

__all__ = [
    # TriGen
    'TriGenTEA', 'TriGenTEAOutputs', 'TriGenCostInputs', 'run_trigen_tea_from_mass_balance',
    'TriGenDetailedTEA', 'TriGenDetailedTEAOutputs', 'TriGenCapexInputs', 'TriGenOpexInputs',
    'TriGenRevenueInputs', 'TriGenFinancingInputs', 'TriGenEmissionsInputs',
    'DepreciationMethod', 'CostAllocationMethod',
    'TriGenIntegratedTEA', 'TriGenIntegratedOutputs', 'TriGenSystemInputs', 'TriGenModel',
    # Unified
    'UnifiedTEA', 'TEAResults', 'GeneralInputs', 'SolarInputs', 'WindInputs', 'BESSInputs',
    'ElectrolyzerInputs', 'H2StorageInputs', 'H2CompressionInputs', 'H2BOPInputs',
    'HaberBoschInputs', 'NH3StorageInputs', 'GridInputs', 'WaterInputs', 'RevenueInputs',
    'TriGenInputs', 'ElectrolyzerType', 'SCALING_FACTORS', 'REFERENCE_DATA',
    # Electrolyzer
    'ElectrolyzerTEA', 'ElectrolyzerTEAOutputs', 'ElectrolyzerCapexInputs',
    'ElectrolyzerOpexInputs', 'ElectrolyzerPerformanceInputs', 'ElectrolyzerFinancialInputs',
    'ElectrolyzerTechnology', 'ElectrolyzerTechSpecs',
    # Optimizer
    'TEAScenarioOptimizer', 'OptimizationConfig', 'OptimizationResults',
    'OptimizationMetric', 'ScenarioResult', 'ScenarioStatus',
    # Solar
    'SolarTEA', 'SolarTEAOutputs', 'SolarCapexInputs', 'SolarOpexInputs',
    'SolarPerformanceInputs', 'SolarFinancialInputs', 'SolarConfiguration', 'SolarModuleType',
    # Wind
    'WindTEA', 'WindTEAOutputs', 'WindCapexInputs', 'WindOpexInputs',
    'WindPerformanceInputs', 'WindFinancialInputs', 'WindType', 'TurbineClass',
    # BESS
    'BESSTEA', 'BESSTEAOutputs', 'BESSCapexInputs', 'BESSOpexInputs',
    'BESSPerformanceInputs', 'BESSRevenueInputs', 'BESSFinancialInputs',
    'BESSOperationalModel', 'BatteryTechnology', 'BatteryTechSpecs', 'BESSApplication',
    # H2 Station
    'H2StationTEA', 'H2StationTEAOutputs', 'H2StationCapexInputs', 'H2StationOpexInputs',
    'H2StationPerformanceInputs', 'H2StationFinancialInputs', 'StationCapacity', 'DispenserType',
    # GH2 Delivery
    'GH2DeliveryTEA', 'GH2DeliveryInputs', 'GH2DeliveryOutputs', 'TrailerType', 'TrailerSpecs',
    # LH2 Delivery
    'LH2DeliveryTEA', 'LH2DeliveryInputs', 'LH2StationInputs', 'LH2PathwayOutputs',
    'LH2TankerSize', 'LH2TankerSpecs',
]
