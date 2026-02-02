# WaterModel.py
#
# Computes volumetric water consumption (m3), wastewater treatment energy (MWh),
# and water costs (USD) for the electrolyser and Haber-Bosch subsystems.
#
# All functions accept pandas Series (hourly production) and scalar parameters,
# returning Series of the same length.


def compute_hourly_water(
    H2_prod_tonnes,
    NH3_prod_tonnes,
    elec_feed_water,
    elec_cooling_water,
    HB_cooling_water,
    waste_water_flowrate,
):
    """
    Compute hourly water volumes for each process stream.

    Parameters
    ----------
    H2_prod_tonnes : pd.Series
        Hourly H2 production (tonnes).
    NH3_prod_tonnes : pd.Series
        Hourly NH3 production (tonnes).
    elec_feed_water : float
        Electrolyser feed water consumption (m3/t H2).
    elec_cooling_water : float
        Electrolyser cooling water consumption (m3/t H2).
    HB_cooling_water : float
        Haber-Bosch cooling water consumption (m3/t NH3).
    waste_water_flowrate : float
        Wastewater produced by electrolysis (m3/t H2).

    Returns
    -------
    dict[str, pd.Series]
        Keys: elec_feed_water_m3, elec_cooling_water_m3,
              HB_cooling_water_m3, waste_water_m3, total_water_m3
    """
    elec_feed = H2_prod_tonnes * elec_feed_water
    elec_cool = H2_prod_tonnes * elec_cooling_water
    hb_cool = NH3_prod_tonnes * HB_cooling_water
    waste = H2_prod_tonnes * waste_water_flowrate

    total = elec_feed + elec_cool + waste + hb_cool

    return {
        "elec_feed_water_m3": elec_feed,
        "elec_cooling_water_m3": elec_cool,
        "HB_cooling_water_m3": hb_cool,
        "waste_water_m3": waste,
        "total_water_m3": total,
    }


def compute_water_energy(waste_water_m3, waste_water_cons):
    """
    Compute energy required for wastewater treatment.

    Parameters
    ----------
    waste_water_m3 : pd.Series
        Hourly wastewater volume (m3).
    waste_water_cons : float
        Energy intensity of wastewater treatment (kWh/m3).

    Returns
    -------
    pd.Series
        Hourly energy for wastewater treatment (MWh).
    """
    return waste_water_m3 * waste_water_cons / 1000.0


def compute_water_cost(total_water_m3, water_unit_cost):
    """
    Compute hourly water procurement cost.

    Parameters
    ----------
    total_water_m3 : pd.Series
        Hourly total water consumption (m3).
    water_unit_cost : float
        Unit cost of water (USD/m3).

    Returns
    -------
    pd.Series
        Hourly water cost (USD).
    """
    return total_water_m3 * water_unit_cost
