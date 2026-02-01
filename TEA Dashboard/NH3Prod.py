import numpy as np
class NH3Prod:
    """
    NH3Prod class manages ammonia (NH3) production-related parameters and computations.

    Attributes:
        NH3_ramp_up_rate_MWh (float): Maximum rate of increase for ammonia production in MWh per hour.
        NH3_ramp_down_rate_MWh (float): Maximum rate of decrease for ammonia production in MWh per hour.
        max_loads (float): Maximum permissible load in MWh.
        min_loads (float): Minimum permissible load in MWh.
        NH3_target (float): Target ammonia production rate in MWh.
        current_max_loads (float): Current computed maximum load considering ramp-up and constraints.
        current_min_loads (float): Current computed minimum load considering ramp-down and constraints.
        current_NH3_target (float): Current computed NH3 production target within constraints.

    Methods:
        __init__(NH3_ramp_up_rate_MWh, NH3_ramp_down_rate_MWh, max_loads, min_loads, NH3_target):
            Initializes a new NH3Prod instance with the specified parameters and computes initial values.

        update(NH3_prod, new_NH3_potential):
            Updates the NH3Prod instance's properties based on the provided NH3_prod and new_NH3_potential values.
        
        calculate_HB_energy_consumption(loading_value, df):
            Calculate interpolated energy consumption using loading values and corresponding energy values.
            
        calculate_H2_ratio(electrolyser_consumption, H2_to_NH3, HB_consumption):
            Calculate the ratio of hydrogen (H2) production to total hydrogen demand.
        compute_augmentation_cost(year_of_aug, project_lifetime, capex_epc, capacity, aug_cost, inflation, discount_rate):
            Compute the total augmentation cost over a project lifetime using the provided formula.
        shortfall_event(sequ):
            Returns the number of times a sequence of consecutive 1s or a single 1 occurs in the input sequence.

    """
    def __init__(self, NH3_ramp_up_rate_MWh, NH3_ramp_down_rate_MWh, max_loads, min_loads, NH3_target):
        """
        Initializes a new NH3Prod instance.

        Args:
            NH3_ramp_up_rate_MWh (float): Maximum rate of increase for ammonia production in MWh per hour.
            NH3_ramp_down_rate_MWh (float): Maximum rate of decrease for ammonia production in MWh per hour.
            max_loads (float): Maximum permissible load in MWh.
            min_loads (float): Minimum permissible load in MWh.
            NH3_target (float): Target ammonia production rate in MWh.
        """
        self.NH3_ramp_up_rate_MWh = NH3_ramp_up_rate_MWh
        self.NH3_ramp_down_rate_MWh = NH3_ramp_down_rate_MWh
        self.max_loads = max_loads
        self.min_loads = min_loads
        self.NH3_target = NH3_target
        # Compute initial values for max loads, min loads, and NH3 target
        self.current_max_loads = max_loads
        self.current_min_loads = min_loads
        self.current_NH3_target = NH3_target

    def update(self, NH3_prod, new_NH3_potential):
        """
        Updates the NH3Prod instance's properties based on the provided NH3_prod and new_NH3_potential values.

        Args:
            NH3_prod (float): Current ammonia production rate in MWh.
            new_NH3_potential (float): the NH3 potential added to it the excess of BESS and H2 storage SOC compared to their SOC target in that hour in MWh.
        """
        # Update the current_max_loads, current_min_loads, and current_NH3_target properties
        self.current_max_loads = max(self.min_loads, min(self.max_loads, NH3_prod + self.NH3_ramp_up_rate_MWh))
        self.current_min_loads = max(self.min_loads, NH3_prod - self.NH3_ramp_down_rate_MWh)
        self.current_NH3_target = max(self.current_min_loads, min(self.current_max_loads, new_NH3_potential))

    def calculate_HB_energy_consumption(loading_value, df):
        

        if hasattr(df, "columns"):
            #print("HB_consumption_profile columns:", [str(c) for c in df.columns], flush=True)
            df = df.copy()
            df.columns = [str(c).strip() for c in df.columns]
        else:
            #print("HB_consumption_profile (raw):", df, flush=True)
            import pandas as pd
            df = pd.DataFrame(df)
            df.columns = [str(c).strip() for c in df.columns]

        # Accept common column name variants
        loading_col_candidates = [
            "Loading_[%]", "Loading_%", "Loading", "loading", "Loading [%]"
        ]
        hb_col_candidates = [
            "HB_Consumption_[MWh/t]", "HB_Consumption",
            "HB Consumption [MWh/t]", "HB_Consumption_MWh_per_t"
        ]

        loading_col = next((c for c in loading_col_candidates if c in df.columns), None)
        hb_col = next((c for c in hb_col_candidates if c in df.columns), None)

        if loading_col is None or hb_col is None:
            raise KeyError(
                f"HB consumption profile missing required columns. Found: {list(df.columns)}"
            )

        loading_values = df[loading_col].astype(float).values
        energy_values = df[hb_col].astype(float).values

        return float(np.interp(float(loading_value), loading_values, energy_values))


    
    def calculate_H2_ratio(electrolyser_consumption, H2_to_NH3, HB_consumption):
        """
        Calculate the ratio of hydrogen (H2) production to total plant demand.

        This function calculates the ratio of hydrogen production to the total Plant demand, given the
        consumption values of an electrolyser, the H2-to-NH3 ratio, and the consumption of the Haber-Bosch (HB)
        process.

        Parameters:
        electrolyser_consumption (float): Hydrogen consumption by the electrolyser in some unit.
        H2_to_NH3 (float): Ratio of hydrogen (H2) to ammonia (NH3) in molar terms.
        HB_consumption (float): Hydrogen consumption by the Haber-Bosch process in some unit.

        Returns:
        float: The calculated ratio of hydrogen production to total hydrogen demand.

        """

        H2_ratio = (electrolyser_consumption * H2_to_NH3) / (electrolyser_consumption * H2_to_NH3 + HB_consumption)
        return H2_ratio
    
    def compute_augmentation_cost(self, year_of_aug, project_lifetime, capex_epc, capacity, aug_cost, inflation, discount_rate):
        """
        Compute the total augmentation cost over a project lifetime using the provided formula.

        Args:
            year_of_aug (int): The interval (in years) between augmentations.
            project_lifetime (int): Total number of years for the project.
            capex_epc (float): Capital expenditure (EPC).
            capacity (float): Capacity.
            aug_cost (float): Augmentation cost.
            inflation (float): Inflation rate.
            discount_rate (float): Discount rate.

        Returns:
            float: Total augmentation cost over the project lifetime.
        """
        total_cost = 0
        for year in range(1, project_lifetime + 1, year_of_aug):
            refurb_cost = capex_epc * capacity * aug_cost * 1000 * (1 + inflation) / (1 + discount_rate) ** year
            total_cost += refurb_cost
        return total_cost
    
    def shortfall_event(self, sequ):
        """
        Returns the number of times a sequence of consecutive 1s or a single 1 occurs in the input sequence.

        Parameters:
            sequ (list[int]): A sequence of 0s and 1s representing a binary signal.

        Returns:
            int: The number of times a sequence of consecutive 1s or a single 1 occurs in the input sequence.

        """
        seq_flag = False
        seq_count = 0
        for i in range(len(sequ) - 1):
            if sequ[i] == sequ[i + 1] == 1 or sequ[i] == 1:
                if not seq_flag:
                    seq_count += 1
                seq_flag = True
            else:
                seq_flag = False
        return seq_count

