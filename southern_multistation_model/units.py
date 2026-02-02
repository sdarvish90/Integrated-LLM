"""
Unit handling for Station Model using Pint library.
Keeps Excel-compatible units for v1, with conversion utilities.
"""

import pint

# Create unit registry
ureg = pint.UnitRegistry()
Q_ = ureg.Quantity

# Define custom units for energy industry
ureg.define('scf = 0.0283168 * meter**3')  # Standard cubic foot
ureg.define('GGE = 114 * scf')  # Gallon gasoline equivalent in scf of NG
ureg.define('DGE = 128.7 * scf')  # Gallon diesel equivalent in scf of NG
ureg.define('kW_th = kilowatt')  # Thermal kilowatt (alias for clarity)
ureg.define('kW_e = kilowatt')  # Electrical kilowatt (alias for clarity)

# Common unit definitions used in the model
UNITS = {
    # Mass flow
    'kg_per_h': ureg.kg / ureg.hour,
    'kg_per_day': ureg.kg / ureg.day,
    'kg_per_s': ureg.kg / ureg.second,
    
    # Volume flow
    'scf_per_h': ureg.scf / ureg.hour,
    'scf_per_day': ureg.scf / ureg.day,
    'm3_per_h': ureg.m**3 / ureg.hour,
    
    # Energy
    'kWh': ureg.kWh,
    'kWh_per_day': ureg.kWh / ureg.day,
    'kW': ureg.kW,
    'kW_th': ureg.kW,  # Thermal
    'kW_e': ureg.kW,   # Electrical
    'BTU': ureg.BTU,
    'BTU_per_scf': ureg.BTU / ureg.scf,
    'kJ': ureg.kJ,
    'MJ': ureg.MJ,
    
    # Pressure
    'bar': ureg.bar,
    'psi': ureg.psi,
    'kPa': ureg.kPa,
    
    # Temperature
    'degC': ureg.degC,
    'K': ureg.K,
    
    # Specific energy
    'kWh_per_kg': ureg.kWh / ureg.kg,
    'kJ_per_kg': ureg.kJ / ureg.kg,
    'kJ_per_kgK': ureg.kJ / (ureg.kg * ureg.K),
    
    # Molecular weight
    'kg_per_kmol': ureg.kg / ureg.kmol,
}


def convert(value, from_unit, to_unit):
    """
    Convert a value from one unit to another.
    
    Args:
        value: Numeric value or Pint Quantity
        from_unit: Source unit (string or pint unit)
        to_unit: Target unit (string or pint unit)
    
    Returns:
        Converted value as float (magnitude only)
    """
    if isinstance(value, pint.Quantity):
        return value.to(to_unit).magnitude
    else:
        q = Q_(value, from_unit)
        return q.to(to_unit).magnitude


def with_unit(value, unit):
    """
    Attach a unit to a value.
    
    Args:
        value: Numeric value
        unit: Unit string or pint unit
    
    Returns:
        Pint Quantity
    """
    return Q_(value, unit)


# Conversion constants (for quick reference, matching Excel)
CONVERSIONS = {
    'NG_m3_per_scf': 0.028317,  # m3/scf
    'NG_m3_per_kmol_STP': 22.414,  # m3/kmol at STP
    'bar_to_kPa': 100,  # kPa/bar
    'BTU_hr_per_kW': 3412,  # BTU/h per kW
    'kg_per_scf_CH4': 0.02007,  # kg/scf for methane (ρCH4)
}
