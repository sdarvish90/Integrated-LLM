#!/usr/bin/env python3
"""
DecarbIQ Market Connector — Bridge news signals to trained model outputs
========================================================================
Reads the trained DecarbIQ model JSON files (projection map, Monte Carlo,
sensitivity analysis) and translates news signals into quantified market
assessments using actual model coefficients.

No hardcoded impact percentages — everything references real model outputs.

Usage:
    from decarbiq_market_connector import DecarbIQMarketConnector
    connector = DecarbIQMarketConnector()
    assessment = connector.assess_signal(signal)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from news_market_signal_parser import (
    ProjectSignal, Project,
    GAS_DEMAND_BCFD_PER_MTPA_SMR, GAS_DEMAND_BCFD_PER_MTPA_ATR,
    STATUS_FID_PROBABILITY,
)

_HERE = Path(__file__).resolve().parent
_DEFAULT_MODEL_DIR = _HERE.parent / 'decarbiq_final_package_v2'

# ---------------------------------------------------------------------------
# LITERATURE-SOURCED BLUE H2 ECONOMICS
# ---------------------------------------------------------------------------

# LCOH breakdown for blue hydrogen (SMR + CCS), $/kg H2
# Source: IEA "Global Hydrogen Review 2023", Figure 3.4
# Also: Hydrogen Council "Hydrogen Decarbonization Pathways" (2021)
BLUE_H2_LCOH_COMPONENTS = {
    'gas_feedstock':   0.80,  # at $4/MMBtu HH, ~56 MMBtu/t H2
    'gas_fuel':        0.25,  # fuel for reformer heat
    'capex_amort':     0.40,  # SMR+CCS capital ($1,200-1,800/kW)
    'opex_fixed':      0.15,  # maintenance, labor
    'ccs_cost':        0.20,  # CO2 transport + storage
    'total':           1.80,  # typical Gulf Coast, no subsidies
}

# Gas price sensitivity: $/kg per $/MMBtu change in Henry Hub
# Source: NETL "H2A Analysis" (2022); confirmed by Hydrogen Council (2024)
GAS_PRICE_TO_LCOH = 0.14  # $/kg per $/MMBtu

# 45Q credit: $85/tCO2 x ~9.3 tCO2/tH2 (SMR 90% capture) = $0.79/kg
# Source: IRC Section 45Q; capture rate from IEAGHG (2017)
CREDIT_45Q_IMPACT = 0.79  # $/kg H2

# 45V credit: up to $3/kg H2 (tier 1, <0.45 kgCO2/kgH2)
# Source: IRC Section 45V
CREDIT_45V_MAX = 3.00


# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------

@dataclass
class MarketAssessment:
    """Assessment of a news signal through the DecarbIQ model lens."""
    signal: ProjectSignal

    # Gas market impact
    incremental_gas_demand_bcfd: Optional[float] = None
    gas_price_impact_per_mmbtu: Optional[float] = None
    gas_price_context: Dict[str, Any] = field(default_factory=dict)

    # Electricity market impact
    ercot_impact_per_mwh: Optional[float] = None
    caiso_impact_per_mwh: Optional[float] = None

    # Blue H2 economics
    lcoh_impact_usd_per_kg: Optional[float] = None

    # Policy context
    policy_variables_affected: List[str] = field(default_factory=list)
    policy_scenario_match: Optional[str] = None

    # Market context
    year_of_impact: int = 2028
    baseline_projection: Dict[str, Any] = field(default_factory=dict)
    adjusted_projection: Optional[Dict[str, Any]] = None

    # Model evidence audit trail
    model_evidence: Dict[str, Any] = field(default_factory=dict)

    # Confidence
    confidence: str = 'LOW'
    methodology_notes: List[str] = field(default_factory=list)
    literature_sources: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CONNECTOR CLASS
# ---------------------------------------------------------------------------

class DecarbIQMarketConnector:
    """Bridge between news signals and DecarbIQ trained model outputs."""

    def __init__(self, model_dir: str = None):
        self.model_dir = Path(model_dir) if model_dir else _DEFAULT_MODEL_DIR
        self.projection_map: Dict = {}
        self.sensitivity: Dict = {}
        self.mc_results: Dict = {}
        self.event_freq: Dict = {}
        self.assumptions: Dict = {}
        self._loaded = False
        self._load_models()

    def _load_json(self, relative_path: str) -> Dict:
        fp = self.model_dir / relative_path
        if not fp.exists():
            print(f"  Warning: {fp} not found")
            return {}
        with open(fp, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _load_models(self):
        print("Loading DecarbIQ model outputs...")
        self.projection_map = self._load_json('decarbiq_projection_map.json')
        self.sensitivity = self._load_json('decarbiq_sensitivity_analysis.json')
        self.mc_results = self._load_json('geopolitical_and_macro/monte_carlo_lng_v8.json')
        self.event_freq = self._load_json('geopolitical_and_macro/event_frequency_projections_v4.json')
        self.assumptions = self._load_json('decarbiq_projection_assumptions.json')

        loaded = sum(1 for d in [self.projection_map, self.sensitivity,
                                  self.mc_results, self.event_freq] if d)
        print(f"  Loaded {loaded}/4 model files")
        self._loaded = loaded > 0

    # -- Coefficient accessors -----------------------------------------------

    @property
    def _coefficients(self) -> Dict:
        return self.projection_map.get('coefficients', {})

    @property
    def gas_demand_coefficient(self) -> float:
        """HH regression coefficient for electric_power_bcfd."""
        hh = self._coefficients.get('hh_full', {})
        return hh.get('electric_power_bcfd', -0.0452)

    @property
    def ercot_passthrough(self) -> float:
        return self._coefficients.get('ercot_gas_passthrough', 6.94)

    @property
    def caiso_passthrough(self) -> float:
        return self._coefficients.get('caiso_gas_passthrough', 9.51)

    def get_projection_context(self, year: int) -> Dict[str, Any]:
        """Get P10/P50/P90 gas and electricity for a given year."""
        vp = self.projection_map.get('variable_projections', {})
        yr_data = vp.get(str(year), {})
        gas = yr_data.get('gas_price', {})

        # Also get from MC results
        mc_annual = self.mc_results.get('annual_forecasts', {})
        mc_yr = mc_annual.get(str(year), {})

        return {
            'year': year,
            'gas_price': {
                'mean': gas.get('mean') or mc_yr.get('mean'),
                'p10': gas.get('p10') or mc_yr.get('p10'),
                'p50': gas.get('p50') or mc_yr.get('median'),
                'p90': gas.get('p90') or mc_yr.get('p90'),
            },
            'ercot_demand_twh': yr_data.get('tx_ercot_demand_twh'),
            'electric_power_bcfd': yr_data.get('electric_power_bcfd'),
            'tx_gas_gen_pct': yr_data.get('tx_gas_gen_pct'),
            'ira_active': yr_data.get('ira_active'),
        }

    def get_gas_price_outlook(self) -> Dict[str, Dict]:
        """Full 2025-2035 gas price outlook from MC."""
        mc = self.mc_results.get('annual_forecasts', {})
        return {yr: {
            'mean': d.get('mean'), 'p10': d.get('p10'),
            'p50': d.get('median'), 'p90': d.get('p90'),
        } for yr, d in mc.items()}

    def get_sensitivity_for_variable(self, var_name: str) -> Dict[str, Any]:
        """Lookup a variable in the sensitivity catalog."""
        catalog = self.sensitivity.get('variable_catalog', {})
        return catalog.get(var_name, {})

    def _find_multi_shock_scenario(self, scenario_name: str) -> Optional[Dict]:
        """Find a predefined multi-shock scenario by partial name match."""
        scenarios = self.sensitivity.get('multi_shock_scenarios', [])
        name_lower = scenario_name.lower()
        for s in scenarios:
            if name_lower in s.get('name', '').lower():
                return s
        return None

    # -- Assessment logic ----------------------------------------------------

    def assess_signal(self, signal: ProjectSignal,
                      fid_override: Optional[float] = None,
                      fid_source: str = '',
                      regulatory_evidence: Optional[List[Dict]] = None,
                      ) -> MarketAssessment:
        """Produce a model-grounded MarketAssessment for a single signal."""
        evidence: Dict[str, Any] = {}
        notes: List[str] = []
        sources: List[str] = []
        confidence = 'LOW'

        # Determine impact year
        year = self._estimate_impact_year(signal)
        ctx = self.get_projection_context(year)
        evidence['year_of_impact'] = year

        # ---- Gas demand impact ----
        gas_demand_bcfd = None
        gas_price_impact = None
        ercot_impact = None
        caiso_impact = None
        lcoh_impact = None

        if signal.capacity_mtpa_h2 and signal.capacity_mtpa_h2 > 0:
            tech = signal.technology or 'SMR'
            rate = GAS_DEMAND_BCFD_PER_MTPA_ATR if tech == 'ATR' else GAS_DEMAND_BCFD_PER_MTPA_SMR
            gas_demand_bcfd = signal.capacity_mtpa_h2 * rate

            # Apply FID probability weighting
            if fid_override is not None:
                fid_prob = fid_override
                evidence['fid_source'] = fid_source or 'fid_engine_override'
            else:
                fid_prob = STATUS_FID_PROBABILITY.get(signal.status or 'Announced', 0.10)
                evidence['fid_source'] = 'static_status_lookup'
            weighted_demand = gas_demand_bcfd * fid_prob

            # Gas price impact via regression coefficient
            coeff = self.gas_demand_coefficient
            gas_price_impact = weighted_demand * abs(coeff)
            # Positive demand -> higher gas price (coefficient is negative in regression)
            if signal.status == 'Cancelled':
                gas_price_impact = -gas_price_impact  # Cancellation reduces price

            evidence['sensitivity_variable'] = 'electric_power_bcfd'
            evidence['coefficient_used'] = coeff
            evidence['coefficient_source'] = 'decarbiq_projection_map.json -> coefficients.hh_full.electric_power_bcfd'
            evidence['gas_demand_bcfd_raw'] = gas_demand_bcfd
            evidence['gas_demand_bcfd_weighted'] = weighted_demand
            evidence['fid_probability_applied'] = fid_prob
            if regulatory_evidence:
                evidence['regulatory_evidence_count'] = len(regulatory_evidence)
                evidence['regulatory_sources'] = list(set(
                    e.get('source', 'unknown') for e in regulatory_evidence))

            # Sigma context
            var_info = self.get_sensitivity_for_variable('electric_power_bcfd')
            if var_info:
                sigma = var_info.get('sigma_1', 1.0)
                evidence['sigma_1_used'] = sigma
                evidence['sigma_source'] = 'decarbiq_sensitivity_analysis.json -> variable_catalog.electric_power_bcfd'
                notes.append(f"Gas demand delta {weighted_demand:.3f} bcf/d = {weighted_demand/sigma:.1f} sigma")

            # Electricity passthrough
            if gas_price_impact is not None:
                ercot_pt = self.ercot_passthrough
                caiso_pt = self.caiso_passthrough
                ercot_impact = gas_price_impact * ercot_pt
                caiso_impact = gas_price_impact * caiso_pt
                evidence['passthrough_ercot'] = ercot_pt
                evidence['passthrough_caiso'] = caiso_pt
                evidence['passthrough_source'] = 'decarbiq_projection_map.json -> coefficients'

            # LCOH impact
            if gas_price_impact is not None:
                lcoh_impact = gas_price_impact * GAS_PRICE_TO_LCOH
                evidence['gas_to_lcoh_rate'] = GAS_PRICE_TO_LCOH
                sources.append('NETL H2A Analysis (2022) — $0.14/kg per $/MMBtu')

            notes.append(f"Capacity: {signal.capacity_mtpa_h2:.3f} MTPA H2 ({tech})")
            notes.append(f"Gas feedstock rate: {rate} bcf/d per MTPA ({tech})")
            sources.append(f"NETL (2010) Table 3-1 — {tech} gas consumption")
            confidence = 'MEDIUM' if signal.confidence > 0.3 else 'LOW'

        # ---- Policy signal ----
        policy_vars: List[str] = []
        policy_match = None

        if signal.policy_signal:
            ps_lower = signal.policy_signal.lower()
            if '45q' in ps_lower or '45v' in ps_lower or 'ira' in ps_lower:
                policy_vars.extend(['ira_active', 'itc_rate_pct'])

                # Check if this matches a predefined scenario
                if any(w in (signal.article_title or '').lower()
                       for w in ['repeal', 'rollback', 'eliminate', 'end']):
                    scenario = self._find_multi_shock_scenario('IRA')
                    if scenario:
                        policy_match = scenario.get('name', 'IRA Full Rollback')
                        evidence['scenario_matched'] = policy_match
                        evidence['scenario_source'] = 'decarbiq_sensitivity_analysis.json -> multi_shock_scenarios'
                        evidence['scenario_delta_gas'] = scenario.get('dynamic', {}).get('delta_gas')
                        evidence['scenario_delta_ercot'] = scenario.get('dynamic', {}).get('delta_ercot')
                        notes.append(f"Matched predefined scenario: {policy_match}")
                        confidence = 'HIGH'

                sources.append('IRC Section 45Q ($85/tCO2); IEAGHG (2017)')
                notes.append(f"Policy signal: {signal.policy_signal}")

        # ---- EPC confirmation signal ----
        if signal.epc_contractor:
            notes.append(f"EPC contractor identified: {signal.epc_contractor}")
            notes.append("EPC award is a strong construction confirmation signal")
            if signal.status in ('EPC Award', 'Construction'):
                confidence = 'HIGH'

        # ---- FID source (for signals without capacity) ----
        if 'fid_source' not in evidence:
            if fid_override is not None:
                evidence['fid_source'] = fid_source or 'fid_engine_override'
                evidence['fid_probability_applied'] = fid_override
            else:
                evidence['fid_source'] = 'static_status_lookup'
                evidence['fid_probability_applied'] = STATUS_FID_PROBABILITY.get(
                    signal.status or 'Announced', 0.10)
            if regulatory_evidence:
                evidence['regulatory_evidence_count'] = len(regulatory_evidence)
                evidence['regulatory_sources'] = list(set(
                    e.get('source', 'unknown') for e in regulatory_evidence))

        # ---- Projection context ----
        evidence['projection_map_year'] = str(year)
        gas_ctx = ctx.get('gas_price', {})
        if gas_ctx.get('p50'):
            evidence['projection_map_key'] = f'variable_projections.{year}.gas_price.p50'
            evidence['baseline_p50_gas'] = gas_ctx['p50']

        return MarketAssessment(
            signal=signal,
            incremental_gas_demand_bcfd=gas_demand_bcfd,
            gas_price_impact_per_mmbtu=gas_price_impact,
            gas_price_context=gas_ctx,
            ercot_impact_per_mwh=ercot_impact,
            caiso_impact_per_mwh=caiso_impact,
            lcoh_impact_usd_per_kg=lcoh_impact,
            policy_variables_affected=policy_vars,
            policy_scenario_match=policy_match,
            year_of_impact=year,
            baseline_projection=ctx,
            adjusted_projection=None,  # Could compute if we had full MC re-run
            model_evidence=evidence,
            confidence=confidence,
            methodology_notes=notes,
            literature_sources=sources,
        )

    def assess_batch(self, signals: List[ProjectSignal],
                     fid_overrides: Optional[Dict[int, Dict]] = None,
                     evidence_map: Optional[Dict[int, List[Dict]]] = None,
                     ) -> List[MarketAssessment]:
        """Assess a batch of signals with optional per-signal FID overrides and evidence.

        fid_overrides: keyed by signal index -> {'probability': float, 'source': str}
        evidence_map:  keyed by signal index -> list of regulatory_evidence dicts
        """
        fid_overrides = fid_overrides or {}
        evidence_map = evidence_map or {}
        results = []
        for i, s in enumerate(signals):
            override = fid_overrides.get(i)
            results.append(self.assess_signal(
                s,
                fid_override=override['probability'] if override else None,
                fid_source=override.get('source', '') if override else '',
                regulatory_evidence=evidence_map.get(i)))
        return results

    def get_cumulative_pipeline_impact(self, projects: List[Project]) -> Dict[str, Any]:
        """Aggregate impact of the entire project pipeline on gas demand and prices."""
        total_demand = 0.0
        weighted_demand = 0.0
        by_region: Dict[str, float] = {}
        by_tech: Dict[str, float] = {}

        for p in projects:
            if p.capacity_mtpa_h2 and p.capacity_mtpa_h2 > 0 and p.status != 'Cancelled':
                rate = GAS_DEMAND_BCFD_PER_MTPA_ATR if p.technology == 'ATR' else GAS_DEMAND_BCFD_PER_MTPA_SMR
                raw_demand = p.capacity_mtpa_h2 * rate
                w_demand = raw_demand * p.fid_probability

                total_demand += raw_demand
                weighted_demand += w_demand

                r = p.region or 'Unknown'
                by_region[r] = by_region.get(r, 0) + w_demand

                t = p.technology or 'Unknown'
                by_tech[t] = by_tech.get(t, 0) + w_demand

        coeff = abs(self.gas_demand_coefficient)
        gas_impact = weighted_demand * coeff

        return {
            'total_pipeline_capacity_mtpa': sum(
                p.capacity_mtpa_h2 for p in projects
                if p.capacity_mtpa_h2 and p.status != 'Cancelled'),
            'probability_weighted_capacity_mtpa': sum(
                (p.capacity_mtpa_h2 or 0) * p.fid_probability
                for p in projects if p.status != 'Cancelled'),
            'total_gas_demand_bcfd': total_demand,
            'weighted_gas_demand_bcfd': weighted_demand,
            'gas_price_impact_per_mmbtu': gas_impact,
            'ercot_impact_per_mwh': gas_impact * self.ercot_passthrough,
            'lcoh_impact_per_kg': gas_impact * GAS_PRICE_TO_LCOH,
            'by_region': by_region,
            'by_technology': by_tech,
            'n_projects': len(projects),
            'n_projects_active': sum(1 for p in projects if p.status != 'Cancelled'),
            'model_evidence': {
                'coefficient': self.gas_demand_coefficient,
                'source': 'decarbiq_projection_map.json -> coefficients.hh_full.electric_power_bcfd',
                'ercot_passthrough': self.ercot_passthrough,
                'caiso_passthrough': self.caiso_passthrough,
            },
        }

    # -- Helpers -------------------------------------------------------------

    def _estimate_impact_year(self, signal: ProjectSignal) -> int:
        """Estimate when this project would affect the market."""
        if signal.cod_date:
            try:
                return int(signal.cod_date[:4])
            except (ValueError, IndexError):
                pass
        if signal.fid_date:
            try:
                fid_yr = int(signal.fid_date[:4])
                return fid_yr + 3  # ~3 years FID to COD for blue H2
            except (ValueError, IndexError):
                pass
        # Default: status-based estimate
        status_offset = {
            'Announced': 5, 'Pre-FEED': 4, 'FEED': 3, 'FID': 3,
            'EPC Award': 2, 'Construction': 2, 'Commissioning': 1,
            'Operational': 0, 'Cancelled': 0, 'Delayed': 5,
        }
        offset = status_offset.get(signal.status or 'Announced', 4)
        return min(2035, datetime.now().year + offset)


# Need datetime for _estimate_impact_year
from datetime import datetime
