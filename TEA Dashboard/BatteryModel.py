import numpy as np

class BatteryModel:
    """
    A class representing a Battery Energy Storage System (BESS).

    Attributes:
    -----------
    capacity : float
        Maximum capacity of the BESS in kWh.
    max_power : float
        Maximum charging or discharging power of the BESS in kW.
    current_energy : float
        current energy that can be stored in the BESS in kWh.
    current_soc : float
        Current state of charge (SOC) of the BESS, expressed as a fraction of `capacity`.
    efficiency : float
        Round trip efficiency of the BESS in percentage.
    ac_losses : float
        AC losses of the BESS in percentage.
    degradation_rate : float
        Annual degradation rate of the BESS in percentage.

    Methods:
    --------
    charge(energy_to_charged) -> float:
        Charges the BESS with the specified energy.
        Returns the actual energy charged in MWh.

    discharge(energy_to_be_discharged) -> float:
        Discharges the BESS with the specified energy.
        Returns the actual energy discharged in MWh.
       degrade() -> None:
        Degrades the BESS by the specified degradation rate.
    """
    
    def __init__(self, capacity, max_power, current_soc, current_energy, efficiency, ac_losses, degradation_rate):
        self.capacity = capacity  # Maximum capacity of the BESS
        self.max_power = max_power  # Maximum charging power of the BESS
        self.current_energy = current_energy
        self.current_soc = current_soc  # Current state of charge (SOC) of the BESS
        self.efficiency = efficiency    # round trip efficiency
        self.ac_losses = ac_losses      # ac_losses
        self.degradation_rate = degradation_rate  # Annual degradation rate

    def charge(self, energy_to_be_charged):
        # Charge efficiency = sqrt(RTE)
        charge_eff = self.efficiency ** 0.5

        BESS_charging = min(
            energy_to_be_charged,
            (self.capacity - self.current_energy) / charge_eff,
            self.max_power
        )

        self.current_energy += BESS_charging * charge_eff
        self.current_soc = self.current_energy / self.capacity
        return BESS_charging

    def discharge(self, energy_to_be_discharged):
        # Discharge efficiency = sqrt(RTE)
        discharge_eff = self.efficiency ** 0.5
        
        # Max output considering efficiency
        max_output = self.current_energy * discharge_eff
        
        BESS_discharging = min(
            energy_to_be_discharged,
            self.max_power,
            max_output
        )
        
        # Energy removed from storage
        self.current_energy -= BESS_discharging / discharge_eff
        self.current_soc = self.current_energy / self.capacity
        return BESS_discharging

    def discharge(self, energy_to_be_discharged):
        """
        Discharges the BESS with the specified load.
        Returns the actual discharging energy including losses.
        """
       
        # Limit the discharging to the maximum energy or power
        BESS_discharging = min(energy_to_be_discharged, self.max_power * (1 - self.ac_losses), self.current_energy)


        # Update the BESS energy and SOC
        #self.current_energy = max(0, self.current_energy -  BESS_discharging / ((self.efficiency) ** (1 / 2) - self.ac_losses))
        self.current_energy = max(0, self.current_energy - BESS_discharging / ((1 - self.ac_losses) * (self.efficiency ** (1 / 2))))
        self.current_soc = self.current_energy / self.capacity

        # Return the actual discharging energy
        return BESS_discharging
    
    
    def degrade(self, annual_degradation):
        """
        Applies an annual degradation to the BESS capacity.
        """
        self.capacity *= (1 - annual_degradation)
        self.current_energy = min(self.current_energy, self.capacity)
        self.current_soc = self.current_energy / self.capacity



