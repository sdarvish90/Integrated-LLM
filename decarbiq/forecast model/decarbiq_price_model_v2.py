#!/usr/bin/env python3
"""
DecarbIQ Electricity Price Model v2.0
=====================================
Comprehensive model for forecasting wholesale electricity prices
based on gas prices and other fundamentals.

Methods Implemented:
1. Multivariate OLS Regression (control for multiple factors)
2. Quantile Regression (P10/P50/P90 scenarios)
3. Regime Detection (normal vs scarcity)
4. Seasonal Decomposition
5. Time Series with Lags (autocorrelation)
6. Monte Carlo Simulation
7. Scarcity Event Modeling

Author: DecarbIQ / Eco Decarb Forward
Date: 2026-02-09
"""

import pandas as pd
import numpy as np
from scipy import stats
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# DATA LOADING AND PREPARATION
# =============================================================================

class DecarbIQDataLoader:
    """Load and prepare all data sources for modeling."""
    
    def __init__(self):
        self.ercot_wholesale = None
        self.caiso_wholesale = None
        self.monthly_data = None
        self.merged_ercot = None
        self.merged_caiso = None
        
    def load_all(self, 
                 ercot_path: str = '/mnt/user-data/uploads/ercot_wholesale_monthly.csv',
                 caiso_path: str = '/mnt/user-data/uploads/caiso_wholesale_monthly.csv',
                 monthly_path: str = '/home/claude/monthly_analysis_data.csv'):
        """Load all data sources."""
        
        # Load wholesale data
        self.ercot_wholesale = pd.read_csv(ercot_path)
        self.caiso_wholesale = pd.read_csv(caiso_path)
        
        # Load monthly fundamentals
        self.monthly_data = pd.read_csv(monthly_path, index_col=0, parse_dates=True)
        self.monthly_data['period'] = self.monthly_data.index.strftime('%Y-%m')
        
        # Merge datasets
        self.merged_ercot = self.ercot_wholesale.merge(
            self.monthly_data[['period', 'henry_hub', 'permian_gas', 'haynesville_gas', 
                              'appalachia_gas', 'us_storage', 'elec_power_demand', 'elec_tx']],
            on='period', how='inner'
        )
        
        self.merged_caiso = self.caiso_wholesale.merge(
            self.monthly_data[['period', 'henry_hub', 'permian_gas', 'haynesville_gas',
                              'appalachia_gas', 'us_storage', 'elec_power_demand', 'elec_ca']],
            on='period', how='inner'
        )
        
        # Add derived features
        self._add_features()
        
        return self
    
    def _add_features(self):
        """Add derived features for modeling."""
        
        for df in [self.merged_ercot, self.merged_caiso]:
            # Time features
            df['month_num'] = df['month']
            df['is_summer'] = df['month'].isin([6, 7, 8, 9]).astype(int)
            df['is_winter'] = df['month'].isin([12, 1, 2]).astype(int)
            
            # Lagged gas prices
            df['henry_hub_lag1'] = df['henry_hub'].shift(1)
            df['henry_hub_lag2'] = df['henry_hub'].shift(2)
            
            # Storage deviation from seasonal norm (simplified)
            df['storage_zscore'] = (df['us_storage'] - df['us_storage'].mean()) / df['us_storage'].std()
            
            # Price volatility (rolling std)
            df['price_volatility'] = df['avg_price_mwh'].rolling(3, min_periods=1).std()
        
        # Drop NaN rows from lagging
        self.merged_ercot = self.merged_ercot.dropna()
        self.merged_caiso = self.merged_caiso.dropna()


# =============================================================================
# SCARCITY EVENT DETECTION AND MODELING
# =============================================================================

class ScarcityEventModel:
    """
    Detect and model scarcity pricing events.
    
    Scarcity events are characterized by:
    - Prices > 3 standard deviations above mean
    - Often driven by weather extremes or infrastructure failures
    - Cannot be predicted by gas prices alone
    """
    
    def __init__(self):
        self.scarcity_threshold = None
        self.scarcity_events = []
        self.normal_data = None
        self.scarcity_data = None
        
    def fit(self, prices: pd.Series, periods: pd.Series, 
            threshold_std: float = 2.5) -> 'ScarcityEventModel':
        """
        Identify scarcity events using statistical threshold.
        
        Args:
            prices: Price series ($/MWh)
            periods: Period labels for identification
            threshold_std: Number of std devs above mean to flag as scarcity
        """
        # Calculate threshold using robust statistics (median + MAD)
        median_price = prices.median()
        mad = np.median(np.abs(prices - median_price))
        self.scarcity_threshold = median_price + threshold_std * 1.4826 * mad
        
        # Identify scarcity events
        is_scarcity = prices > self.scarcity_threshold
        
        self.scarcity_events = list(periods[is_scarcity].values)
        self.scarcity_prices = prices[is_scarcity].to_dict()
        
        # Split data
        self.normal_mask = ~is_scarcity
        
        return self
    
    def get_scarcity_summary(self) -> Dict:
        """Return summary of identified scarcity events."""
        return {
            'threshold_mwh': self.scarcity_threshold,
            'n_events': len(self.scarcity_events),
            'events': self.scarcity_events,
            'event_prices': self.scarcity_prices
        }
    
    def simulate_scarcity_risk(self, 
                               n_months: int = 12,
                               annual_probability: float = 0.15,
                               severity_distribution: str = 'historical') -> np.ndarray:
        """
        Simulate potential scarcity events for risk analysis.
        
        Args:
            n_months: Forecast horizon
            annual_probability: Probability of at least one scarcity event per year
            severity_distribution: 'historical' or 'parametric'
        
        Returns:
            Array of simulated scarcity adders ($/MWh)
        """
        # Monthly probability (assuming independence)
        monthly_prob = 1 - (1 - annual_probability) ** (1/12)
        
        # Simulate occurrence
        occurs = np.random.random(n_months) < monthly_prob
        
        # Simulate severity
        if severity_distribution == 'historical' and len(self.scarcity_events) > 0:
            # Sample from historical scarcity prices
            historical_severities = list(self.scarcity_prices.values())
            severities = np.random.choice(historical_severities, n_months)
        else:
            # Parametric: Log-normal for extreme events
            severities = np.random.lognormal(mean=5.5, sigma=1.0, size=n_months)
        
        return occurs * severities


# =============================================================================
# MULTIVARIATE REGRESSION MODEL
# =============================================================================

class MultivariateElectricityModel:
    """
    Multivariate OLS regression controlling for multiple factors.
    
    Y = β₀ + β₁(gas) + β₂(storage) + β₃(demand) + β₄(season) + ε
    """
    
    def __init__(self):
        self.coefficients = {}
        self.r_squared = None
        self.adj_r_squared = None
        self.residuals = None
        self.fitted_values = None
        self.feature_names = None
        
    def fit(self, df: pd.DataFrame, 
            y_col: str = 'avg_price_mwh',
            feature_cols: List[str] = None) -> 'MultivariateElectricityModel':
        """
        Fit multivariate OLS regression.
        
        Args:
            df: DataFrame with features and target
            y_col: Target variable column
            feature_cols: List of feature columns
        """
        if feature_cols is None:
            feature_cols = ['henry_hub', 'storage_zscore', 'is_summer', 'is_winter']
        
        self.feature_names = feature_cols
        
        # Prepare data
        y = df[y_col].values
        X = df[feature_cols].values
        
        # Add constant
        X = np.column_stack([np.ones(len(X)), X])
        
        # OLS: β = (X'X)⁻¹X'y
        XtX_inv = np.linalg.pinv(X.T @ X)
        self.beta = XtX_inv @ X.T @ y
        
        # Store coefficients
        self.coefficients['intercept'] = self.beta[0]
        for i, name in enumerate(feature_cols):
            self.coefficients[name] = self.beta[i + 1]
        
        # Calculate fit statistics
        self.fitted_values = X @ self.beta
        self.residuals = y - self.fitted_values
        
        ss_res = np.sum(self.residuals ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        
        self.r_squared = 1 - ss_res / ss_tot
        n, p = len(y), len(feature_cols) + 1
        self.adj_r_squared = 1 - (1 - self.r_squared) * (n - 1) / (n - p - 1)
        
        # Calculate standard errors and p-values
        mse = ss_res / (n - p)
        var_beta = mse * np.diag(XtX_inv)
        se_beta = np.sqrt(var_beta)
        t_stats = self.beta / se_beta
        self.p_values = 2 * (1 - stats.t.cdf(np.abs(t_stats), df=n-p))
        
        return self
    
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """Predict electricity prices."""
        X = df[self.feature_names].values
        X = np.column_stack([np.ones(len(X)), X])
        return X @ self.beta
    
    def summary(self) -> str:
        """Return model summary."""
        lines = [
            "=" * 60,
            "MULTIVARIATE REGRESSION RESULTS",
            "=" * 60,
            f"R²: {self.r_squared:.4f}",
            f"Adjusted R²: {self.adj_r_squared:.4f}",
            "",
            f"{'Variable':<20} {'Coefficient':>12} {'p-value':>10}",
            "-" * 45
        ]
        
        names = ['intercept'] + self.feature_names
        for i, name in enumerate(names):
            coef = self.beta[i]
            pval = self.p_values[i]
            sig = '***' if pval < 0.001 else '**' if pval < 0.01 else '*' if pval < 0.05 else ''
            lines.append(f"{name:<20} {coef:>12.4f} {pval:>10.4f} {sig}")
        
        return '\n'.join(lines)


# =============================================================================
# QUANTILE REGRESSION MODEL
# =============================================================================

class QuantileRegressionModel:
    """
    Quantile regression for P10/P50/P90 scenario forecasting.
    
    Instead of predicting the mean, predict specific percentiles
    for risk analysis.
    """
    
    def __init__(self, quantiles: List[float] = [0.10, 0.50, 0.90]):
        self.quantiles = quantiles
        self.models = {}
        
    def fit(self, X: np.ndarray, y: np.ndarray, 
            max_iter: int = 1000) -> 'QuantileRegressionModel':
        """
        Fit quantile regression using iterative reweighted least squares.
        
        Args:
            X: Feature matrix
            y: Target variable
            max_iter: Maximum iterations for convergence
        """
        # Add constant
        X = np.column_stack([np.ones(len(X)), X])
        
        for q in self.quantiles:
            # Initialize with OLS
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            
            # Iterative reweighted least squares
            for _ in range(max_iter):
                residuals = y - X @ beta
                
                # Asymmetric weights for quantile
                weights = np.where(residuals >= 0, q, 1 - q)
                weights = weights / (np.abs(residuals) + 1e-6)
                
                # Weighted least squares
                W = np.diag(weights)
                try:
                    beta_new = np.linalg.solve(X.T @ W @ X, X.T @ W @ y)
                except:
                    break
                
                # Check convergence
                if np.max(np.abs(beta_new - beta)) < 1e-6:
                    break
                beta = beta_new
            
            self.models[q] = beta
        
        return self
    
    def predict(self, X: np.ndarray) -> Dict[float, np.ndarray]:
        """Predict at each quantile."""
        X = np.column_stack([np.ones(len(X)), X])
        
        predictions = {}
        for q, beta in self.models.items():
            predictions[q] = X @ beta
        
        return predictions
    
    def get_scenario_ranges(self, X: np.ndarray) -> pd.DataFrame:
        """Get P10/P50/P90 scenarios as DataFrame."""
        preds = self.predict(X)
        
        return pd.DataFrame({
            'P10_low': preds.get(0.10, preds[min(self.quantiles)]),
            'P50_base': preds.get(0.50, preds[0.50]),
            'P90_high': preds.get(0.90, preds[max(self.quantiles)])
        })


# =============================================================================
# SEASONAL DECOMPOSITION
# =============================================================================

class SeasonalDecomposition:
    """
    Decompose price into trend, seasonal, and residual components.
    """
    
    def __init__(self, period: int = 12):
        self.period = period
        self.trend = None
        self.seasonal = None
        self.residual = None
        self.seasonal_factors = None
        
    def fit(self, prices: pd.Series) -> 'SeasonalDecomposition':
        """
        Perform additive seasonal decomposition.
        
        Args:
            prices: Time series of prices
        """
        # Moving average for trend
        self.trend = prices.rolling(window=self.period, center=True).mean()
        
        # Detrended series
        detrended = prices - self.trend
        
        # Seasonal factors (average by month)
        self.seasonal_factors = {}
        for month in range(1, 13):
            month_mask = prices.index.month == month
            self.seasonal_factors[month] = detrended[month_mask].mean()
        
        # Fill NaN with 0 for missing months
        for month in range(1, 13):
            if pd.isna(self.seasonal_factors.get(month)):
                self.seasonal_factors[month] = 0
        
        # Reconstruct seasonal component
        self.seasonal = pd.Series(
            [self.seasonal_factors.get(d.month, 0) for d in prices.index],
            index=prices.index
        )
        
        # Residual
        self.residual = prices - self.trend - self.seasonal
        
        return self
    
    def get_seasonal_adjustment(self, month: int) -> float:
        """Get seasonal adjustment factor for a given month."""
        return self.seasonal_factors.get(month, 0)


# =============================================================================
# MONTE CARLO SIMULATION
# =============================================================================

class MonteCarloSimulator:
    """
    Monte Carlo simulation for probabilistic price forecasting.
    """
    
    def __init__(self, n_simulations: int = 10000):
        self.n_simulations = n_simulations
        self.base_model = None
        self.residual_std = None
        self.scarcity_model = None
        
    def fit(self, model: MultivariateElectricityModel,
            scarcity_model: ScarcityEventModel = None) -> 'MonteCarloSimulator':
        """
        Fit simulator with base model and residual distribution.
        """
        self.base_model = model
        self.residual_std = np.std(model.residuals)
        self.scarcity_model = scarcity_model
        
        return self
    
    def simulate(self, 
                 df_forecast: pd.DataFrame,
                 gas_price_scenarios: Dict[str, float] = None,
                 include_scarcity: bool = True) -> pd.DataFrame:
        """
        Run Monte Carlo simulation for price forecasting.
        
        Args:
            df_forecast: DataFrame with forecast period features
            gas_price_scenarios: Dict with 'low', 'base', 'high' gas prices
            include_scarcity: Whether to include scarcity event risk
        
        Returns:
            DataFrame with simulation statistics
        """
        if gas_price_scenarios is None:
            gas_price_scenarios = {'low': 2.5, 'base': 3.5, 'high': 5.0}
        
        n_periods = len(df_forecast)
        results = []
        
        for scenario_name, gas_price in gas_price_scenarios.items():
            # Create forecast DataFrame with gas price
            df_sim = df_forecast.copy()
            df_sim['henry_hub'] = gas_price
            
            # Base prediction
            base_pred = self.base_model.predict(df_sim)
            
            # Monte Carlo simulations
            simulations = np.zeros((self.n_simulations, n_periods))
            
            for i in range(self.n_simulations):
                # Add residual noise
                noise = np.random.normal(0, self.residual_std, n_periods)
                simulations[i] = base_pred + noise
                
                # Add scarcity events
                if include_scarcity and self.scarcity_model is not None:
                    scarcity_adder = self.scarcity_model.simulate_scarcity_risk(n_periods)
                    simulations[i] += scarcity_adder
            
            # Calculate statistics
            for t in range(n_periods):
                period_sims = simulations[:, t]
                results.append({
                    'period': df_forecast['period'].iloc[t] if 'period' in df_forecast.columns else t,
                    'scenario': scenario_name,
                    'gas_price': gas_price,
                    'mean': np.mean(period_sims),
                    'std': np.std(period_sims),
                    'P10': np.percentile(period_sims, 10),
                    'P25': np.percentile(period_sims, 25),
                    'P50': np.percentile(period_sims, 50),
                    'P75': np.percentile(period_sims, 75),
                    'P90': np.percentile(period_sims, 90),
                    'P99': np.percentile(period_sims, 99),
                    'scarcity_prob': np.mean(period_sims > self.scarcity_model.scarcity_threshold) if self.scarcity_model else 0
                })
        
        return pd.DataFrame(results)


# =============================================================================
# MAIN DECARBIQ PRICE MODEL
# =============================================================================

class DecarbIQPriceModel:
    """
    Main class integrating all modeling components.
    """
    
    def __init__(self):
        self.data_loader = DecarbIQDataLoader()
        self.scarcity_model_ercot = ScarcityEventModel()
        self.scarcity_model_caiso = ScarcityEventModel()
        self.mv_model_ercot = MultivariateElectricityModel()
        self.mv_model_caiso = MultivariateElectricityModel()
        self.quantile_model_ercot = QuantileRegressionModel()
        self.quantile_model_caiso = QuantileRegressionModel()
        self.seasonal_ercot = SeasonalDecomposition()
        self.seasonal_caiso = SeasonalDecomposition()
        self.mc_simulator_ercot = MonteCarloSimulator()
        self.mc_simulator_caiso = MonteCarloSimulator()
        
        self.is_fitted = False
        
    def fit(self, 
            ercot_path: str = '/mnt/user-data/uploads/ercot_wholesale_monthly.csv',
            caiso_path: str = '/mnt/user-data/uploads/caiso_wholesale_monthly.csv',
            monthly_path: str = '/home/claude/monthly_analysis_data.csv') -> 'DecarbIQPriceModel':
        """
        Fit all model components.
        """
        print("="*70)
        print("DECARBIQ PRICE MODEL - FITTING")
        print("="*70)
        
        # Load data
        print("\n1. Loading data...")
        self.data_loader.load_all(ercot_path, caiso_path, monthly_path)
        
        # Fit scarcity models
        print("\n2. Fitting scarcity event models...")
        self.scarcity_model_ercot.fit(
            self.data_loader.merged_ercot['avg_price_mwh'],
            self.data_loader.merged_ercot['period']
        )
        self.scarcity_model_caiso.fit(
            self.data_loader.merged_caiso['avg_price_mwh'],
            self.data_loader.merged_caiso['period']
        )
        
        print(f"   ERCOT scarcity threshold: ${self.scarcity_model_ercot.scarcity_threshold:.0f}/MWh")
        print(f"   ERCOT scarcity events: {self.scarcity_model_ercot.scarcity_events}")
        print(f"   CAISO scarcity threshold: ${self.scarcity_model_caiso.scarcity_threshold:.0f}/MWh")
        print(f"   CAISO scarcity events: {self.scarcity_model_caiso.scarcity_events}")
        
        # Get normal data (excluding scarcity)
        ercot_normal = self.data_loader.merged_ercot[
            ~self.data_loader.merged_ercot['period'].isin(self.scarcity_model_ercot.scarcity_events)
        ]
        caiso_normal = self.data_loader.merged_caiso[
            ~self.data_loader.merged_caiso['period'].isin(self.scarcity_model_caiso.scarcity_events)
        ]
        
        # Fit multivariate models
        print("\n3. Fitting multivariate regression models...")
        feature_cols = ['henry_hub', 'storage_zscore', 'is_summer', 'is_winter']
        
        self.mv_model_ercot.fit(ercot_normal, 'avg_price_mwh', feature_cols)
        print(f"   ERCOT R²: {self.mv_model_ercot.r_squared:.3f}")
        
        self.mv_model_caiso.fit(caiso_normal, 'avg_price_mwh', feature_cols)
        print(f"   CAISO R²: {self.mv_model_caiso.r_squared:.3f}")
        
        # Fit quantile models
        print("\n4. Fitting quantile regression models...")
        X_ercot = ercot_normal[['henry_hub']].values
        y_ercot = ercot_normal['avg_price_mwh'].values
        self.quantile_model_ercot.fit(X_ercot, y_ercot)
        
        X_caiso = caiso_normal[['henry_hub']].values
        y_caiso = caiso_normal['avg_price_mwh'].values
        self.quantile_model_caiso.fit(X_caiso, y_caiso)
        print("   Quantile models fitted (P10/P50/P90)")
        
        # Fit seasonal decomposition
        print("\n5. Fitting seasonal decomposition...")
        # Need datetime index for seasonal
        ercot_ts = ercot_normal.set_index(pd.to_datetime(ercot_normal['period'] + '-01'))
        caiso_ts = caiso_normal.set_index(pd.to_datetime(caiso_normal['period'] + '-01'))
        
        if len(ercot_ts) >= 12:
            self.seasonal_ercot.fit(ercot_ts['avg_price_mwh'])
            print(f"   ERCOT seasonal factors: Summer={self.seasonal_ercot.get_seasonal_adjustment(7):.1f}, Winter={self.seasonal_ercot.get_seasonal_adjustment(1):.1f}")
        
        if len(caiso_ts) >= 12:
            self.seasonal_caiso.fit(caiso_ts['avg_price_mwh'])
            print(f"   CAISO seasonal factors: Summer={self.seasonal_caiso.get_seasonal_adjustment(7):.1f}, Winter={self.seasonal_caiso.get_seasonal_adjustment(1):.1f}")
        
        # Fit Monte Carlo simulators
        print("\n6. Fitting Monte Carlo simulators...")
        self.mc_simulator_ercot.fit(self.mv_model_ercot, self.scarcity_model_ercot)
        self.mc_simulator_caiso.fit(self.mv_model_caiso, self.scarcity_model_caiso)
        print(f"   ERCOT residual std: ${self.mc_simulator_ercot.residual_std:.1f}/MWh")
        print(f"   CAISO residual std: ${self.mc_simulator_caiso.residual_std:.1f}/MWh")
        
        self.is_fitted = True
        print("\n✅ Model fitting complete!")
        
        return self
    
    def predict_ercot(self, 
                      gas_price: float,
                      month: int = None,
                      storage_zscore: float = 0,
                      scenario: str = 'P50') -> float:
        """
        Predict ERCOT wholesale electricity price.
        
        Args:
            gas_price: Henry Hub price ($/MMBtu)
            month: Month (1-12) for seasonal adjustment
            storage_zscore: Storage deviation from normal (-2 to +2)
            scenario: 'P10', 'P50', 'P90', or 'mean'
        
        Returns:
            Predicted price ($/MWh)
        """
        if scenario == 'mean':
            # Use multivariate model
            df = pd.DataFrame({
                'henry_hub': [gas_price],
                'storage_zscore': [storage_zscore],
                'is_summer': [1 if month in [6,7,8,9] else 0],
                'is_winter': [1 if month in [12,1,2] else 0]
            })
            return self.mv_model_ercot.predict(df)[0]
        else:
            # Use quantile model
            X = np.array([[gas_price]])
            preds = self.quantile_model_ercot.predict(X)
            q_map = {'P10': 0.10, 'P50': 0.50, 'P90': 0.90}
            q = q_map.get(scenario, 0.50)
            base = preds[q][0]
            
            # Add seasonal adjustment
            if month and self.seasonal_ercot.seasonal_factors:
                base += self.seasonal_ercot.get_seasonal_adjustment(month)
            
            return max(0, base)
    
    def predict_caiso(self,
                      gas_price: float,
                      month: int = None,
                      storage_zscore: float = 0,
                      scenario: str = 'P50') -> float:
        """Predict CAISO wholesale electricity price."""
        if scenario == 'mean':
            df = pd.DataFrame({
                'henry_hub': [gas_price],
                'storage_zscore': [storage_zscore],
                'is_summer': [1 if month in [6,7,8,9] else 0],
                'is_winter': [1 if month in [12,1,2] else 0]
            })
            return self.mv_model_caiso.predict(df)[0]
        else:
            X = np.array([[gas_price]])
            preds = self.quantile_model_caiso.predict(X)
            q_map = {'P10': 0.10, 'P50': 0.50, 'P90': 0.90}
            q = q_map.get(scenario, 0.50)
            base = preds[q][0]
            
            if month and self.seasonal_caiso.seasonal_factors:
                base += self.seasonal_caiso.get_seasonal_adjustment(month)
            
            return max(0, base)
    
    def get_model_summary(self) -> str:
        """Return comprehensive model summary."""
        lines = [
            "="*70,
            "DECARBIQ PRICE MODEL SUMMARY",
            "="*70,
            "",
            "ERCOT (TEXAS)",
            "-"*40,
            self.mv_model_ercot.summary(),
            "",
            f"Scarcity Events: {self.scarcity_model_ercot.scarcity_events}",
            f"Scarcity Threshold: ${self.scarcity_model_ercot.scarcity_threshold:.0f}/MWh",
            "",
            "CAISO (CALIFORNIA)",
            "-"*40,
            self.mv_model_caiso.summary(),
            "",
            f"Scarcity Events: {self.scarcity_model_caiso.scarcity_events}",
            f"Scarcity Threshold: ${self.scarcity_model_caiso.scarcity_threshold:.0f}/MWh",
        ]
        
        return '\n'.join(lines)
    
    def export_parameters(self) -> Dict:
        """Export model parameters for use in DecarbIQ tool."""
        return {
            'ercot': {
                'multivariate': {
                    'coefficients': self.mv_model_ercot.coefficients,
                    'r_squared': self.mv_model_ercot.r_squared,
                },
                'quantile': {
                    q: list(beta) for q, beta in self.quantile_model_ercot.models.items()
                },
                'scarcity': self.scarcity_model_ercot.get_scarcity_summary(),
                'seasonal': self.seasonal_ercot.seasonal_factors,
                'residual_std': self.mc_simulator_ercot.residual_std
            },
            'caiso': {
                'multivariate': {
                    'coefficients': self.mv_model_caiso.coefficients,
                    'r_squared': self.mv_model_caiso.r_squared,
                },
                'quantile': {
                    q: list(beta) for q, beta in self.quantile_model_caiso.models.items()
                },
                'scarcity': self.scarcity_model_caiso.get_scarcity_summary(),
                'seasonal': self.seasonal_caiso.seasonal_factors,
                'residual_std': self.mc_simulator_caiso.residual_std
            }
        }


# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    
    # Initialize and fit model
    model = DecarbIQPriceModel()
    model.fit()
    
    # Print summary
    print("\n" + model.get_model_summary())
    
    # Example predictions
    print("\n" + "="*70)
    print("EXAMPLE PREDICTIONS")
    print("="*70)
    
    gas_scenarios = [2.5, 3.5, 5.0, 7.0]
    
    print("\nERCOT Wholesale ($/MWh):")
    print(f"{'Gas Price':<12} {'P10':>10} {'P50':>10} {'P90':>10} {'Mean':>10}")
    print("-"*55)
    for gas in gas_scenarios:
        p10 = model.predict_ercot(gas, month=7, scenario='P10')
        p50 = model.predict_ercot(gas, month=7, scenario='P50')
        p90 = model.predict_ercot(gas, month=7, scenario='P90')
        mean = model.predict_ercot(gas, month=7, scenario='mean')
        print(f"${gas:<11.2f} ${p10:>9.1f} ${p50:>9.1f} ${p90:>9.1f} ${mean:>9.1f}")
    
    print("\nCAISO Wholesale ($/MWh):")
    print(f"{'Gas Price':<12} {'P10':>10} {'P50':>10} {'P90':>10} {'Mean':>10}")
    print("-"*55)
    for gas in gas_scenarios:
        p10 = model.predict_caiso(gas, month=7, scenario='P10')
        p50 = model.predict_caiso(gas, month=7, scenario='P50')
        p90 = model.predict_caiso(gas, month=7, scenario='P90')
        mean = model.predict_caiso(gas, month=7, scenario='mean')
        print(f"${gas:<11.2f} ${p10:>9.1f} ${p50:>9.1f} ${p90:>9.1f} ${mean:>9.1f}")
    
    # Export parameters
    params = model.export_parameters()
    
    print("\n" + "="*70)
    print("EXPORTED MODEL PARAMETERS")
    print("="*70)
    
    import json
    print(json.dumps(params, indent=2, default=str))
    
    # Save to file
    with open('/home/claude/decarbiq_model_params.json', 'w') as f:
        json.dump(params, f, indent=2, default=str)
    
    print("\n✅ Parameters saved to decarbiq_model_params.json")
