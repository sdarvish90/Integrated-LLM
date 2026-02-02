class H2StorageModel:
    """
    A class representing a hydrogen (H2) storage with a fixed capacity.

    Attributes:
    -----------
    capacity : float
        The maximum amount of H2 that can be stored in the storage, in tonnes.
    current_storage : float, optional
        The current amount of H2 stored in the storage, in tonnes. Default is 0.

    Methods:
    --------
    charge(energy: float) -> float:
        Charge the H2 storage with the given energy, up to the capacity.

        Parameters:
        -----------
        H2_to_be_charged : float
            The amount of mass H2 to be stored in the H2 storage, in tonnes.

        Returns:
        --------
        float
            The actual amount of H2 that was stored in the H2 storage, in tonnes.

    discharge(energy: float) -> float:
        Discharge the H2 storage by the given mass to be discharged, down to 0.

        Parameters:
        -----------
        H2_to_be_discharged : float
            The amount of H2 to be extracted from the H2 storage, in tonnes.

        Returns:
        --------
        float
            The actual amount of H2 that was extracted from the H2 storage, in tonnes.
    """
    
    def __init__(self, capacity, current_storage=0):
        """
        Initialize a new H2Storage object.

        Parameters:
        -----------
        capacity : float
            The maximum amount of H2 that can be stored in the storage, in tonnes.
        current_storage : float, optional
            The current amount of H2 stored in the storage, in kg. Default is 0.
        """
        self.capacity = capacity
        self.current_storage = current_storage
    
    def charge(self, H2_to_be_charged):
        """
        Charge the H2 storage with the given energy, up to the capacity.

        Parameters:
        -----------
        H2_to_be_charged : float
            The amount of mass H2 to be stored in the H2 storage, in tonnes.

        Returns:
        --------
        float
            The actual amount of H2 that was stored in the H2 storage, in tonnes.
        """
        H2_charging = min(self.capacity - self.current_storage, H2_to_be_charged)
        self.current_storage += H2_charging
        return H2_charging
    
    def discharge(self, H2_to_be_discharged):
        """
       Discharge the H2 storage by the given mass to be discharged, down to 0.

        Parameters:
        -----------
        H2_to_be_discharged : float
            The amount of H2 to be extracted from the H2 storage, in tonnes.

        Returns:
        --------
        float
            The actual amount of H2 that was extracted from the H2 storage, in tonnes.
        """

        H2_discharging = min(self.current_storage, H2_to_be_discharged)
        self.current_storage -=H2_discharging
        return H2_discharging
