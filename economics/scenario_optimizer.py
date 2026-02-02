"""
TEA Scenario Optimizer Module

Provides multi-dimensional sensitivity analysis and scenario optimization for:
- Natural gas price variations
- Electricity price variations  
- H2 selling price variations
- Multi-product revenue optimization

Features:
- Parameter sweeps (1D, 2D, 3D)
- Scenario ranking by NPV, IRR, LCOH, payback
- Breakeven analysis
- Monte Carlo simulation
- Optimal scenario identification
- Export to DataFrame/Excel

Compatible with all TEA modules (Electrolyzer, TriGen, Unified).
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Callable, Any, Union
from enum import Enum
import numpy as np
import itertools
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings


class OptimizationMetric(Enum):
    """Metrics for scenario optimization/ranking"""
    NPV = "npv"
    IRR = "irr"
    LCOH = "lcoh"
    LCOE = "lcoe"
    LCOA = "lcoa"
    PAYBACK = "payback"
    PROFIT_MARGIN = "profit_margin"
    ROI = "roi"


class ScenarioStatus(Enum):
    """Scenario viability status"""
    OPTIMAL = "optimal"
    VIABLE = "viable"
    MARGINAL = "marginal"
    UNVIABLE = "unviable"


@dataclass
class ScenarioResult:
    """Results from a single scenario run"""
    
    # Input parameters
    scenario_id: int = 0
    scenario_name: str = ""
    
    # Key input variables
    ng_price: float = 0          # $/MMBtu
    electricity_price: float = 0  # $/MWh or $/kWh
    h2_selling_price: float = 0   # $/kg
    h2_production_cost: float = 0 # $/kg (LCOH)
    
    # Additional parameters (for multi-dimensional)
    capacity_factor: float = 0
    capex_multiplier: float = 1.0
    
    # Output metrics
    npv: float = 0
    irr: float = 0
    lcoh: float = 0
    lcoe: float = 0
    payback: float = 0
    
    # Derived metrics
    profit_margin: float = 0      # $/kg (selling price - LCOH)
    annual_profit: float = 0      # $/year
    roi: float = 0                # Return on investment
    
    # Production
    annual_h2_kg: float = 0
    annual_electricity_MWh: float = 0
    
    # Status
    status: ScenarioStatus = ScenarioStatus.VIABLE
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            'scenario_id': self.scenario_id,
            'scenario_name': self.scenario_name,
            'ng_price_$/MMBtu': self.ng_price,
            'elec_price_$/MWh': self.electricity_price,
            'h2_sell_price_$/kg': self.h2_selling_price,
            'LCOH_$/kg': self.lcoh,
            'LCOE_$/MWh': self.lcoe,
            'profit_margin_$/kg': self.profit_margin,
            'NPV_$': self.npv,
            'IRR_%': self.irr * 100 if self.irr else 0,
            'payback_years': self.payback,
            'annual_H2_kg': self.annual_h2_kg,
            'status': self.status.value,
        }


@dataclass
class OptimizationConfig:
    """Configuration for scenario optimization"""
    
    # Parameter ranges
    ng_price_range: Tuple[float, float, int] = (2.0, 8.0, 7)      # (min, max, steps)
    electricity_price_range: Tuple[float, float, int] = (20, 80, 7)  # $/MWh
    h2_price_range: Tuple[float, float, int] = (3.0, 10.0, 8)     # $/kg
    
    # Fixed parameters (if not sweeping)
    fixed_ng_price: Optional[float] = None
    fixed_electricity_price: Optional[float] = None
    fixed_h2_price: Optional[float] = None
    
    # Optimization settings
    optimization_metric: OptimizationMetric = OptimizationMetric.NPV
    minimize: bool = False  # True for LCOH/payback, False for NPV/IRR
    
    # Viability thresholds
    min_irr: float = 0.08          # 8% minimum IRR
    max_payback: float = 10.0      # 10 years max payback
    max_lcoh: float = 10.0         # $10/kg max LCOH
    min_npv: float = 0             # Positive NPV required
    
    # Monte Carlo settings
    monte_carlo_iterations: int = 1000
    ng_price_std: float = 1.0      # Std dev for MC
    elec_price_std: float = 10.0
    h2_price_std: float = 1.0
    
    # Parallel processing
    parallel: bool = True
    max_workers: int = 4


@dataclass 
class OptimizationResults:
    """Complete optimization results"""
    
    # All scenarios
    scenarios: List[ScenarioResult] = field(default_factory=list)
    
    # Best scenarios
    optimal_scenario: Optional[ScenarioResult] = None
    top_scenarios: List[ScenarioResult] = field(default_factory=list)
    
    # Statistics
    total_scenarios: int = 0
    viable_scenarios: int = 0
    optimal_scenarios: int = 0
    
    # Sensitivity results
    ng_sensitivity: Dict[float, float] = field(default_factory=dict)
    elec_sensitivity: Dict[float, float] = field(default_factory=dict)
    h2_sensitivity: Dict[float, float] = field(default_factory=dict)
    
    # Breakeven points
    breakeven_ng_price: Optional[float] = None
    breakeven_elec_price: Optional[float] = None
    breakeven_h2_price: Optional[float] = None
    
    # Monte Carlo results
    mc_npv_mean: float = 0
    mc_npv_std: float = 0
    mc_npv_p10: float = 0
    mc_npv_p50: float = 0
    mc_npv_p90: float = 0
    mc_probability_positive_npv: float = 0
    
    def to_dataframe(self):
        """Convert results to pandas DataFrame"""
        try:
            import pandas as pd
            return pd.DataFrame([s.to_dict() for s in self.scenarios])
        except ImportError:
            return [s.to_dict() for s in self.scenarios]
    
    def summary(self) -> str:
        """Generate text summary"""
        lines = [
            "=" * 80,
            "SCENARIO OPTIMIZATION RESULTS",
            "=" * 80,
            f"Total Scenarios Analyzed: {self.total_scenarios}",
            f"Viable Scenarios: {self.viable_scenarios} ({self.viable_scenarios/self.total_scenarios*100:.1f}%)",
            f"Optimal Scenarios: {self.optimal_scenarios}",
            "",
        ]
        
        if self.optimal_scenario:
            o = self.optimal_scenario
            lines.extend([
                "OPTIMAL SCENARIO:",
                f"  NG Price: ${o.ng_price:.2f}/MMBtu",
                f"  Electricity Price: ${o.electricity_price:.1f}/MWh",
                f"  H2 Selling Price: ${o.h2_selling_price:.2f}/kg",
                f"  LCOH: ${o.lcoh:.2f}/kg",
                f"  Profit Margin: ${o.profit_margin:.2f}/kg",
                f"  NPV: ${o.npv:,.0f}",
                f"  IRR: {o.irr:.1%}",
                f"  Payback: {o.payback:.1f} years",
                "",
            ])
        
        if self.breakeven_h2_price:
            lines.extend([
                "BREAKEVEN ANALYSIS:",
                f"  Breakeven H2 Price: ${self.breakeven_h2_price:.2f}/kg",
                f"  Breakeven NG Price: ${self.breakeven_ng_price:.2f}/MMBtu" if self.breakeven_ng_price else "",
                f"  Breakeven Elec Price: ${self.breakeven_elec_price:.1f}/MWh" if self.breakeven_elec_price else "",
                "",
            ])
        
        if self.mc_npv_mean != 0:
            lines.extend([
                "MONTE CARLO ANALYSIS:",
                f"  NPV Mean: ${self.mc_npv_mean:,.0f}",
                f"  NPV Std Dev: ${self.mc_npv_std:,.0f}",
                f"  NPV P10: ${self.mc_npv_p10:,.0f}",
                f"  NPV P50: ${self.mc_npv_p50:,.0f}",
                f"  NPV P90: ${self.mc_npv_p90:,.0f}",
                f"  Probability NPV > 0: {self.mc_probability_positive_npv:.1%}",
            ])
        
        return "\n".join(lines)


class TEAScenarioOptimizer:
    """
    Multi-dimensional scenario optimizer for TEA models.
    
    Supports:
    - 1D sensitivity analysis (single parameter sweep)
    - 2D sensitivity analysis (two parameter sweep)
    - 3D grid search (three parameter sweep)
    - Monte Carlo simulation
    - Breakeven analysis
    - Scenario ranking and optimization
    
    Works with any TEA model that has a calculate() method returning
    results with npv, irr, lcoh attributes.
    """
    
    def __init__(self, 
                 tea_model: Any,
                 config: Optional[OptimizationConfig] = None):
        """
        Initialize optimizer.
        
        Args:
            tea_model: TEA model instance (ElectrolyzerTEA, TriGenIntegratedTEA, etc.)
            config: Optimization configuration
        """
        self.tea_model = tea_model
        self.config = config or OptimizationConfig()
        self._results: Optional[OptimizationResults] = None
    
    @property
    def results(self) -> OptimizationResults:
        if self._results is None:
            raise ValueError("Run optimize() first")
        return self._results
    
    def _run_single_scenario(self,
                             scenario_id: int,
                             ng_price: float,
                             elec_price: float,
                             h2_sell_price: float,
                             **kwargs) -> ScenarioResult:
        """Run a single scenario and return results"""
        
        result = ScenarioResult(
            scenario_id=scenario_id,
            ng_price=ng_price,
            electricity_price=elec_price,
            h2_selling_price=h2_sell_price,
        )
        
        try:
            # Detect model type and update parameters accordingly
            model_type = type(self.tea_model).__name__
            
            if 'TriGen' in model_type:
                # TriGen model - update NG price
                self.tea_model.opex.ng_commodity_price = ng_price
                self.tea_model.revenue.electricity_price = elec_price / 1000  # Convert to $/kWh
                self.tea_model.revenue.h2_price = h2_sell_price
                
                # Run calculation
                tea_result = self.tea_model.calculate(green_h2_lcoh=kwargs.get('green_h2_lcoh', 3.0))
                
                result.lcoh = tea_result.lcoh_best_estimate
                result.lcoe = tea_result.lcoe * 1000 if hasattr(tea_result, 'lcoe') else 0
                result.npv = tea_result.npv
                result.irr = tea_result.irr
                result.payback = tea_result.payback_simple
                result.annual_h2_kg = tea_result.annual_h2_kg
                result.annual_electricity_MWh = tea_result.annual_electricity_MWh
                
            elif 'Electrolyzer' in model_type:
                # Electrolyzer model - update electricity price
                if self.tea_model.opex.use_ppa:
                    self.tea_model.opex.ppa_price_per_MWh = elec_price
                else:
                    self.tea_model.opex.electricity_price_per_MWh = elec_price
                
                # Run calculation
                tea_result = self.tea_model.calculate(h2_selling_price=h2_sell_price)
                
                result.lcoh = tea_result.lcoh_total
                result.npv = tea_result.npv
                result.irr = tea_result.irr
                result.payback = tea_result.payback_simple
                result.annual_h2_kg = tea_result.annual_H2_kg
                result.annual_electricity_MWh = tea_result.annual_electricity_MWh
                
            else:
                # Generic model - try common patterns
                if hasattr(self.tea_model, 'opex'):
                    if hasattr(self.tea_model.opex, 'ng_commodity_price'):
                        self.tea_model.opex.ng_commodity_price = ng_price
                    if hasattr(self.tea_model.opex, 'electricity_price_per_MWh'):
                        self.tea_model.opex.electricity_price_per_MWh = elec_price
                
                tea_result = self.tea_model.calculate()
                
                result.lcoh = getattr(tea_result, 'lcoh', 0) or getattr(tea_result, 'lcoh_total', 0)
                result.npv = getattr(tea_result, 'npv', 0)
                result.irr = getattr(tea_result, 'irr', 0)
                result.payback = getattr(tea_result, 'payback_simple', float('inf'))
                result.annual_h2_kg = getattr(tea_result, 'annual_H2_kg', 0) or getattr(tea_result, 'annual_h2_kg', 0)
            
            # Calculate derived metrics
            result.profit_margin = h2_sell_price - result.lcoh
            result.annual_profit = result.profit_margin * result.annual_h2_kg
            
            if result.npv != 0 and hasattr(tea_result, 'capex_total'):
                result.roi = result.npv / tea_result.capex_total
            
            # Determine status
            result.status = self._evaluate_scenario_status(result)
            
        except Exception as e:
            result.status = ScenarioStatus.UNVIABLE
            result.warnings.append(str(e))
        
        return result
    
    def _evaluate_scenario_status(self, result: ScenarioResult) -> ScenarioStatus:
        """Evaluate scenario viability"""
        cfg = self.config
        
        # Check viability criteria
        issues = []
        
        if result.irr and result.irr < cfg.min_irr:
            issues.append(f"IRR {result.irr:.1%} < {cfg.min_irr:.1%}")
        
        if result.payback > cfg.max_payback:
            issues.append(f"Payback {result.payback:.1f}y > {cfg.max_payback}y")
        
        if result.lcoh > cfg.max_lcoh:
            issues.append(f"LCOH ${result.lcoh:.2f} > ${cfg.max_lcoh}")
        
        if result.npv < cfg.min_npv:
            issues.append(f"NPV ${result.npv:,.0f} < ${cfg.min_npv:,.0f}")
        
        if result.profit_margin < 0:
            issues.append(f"Negative margin ${result.profit_margin:.2f}/kg")
        
        result.warnings = issues
        
        if len(issues) == 0:
            return ScenarioStatus.OPTIMAL
        elif len(issues) <= 1 and result.npv > 0:
            return ScenarioStatus.VIABLE
        elif result.npv > 0:
            return ScenarioStatus.MARGINAL
        else:
            return ScenarioStatus.UNVIABLE
    
    def run_sensitivity_1d(self,
                           parameter: str,
                           values: Optional[List[float]] = None,
                           fixed_params: Optional[Dict[str, float]] = None) -> OptimizationResults:
        """
        Run 1D sensitivity analysis on a single parameter.
        
        Args:
            parameter: 'ng_price', 'electricity_price', or 'h2_price'
            values: List of values to test (or use config range)
            fixed_params: Fixed values for other parameters
        """
        cfg = self.config
        results = OptimizationResults()
        
        # Get parameter range
        if values is None:
            if parameter == 'ng_price':
                rng = cfg.ng_price_range
            elif parameter == 'electricity_price':
                rng = cfg.electricity_price_range
            elif parameter == 'h2_price':
                rng = cfg.h2_price_range
            else:
                raise ValueError(f"Unknown parameter: {parameter}")
            values = np.linspace(rng[0], rng[1], rng[2]).tolist()
        
        # Get fixed parameters
        fixed = fixed_params or {}
        ng_fixed = fixed.get('ng_price', cfg.fixed_ng_price or 4.0)
        elec_fixed = fixed.get('electricity_price', cfg.fixed_electricity_price or 40.0)
        h2_fixed = fixed.get('h2_price', cfg.fixed_h2_price or 6.0)
        
        # Run scenarios
        for i, val in enumerate(values):
            if parameter == 'ng_price':
                result = self._run_single_scenario(i, val, elec_fixed, h2_fixed)
            elif parameter == 'electricity_price':
                result = self._run_single_scenario(i, ng_fixed, val, h2_fixed)
            else:  # h2_price
                result = self._run_single_scenario(i, ng_fixed, elec_fixed, val)
            
            result.scenario_name = f"{parameter}={val:.2f}"
            results.scenarios.append(result)
            
            # Store sensitivity data
            metric_val = self._get_metric_value(result, cfg.optimization_metric)
            if parameter == 'ng_price':
                results.ng_sensitivity[val] = metric_val
            elif parameter == 'electricity_price':
                results.elec_sensitivity[val] = metric_val
            else:
                results.h2_sensitivity[val] = metric_val
        
        # Find breakeven
        results = self._find_breakeven(results, parameter)
        
        # Rank and find optimal
        results = self._rank_scenarios(results)
        
        self._results = results
        return results
    
    def run_sensitivity_2d(self,
                           param1: str,
                           param2: str,
                           values1: Optional[List[float]] = None,
                           values2: Optional[List[float]] = None,
                           fixed_params: Optional[Dict[str, float]] = None) -> OptimizationResults:
        """
        Run 2D sensitivity analysis on two parameters.
        
        Args:
            param1, param2: Parameters to sweep ('ng_price', 'electricity_price', 'h2_price')
            values1, values2: Values to test for each parameter
            fixed_params: Fixed value for the third parameter
        """
        cfg = self.config
        results = OptimizationResults()
        
        # Get ranges
        def get_values(param, vals):
            if vals is not None:
                return vals
            if param == 'ng_price':
                rng = cfg.ng_price_range
            elif param == 'electricity_price':
                rng = cfg.electricity_price_range
            else:
                rng = cfg.h2_price_range
            return np.linspace(rng[0], rng[1], rng[2]).tolist()
        
        values1 = get_values(param1, values1)
        values2 = get_values(param2, values2)
        
        # Fixed parameter
        fixed = fixed_params or {}
        params = ['ng_price', 'electricity_price', 'h2_price']
        third_param = [p for p in params if p not in [param1, param2]][0]
        
        if third_param == 'ng_price':
            third_val = fixed.get('ng_price', cfg.fixed_ng_price or 4.0)
        elif third_param == 'electricity_price':
            third_val = fixed.get('electricity_price', cfg.fixed_electricity_price or 40.0)
        else:
            third_val = fixed.get('h2_price', cfg.fixed_h2_price or 6.0)
        
        # Run all combinations
        scenario_id = 0
        for v1, v2 in itertools.product(values1, values2):
            # Build parameter dict
            param_dict = {param1: v1, param2: v2, third_param: third_val}
            
            result = self._run_single_scenario(
                scenario_id,
                param_dict['ng_price'],
                param_dict['electricity_price'],
                param_dict['h2_price']
            )
            result.scenario_name = f"{param1}={v1:.2f}, {param2}={v2:.2f}"
            results.scenarios.append(result)
            scenario_id += 1
        
        results = self._rank_scenarios(results)
        self._results = results
        return results
    
    def run_full_grid_search(self,
                             ng_values: Optional[List[float]] = None,
                             elec_values: Optional[List[float]] = None,
                             h2_values: Optional[List[float]] = None) -> OptimizationResults:
        """
        Run full 3D grid search across all parameters.
        
        Args:
            ng_values: NG price values ($/MMBtu)
            elec_values: Electricity price values ($/MWh)
            h2_values: H2 selling price values ($/kg)
        """
        cfg = self.config
        results = OptimizationResults()
        
        # Get ranges
        if ng_values is None:
            ng_values = np.linspace(*cfg.ng_price_range[:2], cfg.ng_price_range[2]).tolist()
        if elec_values is None:
            elec_values = np.linspace(*cfg.electricity_price_range[:2], cfg.electricity_price_range[2]).tolist()
        if h2_values is None:
            h2_values = np.linspace(*cfg.h2_price_range[:2], cfg.h2_price_range[2]).tolist()
        
        # Generate all combinations
        combinations = list(itertools.product(ng_values, elec_values, h2_values))
        results.total_scenarios = len(combinations)
        
        print(f"Running {len(combinations)} scenarios...")
        
        # Run scenarios (optionally parallel)
        if cfg.parallel and len(combinations) > 10:
            with ThreadPoolExecutor(max_workers=cfg.max_workers) as executor:
                futures = {
                    executor.submit(self._run_single_scenario, i, ng, elec, h2): i
                    for i, (ng, elec, h2) in enumerate(combinations)
                }
                for future in as_completed(futures):
                    result = future.result()
                    result.scenario_name = f"NG=${result.ng_price:.2f}, E=${result.electricity_price:.0f}, H2=${result.h2_selling_price:.2f}"
                    results.scenarios.append(result)
        else:
            for i, (ng, elec, h2) in enumerate(combinations):
                result = self._run_single_scenario(i, ng, elec, h2)
                result.scenario_name = f"NG=${ng:.2f}, E=${elec:.0f}, H2=${h2:.2f}"
                results.scenarios.append(result)
                
                if (i + 1) % 50 == 0:
                    print(f"  Completed {i + 1}/{len(combinations)} scenarios")
        
        # Find breakeven points
        results = self._find_breakeven_3d(results)
        
        # Rank scenarios
        results = self._rank_scenarios(results)
        
        self._results = results
        return results
    
    def run_monte_carlo(self,
                        base_ng_price: float = 4.0,
                        base_elec_price: float = 40.0,
                        base_h2_price: float = 6.0,
                        iterations: Optional[int] = None) -> OptimizationResults:
        """
        Run Monte Carlo simulation with price uncertainty.
        
        Args:
            base_ng_price: Mean NG price
            base_elec_price: Mean electricity price
            base_h2_price: Mean H2 selling price
            iterations: Number of iterations
        """
        cfg = self.config
        n = iterations or cfg.monte_carlo_iterations
        results = OptimizationResults()
        results.total_scenarios = n
        
        print(f"Running Monte Carlo with {n} iterations...")
        
        # Generate random samples (lognormal for prices to avoid negatives)
        np.random.seed(42)
        
        ng_samples = np.random.lognormal(
            np.log(base_ng_price), 
            cfg.ng_price_std / base_ng_price, 
            n
        )
        elec_samples = np.random.lognormal(
            np.log(base_elec_price),
            cfg.elec_price_std / base_elec_price,
            n
        )
        h2_samples = np.random.lognormal(
            np.log(base_h2_price),
            cfg.h2_price_std / base_h2_price,
            n
        )
        
        # Run scenarios
        npv_results = []
        for i in range(n):
            result = self._run_single_scenario(
                i, ng_samples[i], elec_samples[i], h2_samples[i]
            )
            results.scenarios.append(result)
            npv_results.append(result.npv)
            
            if (i + 1) % 100 == 0:
                print(f"  Completed {i + 1}/{n} iterations")
        
        # Calculate statistics
        npv_array = np.array(npv_results)
        results.mc_npv_mean = float(np.mean(npv_array))
        results.mc_npv_std = float(np.std(npv_array))
        results.mc_npv_p10 = float(np.percentile(npv_array, 10))
        results.mc_npv_p50 = float(np.percentile(npv_array, 50))
        results.mc_npv_p90 = float(np.percentile(npv_array, 90))
        results.mc_probability_positive_npv = float(np.mean(npv_array > 0))
        
        results = self._rank_scenarios(results)
        self._results = results
        return results
    
    def find_optimal_h2_price(self,
                              target_irr: float = 0.15,
                              ng_price: float = 4.0,
                              elec_price: float = 40.0) -> float:
        """
        Find minimum H2 selling price to achieve target IRR.
        
        Uses binary search to find the breakeven price.
        """
        low, high = 1.0, 20.0
        tolerance = 0.01
        
        for _ in range(50):  # Max iterations
            mid = (low + high) / 2
            result = self._run_single_scenario(0, ng_price, elec_price, mid)
            
            if result.irr is None:
                low = mid
            elif abs(result.irr - target_irr) < 0.001:
                return mid
            elif result.irr < target_irr:
                low = mid
            else:
                high = mid
            
            if high - low < tolerance:
                break
        
        return mid
    
    def _get_metric_value(self, result: ScenarioResult, metric: OptimizationMetric) -> float:
        """Get the value of a specific metric from results"""
        if metric == OptimizationMetric.NPV:
            return result.npv
        elif metric == OptimizationMetric.IRR:
            return result.irr or 0
        elif metric == OptimizationMetric.LCOH:
            return result.lcoh
        elif metric == OptimizationMetric.LCOE:
            return result.lcoe
        elif metric == OptimizationMetric.PAYBACK:
            return result.payback
        elif metric == OptimizationMetric.PROFIT_MARGIN:
            return result.profit_margin
        elif metric == OptimizationMetric.ROI:
            return result.roi
        return 0
    
    def _find_breakeven(self, results: OptimizationResults, parameter: str) -> OptimizationResults:
        """Find breakeven point where NPV crosses zero"""
        # Map parameter to attribute name
        attr_map = {
            'ng_price': 'ng_price',
            'electricity_price': 'electricity_price', 
            'h2_price': 'h2_selling_price'
        }
        attr_name = attr_map.get(parameter, parameter)
        
        scenarios = sorted(results.scenarios, key=lambda s: getattr(s, attr_name, 0))
        
        for i in range(len(scenarios) - 1):
            s1, s2 = scenarios[i], scenarios[i + 1]
            if s1.npv * s2.npv < 0:  # Sign change
                # Linear interpolation
                val1 = getattr(s1, attr_name, 0)
                val2 = getattr(s2, attr_name, 0)
                breakeven = val1 + (val2 - val1) * (-s1.npv) / (s2.npv - s1.npv)
                
                if parameter == 'ng_price':
                    results.breakeven_ng_price = breakeven
                elif parameter == 'electricity_price':
                    results.breakeven_elec_price = breakeven
                else:
                    results.breakeven_h2_price = breakeven
                break
        
        return results
    
    def _find_breakeven_3d(self, results: OptimizationResults) -> OptimizationResults:
        """Find breakeven points from 3D grid search"""
        # Find minimum H2 price with positive NPV for each NG/elec combination
        viable = [s for s in results.scenarios if s.npv > 0]
        if viable:
            min_h2 = min(s.h2_selling_price for s in viable)
            results.breakeven_h2_price = min_h2
        
        return results
    
    def _rank_scenarios(self, results: OptimizationResults) -> OptimizationResults:
        """Rank scenarios and identify optimal"""
        cfg = self.config
        
        # Count by status
        results.total_scenarios = len(results.scenarios)
        results.viable_scenarios = sum(1 for s in results.scenarios 
                                       if s.status in [ScenarioStatus.OPTIMAL, ScenarioStatus.VIABLE])
        results.optimal_scenarios = sum(1 for s in results.scenarios 
                                        if s.status == ScenarioStatus.OPTIMAL)
        
        # Sort by optimization metric
        metric = cfg.optimization_metric
        reverse = not cfg.minimize
        
        sorted_scenarios = sorted(
            results.scenarios,
            key=lambda s: self._get_metric_value(s, metric),
            reverse=reverse
        )
        
        # Filter viable and get top scenarios
        viable_sorted = [s for s in sorted_scenarios 
                        if s.status in [ScenarioStatus.OPTIMAL, ScenarioStatus.VIABLE]]
        
        if viable_sorted:
            results.optimal_scenario = viable_sorted[0]
            results.top_scenarios = viable_sorted[:10]
        elif sorted_scenarios:
            results.optimal_scenario = sorted_scenarios[0]
            results.top_scenarios = sorted_scenarios[:10]
        
        return results


# ============================================================
# EXAMPLE USAGE
# ============================================================

if __name__ == "__main__":
    print("=" * 80)
    print("TEA SCENARIO OPTIMIZER - EXAMPLE")
    print("=" * 80)
    
    # Import a TEA model
    from trigen_tea_integrated import TriGenIntegratedTEA, TriGenSystemInputs
    
    # Create TEA model
    tea = TriGenIntegratedTEA(
        system=TriGenSystemInputs(
            rated_power_kW=1400,
            ng_input_scf_h=10700,
            h2_output_kg_h=21.76,
        )
    )
    
    # Create optimizer
    optimizer = TEAScenarioOptimizer(
        tea,
        config=OptimizationConfig(
            ng_price_range=(2.0, 8.0, 7),
            electricity_price_range=(40, 120, 5),
            h2_price_range=(4.0, 10.0, 7),
            optimization_metric=OptimizationMetric.NPV,
            min_irr=0.10,
        )
    )
    
    # Run 1D sensitivity on H2 price
    print("\n1D SENSITIVITY - H2 Price")
    print("-" * 40)
    results_1d = optimizer.run_sensitivity_1d(
        'h2_price',
        fixed_params={'ng_price': 4.0, 'electricity_price': 60.0}
    )
    
    for s in results_1d.scenarios:
        print(f"H2=${s.h2_selling_price:.2f}/kg -> LCOH=${s.lcoh:.2f}, NPV=${s.npv:,.0f}, Status={s.status.value}")
    
    if results_1d.breakeven_h2_price:
        print(f"\nBreakeven H2 Price: ${results_1d.breakeven_h2_price:.2f}/kg")
    
    # Run 2D sensitivity
    print("\n\n2D SENSITIVITY - NG Price vs H2 Price")
    print("-" * 40)
    results_2d = optimizer.run_sensitivity_2d(
        'ng_price', 'h2_price',
        values1=[2, 4, 6, 8],
        values2=[4, 6, 8, 10],
        fixed_params={'electricity_price': 60.0}
    )
    
    print(f"Total scenarios: {results_2d.total_scenarios}")
    print(f"Viable scenarios: {results_2d.viable_scenarios}")
    
    if results_2d.optimal_scenario:
        o = results_2d.optimal_scenario
        print(f"\nOptimal: NG=${o.ng_price:.2f}, H2=${o.h2_selling_price:.2f} -> NPV=${o.npv:,.0f}")
    
    # Find optimal H2 price for target IRR
    print("\n\nFINDING OPTIMAL H2 PRICE FOR 15% IRR")
    print("-" * 40)
    optimal_h2 = optimizer.find_optimal_h2_price(target_irr=0.15, ng_price=4.0, elec_price=60.0)
    print(f"Minimum H2 price for 15% IRR: ${optimal_h2:.2f}/kg")
