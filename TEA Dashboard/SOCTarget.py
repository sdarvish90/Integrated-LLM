import pandas as pd

class SOCTarget:
    """
    SOCTarget class computes the State of Charge (SOC) target based on the provided data.

    Parameters:
        data (pd.DataFrame): The input DataFrame containing the necessary data columns.
        forecast_window (int): The size of the rolling window for the forecast calculation.
        ramp_rates_buffer (float): The buffer factor for ramp rates in the SOC target computation.
        shortfall_column (str): The name of the column in the DataFrame representing the shortfall values.
        soc_target_column (str): The desired name for the SOC target column in the DataFrame.

    Attributes:
        data (pd.DataFrame): The input DataFrame containing the necessary data columns.
        forecast_window (int): The size of the rolling window for the forecast calculation.
        ramp_rates_buffer (float): The buffer factor for ramp rates in the SOC target computation.
        shortfall_column (str): The name of the column in the DataFrame representing the shortfall values.
        soc_target_column (str): The desired name for the SOC target column in the DataFrame.

    Methods:
        calculate_hx():
            Calculates the rolling sum for 'shortfall' column and adds it as 'hx' columns to the DataFrame.
            Returns a list of the 'hx' column names.

        compute_SOC_target():
            Computes the SOC target based on 'hx' columns and other parameters.
            Updates the SOC target column in the DataFrame with the specified name.
    """

    def __init__(self, data, forecast_window, ramp_rates_buffer, shortfall_column, soc_target_column='SOC_target'):
        self.data = data
        self.forecast_window = forecast_window
        self.ramp_rates_buffer = ramp_rates_buffer
        self.shortfall_column = shortfall_column
        self.soc_target_column = soc_target_column

    def calculate_hx(self):
        """
        Calculates the rolling sum for 'shortfall' column and adds it as 'hx' columns to the DataFrame.

        Returns:
            list: A list of the 'hx' column names.
        """
        for x in range(1, self.forecast_window + 1):
            h = pd.Series(self.data[self.shortfall_column].iloc[::-1].rolling(x).sum().iloc[::-1].shift(-1), index=self.data.index)
            self.data[f"h{x}"] = h.fillna(0)
        
        columns = [f"h{i}" for i in range(1, self.forecast_window + 1)]
        return columns

    def compute_SOC_target(self):
        """
        Computes the SOC target based on 'hx' columns and other parameters.
        Updates the SOC target column in the DataFrame with the specified name.
        """
        columns = self.calculate_hx()

        # Compute SOC target 
        self.data[self.soc_target_column] = self.data[columns].max(axis=1)
        self.data[self.soc_target_column] = self.data[self.soc_target_column].apply(lambda x: 0 if x < 0 else x) + (self.ramp_rates_buffer * self.data['max_loads'].max())

        # Drop the 'hx' columns
        self.data.drop(columns=columns, inplace=True)
