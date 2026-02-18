#!/usr/bin/env python3
"""
Blue H2 / Ammonia Intelligence Monitor — Interactive Menu
==========================================================
General-purpose blue hydrogen and ammonia market intelligence tool.
Connects news collection → signal parsing → DecarbIQ model assessment.

Includes a pluggable client extension system (first demo: refractory materials).

Usage:
    python blue_h2_intelligence.py
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

_REQUIRED_MODULES = [
    'blue_h2_news_collector',
    'news_market_signal_parser',
    'decarbiq_market_connector',
    'report_generator',
]
for _mod in _REQUIRED_MODULES:
    try:
        __import__(_mod)
    except ImportError:
        print(f"\n  ERROR: Required module '{_mod}' not found.")
        print(f"  Make sure all 4 companion files are in the same directory as this script:")
        print(f"    blue_h2_news_collector.py, news_market_signal_parser.py,")
        print(f"    decarbiq_market_connector.py, report_generator.py\n")
        sys.exit(1)

from blue_h2_news_collector import BlueH2NewsCollector, KEYWORDS as _COLLECTOR_KEYWORDS
from news_market_signal_parser import (
    NewsMarketSignalParser, Project, ProjectSignal,
    STATUS_FID_PROBABILITY, EQUIPMENT_LEAD_TIMES,
    GAS_DEMAND_BCFD_PER_MTPA_SMR, GAS_DEMAND_BCFD_PER_MTPA_ATR,
    _STATUS_ORDER,
)
from decarbiq_market_connector import (
    DecarbIQMarketConnector, MarketAssessment,
    BLUE_H2_LCOH_COMPONENTS, GAS_PRICE_TO_LCOH,
    CREDIT_45Q_IMPACT, CREDIT_45V_MAX,
)
from report_generator import ReportGenerator
from fid_probability import FIDProbabilityEngine
from llm_client import llm_client, GROQ_MODEL as _GROQ_MODEL, OLLAMA_MODEL as _OLLAMA_MODEL

_HERE = Path(__file__).resolve().parent
_CONFIG_DIR = _HERE / 'config'

_DEFAULT_SYSTEM_PROMPT = (
    'You are a senior energy market analyst specializing in blue hydrogen, '
    'ammonia, and natural gas markets. Provide concise, data-driven analysis.'
)


# ============================================================================
# ASSESSMENT LLM — thin wrappers around shared llm_client
# ============================================================================

def _check_assessment_llm() -> str:
    """Check which assessment LLM backend is available."""
    return llm_client.check()


def _assessment_llm_generate(prompt: str, max_tokens: int = 1500) -> str:
    """Generate text using assessment LLM (Groq → Ollama fallback)."""
    return llm_client.generate(_DEFAULT_SYSTEM_PROMPT, prompt, max_tokens)


# -- Assessment prompts ---

_SIGNAL_ASSESSMENT_PROMPT = """Analyze this blue hydrogen/ammonia project signal and its quantitative market assessment.

SIGNAL:
- Project: {project_name}
- Developer: {developer}
- Capacity: {capacity} MTPA H2
- Technology: {technology}
- Status: {status} (FID probability: {fid_prob}%, source: {fid_source})
- Location: {location}
- EPC: {epc}
- Article: {title}

SOURCE ARTICLE CONTENT:
{article_content}

QUANTITATIVE ASSESSMENT:
- Gas demand impact: +{gas_demand} bcf/d
- HH price impact: +${hh_impact}/MMBtu
- ERCOT impact: +${ercot_impact}/MWh
- Year of impact: {year}
- Policy: {policy}

REGULATORY EVIDENCE (from SEC EDGAR, EPA, DOE, FERC):
{regulatory_evidence}

Provide a brief assessment (4-6 sentences) covering:
1. SIGNIFICANCE: How material is this signal for gas/power markets? Base this on the actual article content above, not just the extracted metadata.
2. CREDIBILITY: How reliable is the status claim? Cross-reference the article content and regulatory evidence — do SEC filings, EPA permits, or DOE awards corroborate or contradict the news article?
3. RISK FACTORS: What could delay or cancel this project? Note any gaps in regulatory approvals.
4. MARKET IMPLICATION: Key takeaway for energy market participants.

Be specific and quantitative where possible. Reference specific evidence items by source and date. Do NOT repeat the input data."""


_BATCH_SUMMARY_PROMPT = """You are reviewing {n_signals} blue hydrogen/ammonia project signals assessed through the DecarbIQ energy model.

AGGREGATE METRICS:
- Total signals: {n_signals} ({high} HIGH, {medium} MEDIUM, {low} LOW confidence)
- Cumulative gas demand: +{total_gas:.4f} bcf/d
- Cumulative HH price impact: +${total_hh:.5f}/MMBtu
- Cumulative ERCOT impact: +${total_ercot:.4f}/MWh
- Signals with quantifiable impact: {with_impact}

REGULATORY EVIDENCE COVERAGE:
{evidence_coverage}

TOP SIGNALS:
{top_signals}

Provide a market outlook summary (6-10 sentences) covering:
1. OVERALL ASSESSMENT: Net market impact direction and magnitude
2. KEY MOVERS: Which 2-3 signals matter most and why
3. EVIDENCE QUALITY: How well are these signals corroborated by regulatory filings? Note any signals with strong or weak evidence backing.
4. RISK CONCENTRATION: Are impacts concentrated in one region/developer/technology?
5. TIMELINE: When will these impacts materialize (near-term vs long-term)?
6. WATCH ITEMS: What should market participants monitor next?

Be specific, quantitative, and actionable. Write for a professional energy market audience."""


def _llm_assess_signal(assessment, regulatory_evidence=None,
                       fid_info=None, article_text=None) -> str:
    """Run LLM analysis on a single MarketAssessment with full article content."""
    s = assessment.signal
    regulatory_evidence = regulatory_evidence or []
    fid_info = fid_info or {}
    article_text = article_text or ''

    # Format regulatory evidence for the prompt (full excerpts, no truncation)
    if regulatory_evidence:
        ev_lines = []
        for i, e in enumerate(regulatory_evidence, 1):
            source = e.get('source', 'unknown')
            doc_type = e.get('document_type', '?')
            doc_date = e.get('document_date', '?')
            doc_url = e.get('document_url', '')
            excerpt = e.get('raw_text_excerpt', '') or ''
            stage = e.get('stage', '')
            project = e.get('project_name', '')
            ev_lines.append(
                f"  [{i}] {source} / {doc_type} ({doc_date})"
                f"{f' — Project: {project}' if project else ''}"
                f"{f' — Stage: {stage}' if stage else ''}\n"
                f"      Excerpt: {excerpt}"
                f"{f'  URL: {doc_url}' if doc_url else ''}"
            )
        evidence_text = '\n'.join(ev_lines)
    else:
        evidence_text = '  (No regulatory evidence found for this developer/project)'

    # Determine FID source label
    fid_source_label = fid_info.get('source', 'static_status_lookup')
    if fid_info.get('reasoning'):
        fid_source_label += f" — {fid_info['reasoning']}"

    # Get the actual FID probability used
    fid_applied = assessment.model_evidence.get(
        'fid_probability_applied',
        STATUS_FID_PROBABILITY.get(s.status, 0.10))

    # Build article content section
    if article_text:
        article_content = article_text
    else:
        article_content = '(Article full text not available — only title and metadata were collected.)'

    prompt = _SIGNAL_ASSESSMENT_PROMPT.format(
        project_name=s.project_name or 'Unknown',
        developer=(s.developer or 'Unknown').title(),
        capacity=f"{s.capacity_mtpa_h2:.4f}" if s.capacity_mtpa_h2 else 'N/A',
        technology=s.technology or 'Unknown',
        status=s.status or 'Unknown',
        fid_prob=f"{(fid_applied * 100):.0f}",
        fid_source=fid_source_label,
        location=f"{s.location_subregion or '—'}, {s.location_state or '—'} ({s.region or 'Unknown'})",
        epc=(s.epc_contractor or 'N/A').title(),
        title=s.article_title or '(no title)',
        article_content=article_content,
        gas_demand=f"{assessment.incremental_gas_demand_bcfd:.4f}" if assessment.incremental_gas_demand_bcfd else '0',
        hh_impact=f"{assessment.gas_price_impact_per_mmbtu:.5f}" if assessment.gas_price_impact_per_mmbtu else '0',
        ercot_impact=f"{assessment.ercot_impact_per_mwh:.4f}" if assessment.ercot_impact_per_mwh else '0',
        year=assessment.year_of_impact,
        policy=assessment.policy_scenario_match or 'None detected',
        regulatory_evidence=evidence_text,
    )
    return _assessment_llm_generate(prompt, max_tokens=500)


def _llm_assess_batch_summary(assessments, evidence_by_company=None) -> str:
    """Run LLM analysis on the full batch of assessments."""
    evidence_by_company = evidence_by_company or {}

    conf_counts = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
    total_gas = total_hh = total_ercot = 0.0
    with_impact = 0
    for a in assessments:
        conf_counts[a.confidence] = conf_counts.get(a.confidence, 0) + 1
        if a.incremental_gas_demand_bcfd:
            total_gas += a.incremental_gas_demand_bcfd
            total_hh += a.gas_price_impact_per_mmbtu or 0
            total_ercot += a.ercot_impact_per_mwh or 0
            with_impact += 1

    # Top 5 signals by gas demand
    top = sorted(assessments, key=lambda a: a.incremental_gas_demand_bcfd or 0, reverse=True)[:5]
    top_lines = []
    for a in top:
        s = a.signal
        gas = f"+{a.incremental_gas_demand_bcfd:.4f} bcf/d" if a.incremental_gas_demand_bcfd else "N/A"
        fid_src = a.model_evidence.get('fid_source', 'static')
        top_lines.append(
            f"- [{a.confidence}] {s.project_name or s.article_title[:50]} "
            f"({(s.developer or '?').title()}, {s.technology or '?'}, "
            f"{s.status or '?'}) -> {gas} [FID source: {fid_src}]"
        )

    # Evidence coverage summary
    if evidence_by_company:
        n_companies_with_ev = sum(1 for v in evidence_by_company.values() if v)
        total_ev = sum(len(v) for v in evidence_by_company.values())
        source_types = set()
        for ev_list in evidence_by_company.values():
            for e in ev_list:
                source_types.add(e.get('source', 'unknown'))
        evidence_coverage = (
            f"- {n_companies_with_ev} developers have regulatory evidence "
            f"({total_ev} total documents)\n"
            f"- Sources: {', '.join(sorted(source_types))}\n"
            f"- Developers without evidence rely on static status-based FID probabilities"
        )
    else:
        evidence_coverage = (
            "- No regulatory evidence loaded "
            "(all FID probabilities are static defaults)")

    prompt = _BATCH_SUMMARY_PROMPT.format(
        n_signals=len(assessments),
        high=conf_counts['HIGH'],
        medium=conf_counts['MEDIUM'],
        low=conf_counts['LOW'],
        total_gas=total_gas,
        total_hh=total_hh,
        total_ercot=total_ercot,
        with_impact=with_impact,
        top_signals='\n'.join(top_lines),
        evidence_coverage=evidence_coverage,
    )
    return _assessment_llm_generate(prompt, max_tokens=800)


# ---------------------------------------------------------------------------
# Evidence-to-signal matching & enrichment (Option 7 wiring to Options 1 & 4)
# ---------------------------------------------------------------------------

# Mirror of _TYPE_PRIORITY from fid_probability.py (avoid circular import)
_EVIDENCE_TYPE_PRIORITY = {
    '8-K': 1.0, '8-K/A': 1.0, '10-K': 0.8, '10-Q': 0.7,
    'S-1': 0.6, 'S-4': 0.6, 'EX-99.1': 0.7,
    'facility': 0.5, 'award': 0.7,
    'lpo_project': 0.8, 'h2hub_project': 0.85,
    'press_release': 0.5, 'ferc_filing': 0.6,
    'regulatory_notice': 0.55, 'lpo_mention': 0.6,
}

# SIC → technology hint (LLM-overridable, not deterministic)
_SIC_TECHNOLOGY_HINTS = {
    '2813': ('SMR', 'Industrial Gases — typically SMR-based hydrogen'),
    '2819': (None, 'Industrial Inorganic Chemicals — ambiguous'),
    '2869': ('SMR', 'Industrial Organic Chemicals — may indicate ammonia synthesis'),
    '2873': ('SMR', 'Nitrogenous Fertilizers — typically SMR-based ammonia'),
    '2911': ('ATR', 'Petroleum Refining — ATR more common for refinery H2'),
    '4922': (None, 'Natural Gas Transmission — pipeline/storage, not production'),
    '4924': (None, 'Natural Gas Distribution — not production'),
}

# Capacity extraction patterns with conversion factors
_CAPACITY_PATTERNS = [
    # "500 tonnes per day" / "500 TPD" / "500 t/d" / "500 metric tons per day"
    (re.compile(
        r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:metric\s+)?(?:tons?|tonnes?|TPD|t)\s*'
        r'(?:per\s*day|/\s*d(?:ay)?)', re.IGNORECASE),
     lambda m: float(m.group(1).replace(',', '')) * 365 / 1_000_000),  # TPD → MTPA

    # "1.2 MTPA"
    (re.compile(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*MTPA', re.IGNORECASE),
     lambda m: float(m.group(1).replace(',', ''))),

    # "1,200 KTPA"
    (re.compile(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*KTPA', re.IGNORECASE),
     lambda m: float(m.group(1).replace(',', '')) / 1_000),

    # "1200000 TPA"
    (re.compile(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*TPA', re.IGNORECASE),
     lambda m: float(m.group(1).replace(',', '')) / 1_000_000),

    # "500 kg per day" / "500 kg/d"
    (re.compile(
        r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:kg|kilogram)s?\s*'
        r'(?:per\s*day|/\s*d(?:ay)?)', re.IGNORECASE),
     lambda m: float(m.group(1).replace(',', '')) * 365 / 1e9),  # kg/d → MTPA

    # "100 MW electrolyzer" — MW → MTPA conversion
    # PEM: ~55 kg H2/hr per MW; Alkaline: ~52 kg/hr; SOEC: ~40 kg/hr
    # Using 55 (PEM, most common for new projects)
    (re.compile(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:MW|megawatt)', re.IGNORECASE),
     lambda m: float(m.group(1).replace(',', '')) * 55 * 8760 / 1e9),  # MW → MTPA (PEM)
]


def _days_since_evidence(date_str: str) -> int:
    """Days since a date string. Returns 365 on failure."""
    if not date_str:
        return 365
    try:
        dt = datetime.fromisoformat(date_str[:10])
        return (datetime.now() - dt).days
    except Exception:
        return 365


def _match_signal_to_fid(signal, fid_assessments_by_company):
    """Match a ProjectSignal to the best FID assessment.

    Matching priority:
    1. project_name match within developer's assessments
    2. Single-project developer → use it
    3. Multiple projects → best probability
    4. Substring match on developer name as fallback

    Returns (fid_dict, match_method) or (None, None).
    """
    developer = (signal.developer or '').lower().strip()
    project = (signal.project_name or '').lower().strip()

    if not developer:
        return None, None

    # Get all assessments for this developer
    assessments = fid_assessments_by_company.get(developer, [])
    if not assessments:
        # Try partial match on developer name
        for key, assmts in fid_assessments_by_company.items():
            if developer in key or key in developer:
                assessments = assmts
                break

    if not assessments:
        return None, None

    # Priority 1: project_name match
    if project:
        for a in assessments:
            a_proj = (a.get('project_name', '') or '').lower()
            if project in a_proj or a_proj in project:
                return a, 'project_name_match'

    # Priority 2: single project → use it
    if len(assessments) == 1:
        return assessments[0], 'developer_single_project'

    # Priority 3: best probability among multiple
    best = max(assessments, key=lambda a: a.get('probability', 0))
    return best, 'developer_best_match'


def _match_signal_to_evidence(signal, evidence_by_company):
    """Match a ProjectSignal to relevant regulatory evidence items.

    Filters by project_name and location_state for granularity.
    Returns all matching evidence ranked by type_priority × recency.
    """
    developer = (signal.developer or '').lower().strip()
    project = (signal.project_name or '').lower().strip()
    state = (signal.location_state or '').lower().strip()

    if not developer:
        return []

    # Get all evidence for this developer
    evidence = list(evidence_by_company.get(developer, []))
    if not evidence:
        for key, ev in evidence_by_company.items():
            if developer in key or key in developer:
                evidence = list(ev)
                break

    if not evidence:
        return []

    # Filter by project_name if available
    if project:
        project_matched = [e for e in evidence
                           if project in (e.get('project_name', '') or '').lower()
                           or (e.get('project_name', '') or '').lower() in project]
        if project_matched:
            evidence = project_matched

    # Filter by location_state if available
    if state:
        state_matched = [e for e in evidence
                         if (e.get('state', '') or '').lower() == state]
        if state_matched:
            evidence = state_matched

    # Rank by type priority × recency
    for e in evidence:
        days = _days_since_evidence(e.get('document_date', ''))
        recency = max(0, 1.0 - (days / 365) * 0.3)
        type_score = _EVIDENCE_TYPE_PRIORITY.get(e.get('document_type', ''), 0.3)
        e['_relevance'] = type_score * recency

    evidence.sort(key=lambda x: -x.get('_relevance', 0))
    return evidence  # No limit — return ALL matched evidence


def _enrich_signal_from_evidence(signal, evidence_items):
    """Fill missing capacity/technology from regulatory evidence.

    Technology: SIC code hints (LLM-overridable) + keyword search in excerpts.
    Capacity: Regex extraction from DOE award descriptions.

    Modifies signal in-place. Returns list of enrichment notes for audit trail.
    """
    notes = []

    # Technology enrichment from EPA SIC codes and excerpt keywords
    if not signal.technology or signal.technology == 'Unknown':
        for e in evidence_items:
            excerpt = (e.get('raw_text_excerpt', '') or '').lower()
            source = (e.get('source', '') or '')

            # Check for SIC codes in EPA evidence (hints, not deterministic)
            if 'epa' in source:
                for sic, (tech_hint, reason) in _SIC_TECHNOLOGY_HINTS.items():
                    if sic in excerpt and tech_hint is not None:
                        signal.technology = tech_hint
                        notes.append(
                            f"Technology hint: {tech_hint} from EPA SIC {sic} "
                            f"({reason})")
                        break

            # Check for explicit tech mentions in any evidence
            if not signal.technology or signal.technology == 'Unknown':
                if 'autothermal reform' in excerpt or ' atr ' in f' {excerpt} ':
                    signal.technology = 'ATR'
                    notes.append(f"Technology set to ATR from {source} excerpt")
                    break
                elif 'steam methane reform' in excerpt or ' smr ' in f' {excerpt} ':
                    signal.technology = 'SMR'
                    notes.append(f"Technology set to SMR from {source} excerpt")
                    break
                elif 'electrolysis' in excerpt or 'electrolyzer' in excerpt or 'electrolyser' in excerpt:
                    signal.technology = 'Electrolysis'
                    notes.append(f"Technology set to Electrolysis from {source} excerpt")
                    break

            if signal.technology and signal.technology != 'Unknown':
                break

    # Capacity enrichment from DOE award descriptions
    if not signal.capacity_mtpa_h2:
        for e in evidence_items:
            source = (e.get('source', '') or '')
            if 'doe' in source or 'usaspending' in source:
                excerpt = e.get('raw_text_excerpt', '') or ''
                for pattern, converter in _CAPACITY_PATTERNS:
                    match = pattern.search(excerpt)
                    if match:
                        try:
                            capacity = converter(match)
                            if 0.001 <= capacity <= 10.0:  # Sanity check
                                signal.capacity_mtpa_h2 = capacity
                                notes.append(
                                    f"Capacity {capacity:.4f} MTPA extracted "
                                    f"from {source}: {match.group(0)}")
                                break
                        except (ValueError, IndexError):
                            pass
                if signal.capacity_mtpa_h2:
                    break

    return notes


# -- Sensitivity variable suggestion prompt ---

_SENSITIVITY_SUGGEST_PROMPT = """You are analyzing blue hydrogen/ammonia news signals to determine which energy market variables should be stress-tested.

NEWS ASSESSMENT SUMMARY:
- Total signals: {n_signals} ({high} HIGH, {medium} MEDIUM, {low} LOW confidence)
- Cumulative gas demand: +{total_gas:.4f} bcf/d
- Cumulative HH price impact: +${total_hh:.5f}/MMBtu
- Cumulative ERCOT impact: +${total_ercot:.4f}/MWh

TOP SIGNALS:
{top_signals}

AVAILABLE SENSITIVITY VARIABLES:
{variable_list}

AVAILABLE MULTI-SHOCK SCENARIOS:
{scenario_list}

Based on the news signals above, select the 3-5 most relevant sensitivity variables AND any matching multi-shock scenarios to stress-test. Explain WHY each is relevant to the current news.

Return ONLY valid JSON:
{{
  "variables": [
    {{"name": "exact_variable_name", "direction": "plus_1sigma or minus_1sigma", "reason": "1-sentence why"}}
  ],
  "scenarios": [
    {{"name": "exact_scenario_name", "reason": "1-sentence why"}}
  ],
  "rationale": "2-3 sentence overall rationale for these selections"
}}"""


def _llm_suggest_sensitivity_variables(assessments, catalog, scenarios) -> dict:
    """Ask LLM to suggest which sensitivity variables to test based on news."""
    # Build assessment summary
    conf_counts = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
    total_gas = total_hh = total_ercot = 0.0
    for a in assessments:
        conf_counts[a.confidence] = conf_counts.get(a.confidence, 0) + 1
        if a.incremental_gas_demand_bcfd:
            total_gas += a.incremental_gas_demand_bcfd
            total_hh += a.gas_price_impact_per_mmbtu or 0
            total_ercot += a.ercot_impact_per_mwh or 0

    # Top signals
    top = sorted(assessments, key=lambda a: a.incremental_gas_demand_bcfd or 0, reverse=True)[:5]
    top_lines = []
    for a in top:
        s = a.signal
        gas = f"+{a.incremental_gas_demand_bcfd:.4f} bcf/d" if a.incremental_gas_demand_bcfd else "N/A"
        top_lines.append(
            f"- [{a.confidence}] {s.project_name or s.article_title[:50]} "
            f"({(s.developer or '?').title()}, {s.technology or '?'}, "
            f"{s.status or '?'}, {s.region or '?'}) → {gas}"
        )

    # Variable catalog summary
    var_lines = []
    for vname, vinfo in sorted(catalog.items()):
        cat = vinfo.get('category', '')
        unit = vinfo.get('unit', '')
        models = ', '.join(vinfo.get('models', []))
        var_lines.append(f"- {vname} (category={cat}, unit={unit}, models={models})")

    # Scenario list
    scen_lines = []
    for sc in scenarios:
        scen_lines.append(f"- {sc['name']}: {sc.get('description', '')}")

    prompt = _SENSITIVITY_SUGGEST_PROMPT.format(
        n_signals=len(assessments),
        high=conf_counts['HIGH'],
        medium=conf_counts['MEDIUM'],
        low=conf_counts['LOW'],
        total_gas=total_gas,
        total_hh=total_hh,
        total_ercot=total_ercot,
        top_signals='\n'.join(top_lines),
        variable_list='\n'.join(var_lines),
        scenario_list='\n'.join(scen_lines) if scen_lines else '(none)',
    )

    raw = _assessment_llm_generate(prompt, max_tokens=800)
    if not raw:
        return {}

    # Parse JSON from response
    try:
        # Handle markdown code blocks
        text = raw.strip()
        if '```' in text:
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        pass
    # Strip stray backslashes the LLM sometimes appends
    import re
    cleaned = raw.replace('\\', '')
    try:
        text = cleaned.strip()
        if '```' in text:
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}


def _llm_interpret_sensitivity(var_results, scen_results, assessments) -> str:
    """Ask LLM to interpret sensitivity analysis results in context of news."""
    # Build variable results summary
    var_lines = []
    for vr in var_results:
        dyn = vr['result'].get('dynamic', {})
        var_lines.append(
            f"- {vr['name']} ({vr['direction']}): "
            f"gas={dyn.get('delta_gas', 'N/A')}, "
            f"ercot={dyn.get('delta_ercot', 'N/A')}, "
            f"caiso={dyn.get('delta_caiso', 'N/A')} "
            f"[reason: {vr['reason']}]"
        )

    scen_lines = []
    for sr in scen_results:
        dyn = sr['data'].get('dynamic', {})
        scen_lines.append(
            f"- {sr['name']}: gas={dyn.get('delta_gas')}, "
            f"ercot={dyn.get('delta_ercot')}, caiso={dyn.get('delta_caiso')}"
        )

    # News context
    top = sorted(assessments, key=lambda a: a.incremental_gas_demand_bcfd or 0, reverse=True)[:3]
    news_lines = []
    for a in top:
        s = a.signal
        news_lines.append(
            f"- {s.project_name or s.article_title[:40]} "
            f"({s.status or '?'}, +{a.incremental_gas_demand_bcfd or 0:.4f} bcf/d)"
        )

    prompt = f"""You are interpreting sensitivity analysis results for energy market variables, in the context of recent blue hydrogen/ammonia project news.

SENSITIVITY RESULTS (1-sigma shocks):
{chr(10).join(var_lines) if var_lines else '(none)'}

SCENARIO RESULTS:
{chr(10).join(scen_lines) if scen_lines else '(none)'}

TOP NEWS SIGNALS DRIVING THIS ANALYSIS:
{chr(10).join(news_lines)}

Provide an interpretation (6-10 sentences) covering:
1. COMBINED IMPACT: What do these sensitivities tell us when combined with the news signals?
2. UPSIDE/DOWNSIDE RISKS: Which direction is more likely given current market conditions?
3. KEY VARIABLE: Which single variable has the most outsized impact and why?
4. ACTIONABLE INSIGHT: What should energy market participants do with this information?

Be specific and quantitative. Reference actual delta values from the results."""

    return _assessment_llm_generate(prompt, max_tokens=600)


# ============================================================================
# CLIENT EXTENSION SYSTEM
# ============================================================================

class ClientExtension(ABC):
    """Base class for pluggable client-specific analysis modules."""
    name: str = 'Base Extension'
    description: str = ''

    @abstractmethod
    def analyze_signal(self, signal: ProjectSignal,
                       assessment: MarketAssessment) -> Dict[str, Any]:
        """Client-specific analysis of a single signal."""

    @abstractmethod
    def analyze_pipeline(self, projects: List[Project],
                         assessments: List[MarketAssessment]) -> Dict[str, Any]:
        """Client-specific analysis of the full pipeline."""

    @abstractmethod
    def generate_report_section(self, analysis: Dict[str, Any]) -> str:
        """Generate HTML section for the client report."""

    @abstractmethod
    def get_questions(self) -> List[str]:
        """Return the list of questions this extension answers."""


# ---------------------------------------------------------------------------
# REFRACTORY CLIENT EXTENSION
# ---------------------------------------------------------------------------

# Refractory lining cost for hydrogen/ammonia reformers ($/MTPA H2 capacity)
# Source: Harbison-Walker "Handbook of Refractory Practice" (2019 edition)
# CEPCI 2024 adjustment applied
REFRACTORY_COST_DEFAULTS = {
    'smr_primary_reformer':   45_000,   # $/MTPA H2
    'smr_secondary_reformer': 20_000,   # $/MTPA H2
    'atr_reformer':           55_000,   # $/MTPA H2 (higher temp -> more refractory)
    'ammonia_converter':      15_000,   # $/MTPA NH3
    'total_smr_h2_plant':     65_000,   # $/MTPA H2 (all refractory)
    'total_atr_h2_plant':     75_000,   # $/MTPA H2 (all refractory)
}


class RefractoryClientExtension(ClientExtension):
    """Addressable market sizing for refractory lining in blue H2/ammonia plants.

    Answers 5 questions:
      Q1: Which projects are likely to proceed?
      Q2: When to expect equipment orders for refractory lining?
      Q3: Which regions should we prioritize?
      Q4: Who are the EPC contractors to partner with?
      Q5: What is the addressable market size (P10/P50/P90)?
    """
    name = 'Refractory Materials'
    description = 'Addressable market sizing for refractory lining in blue H2/ammonia plants'

    def __init__(self, config: Dict[str, Any] = None):
        config = config or {}
        self.cost_overrides = config.get('cost_overrides', {})
        self.filters = config.get('filters', {})
        self.report_options = config.get('report_options', {})

        # Merge defaults with overrides
        self.costs = dict(REFRACTORY_COST_DEFAULTS)
        for k, v in self.cost_overrides.items():
            if v is not None:
                self.costs[k] = v

        self.min_capacity = self.filters.get('min_capacity_mtpa', 0.0)
        self.regions_filter = self.filters.get('regions')  # None = all
        self.tech_filter = self.filters.get('technologies')  # None = all
        self.min_fid_prob = self.filters.get('min_fid_probability', 0.0)

    def get_questions(self) -> List[str]:
        return [
            'Q1: Which projects are likely to proceed?',
            'Q2: When to expect refractory lining equipment orders?',
            'Q3: Which regions should we prioritize?',
            'Q4: Who are the EPC contractors to partner with?',
            'Q5: What is the addressable market size (P10/P50/P90)?',
        ]

    def _filter_projects(self, projects: List[Project]) -> List[Project]:
        """Apply client filters."""
        filtered = []
        for p in projects:
            if p.status == 'Cancelled':
                continue
            if self.min_capacity and (p.capacity_mtpa_h2 or 0) < self.min_capacity:
                continue
            if self.regions_filter and p.region not in self.regions_filter:
                continue
            if self.tech_filter and p.technology not in self.tech_filter:
                continue
            if p.fid_probability < self.min_fid_prob:
                continue
            filtered.append(p)
        return filtered

    def analyze_signal(self, signal: ProjectSignal,
                       assessment: MarketAssessment) -> Dict[str, Any]:
        """Estimate refractory opportunity for a single project signal."""
        if not signal.capacity_mtpa_h2 or signal.capacity_mtpa_h2 <= 0:
            return {'applicable': False}

        tech = signal.technology or 'SMR'
        cost_key = 'total_atr_h2_plant' if tech == 'ATR' else 'total_smr_h2_plant'
        unit_cost = self.costs.get(cost_key, 65_000)
        total_value = signal.capacity_mtpa_h2 * unit_cost

        fid_prob = STATUS_FID_PROBABILITY.get(signal.status or 'Announced', 0.10)
        expected_value = total_value * fid_prob

        # Refractory lining timeline
        lead_times = EQUIPMENT_LEAD_TIMES.get('refractory_lining', {})
        if isinstance(lead_times, dict):
            months = lead_times.get(tech, 8)
        else:
            months = lead_times

        return {
            'applicable': True,
            'technology': tech,
            'capacity_mtpa': signal.capacity_mtpa_h2,
            'unit_cost_per_mtpa': unit_cost,
            'total_refractory_value_usd': total_value,
            'expected_value_usd': expected_value,
            'fid_probability': fid_prob,
            'months_from_fid_to_order': months,
            'cost_source': 'Harbison-Walker (2019) + CEPCI 2024' if cost_key not in self.cost_overrides else 'Client override',
        }

    def analyze_pipeline(self, projects: List[Project],
                         assessments: List[MarketAssessment]) -> Dict[str, Any]:
        """Full pipeline analysis answering Q1-Q5."""
        filtered = self._filter_projects(projects)

        # Q1: Projects likely to proceed
        q1_projects = sorted(
            [p for p in filtered if p.fid_probability >= 0.25],
            key=lambda p: -p.fid_probability
        )

        # Q2: Equipment order timelines
        q2_timelines = []
        for p in filtered:
            if p.fid_date or p.status in ('FID', 'EPC Award', 'Construction'):
                tech = p.technology or 'SMR'
                lead = EQUIPMENT_LEAD_TIMES.get('refractory_lining', {})
                months = lead.get(tech, 8) if isinstance(lead, dict) else lead

                if p.fid_date:
                    try:
                        fid_yr = int(p.fid_date[:4])
                        order_year = fid_yr + (months / 12)
                    except (ValueError, IndexError):
                        order_year = datetime.now().year + 1
                else:
                    # Estimate based on status
                    status_to_fid_months = {
                        'FID': 0, 'EPC Award': -3, 'Construction': -6,
                    }
                    fid_offset = status_to_fid_months.get(p.status, 12)
                    order_year = datetime.now().year + (fid_offset + months) / 12

                q2_timelines.append({
                    'project': p.project_name,
                    'developer': p.developer,
                    'technology': tech,
                    'fid_probability': p.fid_probability,
                    'months_from_fid': months,
                    'estimated_order_year': round(order_year, 1),
                    'capacity_mtpa': p.capacity_mtpa_h2,
                })
        q2_timelines.sort(key=lambda x: x['estimated_order_year'])

        # Q3: Regions to prioritize (capacity x FID probability)
        q3_regions: Dict[str, Dict] = {}
        for p in filtered:
            r = p.region or 'Unknown'
            if r not in q3_regions:
                q3_regions[r] = {'count': 0, 'cap': 0.0, 'weighted_cap': 0.0, 'value': 0.0}
            q3_regions[r]['count'] += 1
            q3_regions[r]['cap'] += p.capacity_mtpa_h2 or 0
            q3_regions[r]['weighted_cap'] += (p.capacity_mtpa_h2 or 0) * p.fid_probability
            tech = p.technology or 'SMR'
            cost_key = 'total_atr_h2_plant' if tech == 'ATR' else 'total_smr_h2_plant'
            q3_regions[r]['value'] += (p.capacity_mtpa_h2 or 0) * p.fid_probability * self.costs.get(cost_key, 65_000)

        # Q4: EPC contractors
        q4_epc: Dict[str, Dict] = {}
        for p in filtered:
            epc = p.epc_contractor or 'Unknown'
            if epc not in q4_epc:
                q4_epc[epc] = {'count': 0, 'cap': 0.0}
            q4_epc[epc]['count'] += 1
            q4_epc[epc]['cap'] += p.capacity_mtpa_h2 or 0

        # Q5: Addressable market size (P10/P50/P90 via FID probability weighting)
        total_raw_value = 0.0
        total_expected_value = 0.0
        values_by_project = []
        for p in filtered:
            if not p.capacity_mtpa_h2 or p.capacity_mtpa_h2 <= 0:
                continue
            tech = p.technology or 'SMR'
            cost_key = 'total_atr_h2_plant' if tech == 'ATR' else 'total_smr_h2_plant'
            unit_cost = self.costs.get(cost_key, 65_000)
            raw_val = p.capacity_mtpa_h2 * unit_cost
            exp_val = raw_val * p.fid_probability
            total_raw_value += raw_val
            total_expected_value += exp_val
            values_by_project.append({
                'project': p.project_name, 'raw': raw_val,
                'expected': exp_val, 'fid_prob': p.fid_probability,
            })

        # P10/P50/P90 via simple scenario:
        # P90 (optimistic): all projects at face value
        # P50 (base): FID-probability-weighted
        # P10 (conservative): only projects with FID prob >= 0.75
        p90_value = total_raw_value
        p50_value = total_expected_value
        p10_value = sum(v['raw'] for v in values_by_project if v['fid_prob'] >= 0.75)

        return {
            'q1_likely_projects': [
                {'name': p.project_name, 'developer': p.developer,
                 'fid_probability': p.fid_probability, 'status': p.status,
                 'capacity_mtpa': p.capacity_mtpa_h2, 'region': p.region}
                for p in q1_projects
            ],
            'q2_order_timelines': q2_timelines,
            'q3_regions': dict(sorted(q3_regions.items(), key=lambda x: -x[1]['value'])),
            'q4_epc_contractors': dict(sorted(q4_epc.items(), key=lambda x: -x[1]['count'])),
            'q5_market_size': {
                'p10_usd': p10_value,
                'p50_usd': p50_value,
                'p90_usd': p90_value,
                'project_count': len(values_by_project),
                'by_project': values_by_project,
                'methodology': (
                    'P90 = all pipeline at face value; '
                    'P50 = FID-probability-weighted; '
                    'P10 = only projects with FID prob >= 75%'
                ),
                'cost_basis': 'Harbison-Walker (2019) + CEPCI 2024, with client overrides where provided',
            },
            'filters_applied': {
                'min_capacity_mtpa': self.min_capacity,
                'regions': self.regions_filter,
                'technologies': self.tech_filter,
                'min_fid_probability': self.min_fid_prob,
            },
        }

    def generate_report_section(self, analysis: Dict[str, Any]) -> str:
        """Generate HTML section for the refractory client report."""
        q1 = analysis.get('q1_likely_projects', [])
        q2 = analysis.get('q2_order_timelines', [])
        q3 = analysis.get('q3_regions', {})
        q4 = analysis.get('q4_epc_contractors', {})
        q5 = analysis.get('q5_market_size', {})

        # Q1 table
        q1_rows = ''
        for p in q1[:15]:
            cap = f"{p['capacity_mtpa']:.3f}" if p.get('capacity_mtpa') else 'N/A'
            q1_rows += f"<tr><td>{p['name']}</td><td>{p.get('developer', 'N/A')}</td><td>{cap}</td><td>{p.get('status', '?')}</td><td>{p['fid_probability']:.0%}</td><td>{p.get('region', 'N/A')}</td></tr>"

        # Q2 table
        q2_rows = ''
        for t in q2[:15]:
            cap = f"{t['capacity_mtpa']:.3f}" if t.get('capacity_mtpa') else 'N/A'
            q2_rows += f"<tr><td>{t['project']}</td><td>{t.get('developer', 'N/A')}</td><td>{t['technology']}</td><td>{t['months_from_fid']}mo</td><td>{t['estimated_order_year']}</td><td>{t['fid_probability']:.0%}</td><td>{cap}</td></tr>"

        # Q3 table
        q3_rows = ''
        for r, info in q3.items():
            q3_rows += f"<tr><td>{r}</td><td>{info['count']}</td><td>{info['cap']:.3f}</td><td>{info['weighted_cap']:.3f}</td><td>${info['value']:,.0f}</td></tr>"

        # Q4 table
        q4_rows = ''
        for epc, info in q4.items():
            q4_rows += f"<tr><td>{epc.title()}</td><td>{info['count']}</td><td>{info['cap']:.3f}</td></tr>"

        # Q5 summary
        p10 = q5.get('p10_usd', 0)
        p50 = q5.get('p50_usd', 0)
        p90 = q5.get('p90_usd', 0)

        return f"""
<div class="section">
<h2>6. Client Extension: Refractory Materials</h2>
<p class="note">Addressable market for refractory lining in blue H2/ammonia reformers.
Cost basis: Harbison-Walker (2019) + CEPCI 2024 adjustment. Client overrides applied where provided.</p>

<h3>Q1: Which projects are likely to proceed? (FID prob &ge; 25%)</h3>
<table>
<tr><th>Project</th><th>Developer</th><th>Capacity (MTPA)</th><th>Status</th><th>FID Prob</th><th>Region</th></tr>
{q1_rows}
</table>

<h3>Q2: When to expect refractory lining orders?</h3>
<p class="note">Lead time source: Wood Mackenzie "Blue Hydrogen Guide" (2023); Technip Energies project data</p>
<table>
<tr><th>Project</th><th>Developer</th><th>Tech</th><th>Lead Time</th><th>Est. Order Year</th><th>FID Prob</th><th>Capacity</th></tr>
{q2_rows}
</table>

<h3>Q3: Which regions to prioritize?</h3>
<table>
<tr><th>Region</th><th>Projects</th><th>Total Cap (MTPA)</th><th>Weighted Cap</th><th>Expected Value</th></tr>
{q3_rows}
</table>

<h3>Q4: EPC contractors to partner with</h3>
<table>
<tr><th>Contractor</th><th>Projects</th><th>Total Cap (MTPA)</th></tr>
{q4_rows}
</table>

<h3>Q5: Addressable Market Size</h3>
<div class="stat-grid">
    <div class="stat-card">
        <div class="value">${p10:,.0f}</div>
        <div class="label">P10 (Conservative)</div>
    </div>
    <div class="stat-card">
        <div class="value">${p50:,.0f}</div>
        <div class="label">P50 (Base Case)</div>
    </div>
    <div class="stat-card">
        <div class="value">${p90:,.0f}</div>
        <div class="label">P90 (Optimistic)</div>
    </div>
</div>
<p class="note">{q5.get('methodology', '')}<br>
Projects counted: {q5.get('project_count', 0)} | {q5.get('cost_basis', '')}</p>

</div>"""


# ---------------------------------------------------------------------------
# EXTENSION REGISTRY
# ---------------------------------------------------------------------------

_EXTENSION_CLASSES = {
    'RefractoryClientExtension': RefractoryClientExtension,
}


def load_extension_from_yaml(yaml_path: str) -> Optional[ClientExtension]:
    """Load a client extension from a YAML config file."""
    fp = Path(yaml_path)
    if not fp.exists():
        print(f"  Config not found: {fp}")
        return None
    with open(fp, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    ext_class_name = config.get('extension', '')
    cls = _EXTENSION_CLASSES.get(ext_class_name)
    if cls is None:
        print(f"  Unknown extension class: {ext_class_name}")
        return None
    return cls(config=config)


# ============================================================================
# INTERACTIVE MENU
# ============================================================================

class BlueH2Intelligence:
    """Interactive blue hydrogen / ammonia intelligence monitor."""

    def __init__(self):
        self.collector = BlueH2NewsCollector()
        self.parser = NewsMarketSignalParser()
        self.connector = DecarbIQMarketConnector()
        self.report_gen = ReportGenerator()
        self.fid_engine = FIDProbabilityEngine()
        self.extension: Optional[ClientExtension] = None

        # Background collection thread
        self._bg_stop = threading.Event()
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_lock = threading.Lock()  # guards collector access
        self._bg_collecting = False  # True while bg collection is running

        # Try to auto-load default extension config
        default_cfg = _CONFIG_DIR / 'refractory_client.yaml'
        if default_cfg.exists():
            self.extension = load_extension_from_yaml(str(default_cfg))
            if self.extension:
                print(f"  Auto-loaded extension: {self.extension.name}")

    # -- Collector helper ----------------------------------------------------
    def _run_collector(self, label: str, collector_fn):
        """Run a collector with standard error handling."""
        print(f"  [auto] Running {label} scan...")
        try:
            result = collector_fn()
            print(f"  [auto] {label}: {result.get('filings_new', 0)} new items.")
        except Exception as e:
            print(f"  [auto] {label} error: {e}")

    # -- Auto-cascade helpers ------------------------------------------------
    def _auto_collect(self):
        """Skip collection if DB already has articles from Option 1 or 2.

        Checks the DB — if articles exist, assumes they're current and skips.
        User runs Option 1 or 2 explicitly to refresh data.
        Only collects if DB is empty (first run).
        """
        from blue_h2_news_collector import _load_existing_url_hashes
        existing = _load_existing_url_hashes(self.collector.db_path)
        if existing:
            print(f"  [auto] Using {len(existing)} articles already in database.")
        else:
            # DB is empty — do initial collection
            print("  [auto] No articles in database — running RSS collection...")
            try:
                n = self.collector.collect_rss_only(use_llm=True)
                print(f"  [auto] {n} new articles collected.")
            except Exception as e:
                print(f"  [auto] (collection error: {e})")

        # Check freshness for each broad collector
        collectors = [
            ('efts_sec', 'EFTS SEC filings', self.fid_engine.collect_filings),
            ('epa_echo_broad', 'EPA permits', self.fid_engine.collect_permits),
            ('doe_awards', 'DOE awards', self.fid_engine.collect_doe_awards),
            ('regsgov', 'regulatory filings', self.fid_engine.collect_regulatory_filings),
        ]
        for run_type, label, collector_fn in collectors:
            last_run = self.fid_engine._get_last_collection_run_by_type(run_type)
            if last_run:
                last_dt = datetime.fromisoformat(last_run)
                if (datetime.now() - last_dt).days < 7:
                    print(f"  [auto] {label} fresh (last: {last_run[:10]})")
                    continue
            self._run_collector(label, collector_fn)

    def _auto_parse(self, days: int = 30):
        """Silently parse recent articles and update the projects table.

        Returns (signals, projects) for callers that need them.
        """
        print("  [auto] Parsing signals & updating project pipeline...", end='', flush=True)
        try:
            signals = self.parser.parse_recent(days=days)
            if signals:
                projects, _ = self.parser.deduplicate_to_projects(
                    signals, return_clusters=True)
                print(f" {len(signals)} signals -> {len(projects)} projects.")
                return signals, projects
            else:
                print(" 0 signals.")
                return [], []
        except Exception as e:
            print(f" (parse error: {e})")
            return [], []

    # -- Background collection ------------------------------------------------

    def _bg_collection_loop(self, rss_interval: int = 900,
                            full_interval: int = 7200):
        """Background daemon loop: runs RSS every ~15 min, full every ~2 hrs.

        All collection runs through the same BlueH2NewsCollector, which uses
        SQLite (thread-safe in serialized mode) and ChromaDB (PersistentClient).

        Args:
            rss_interval: seconds between RSS collections (default 900 = 15 min)
            full_interval: seconds between full collections (default 7200 = 2 hrs)
        """
        logger = logging.getLogger('bg_collector')
        # Start timers at now — first collection waits the full interval
        last_rss = time.time()
        last_full = time.time()

        while not self._bg_stop.is_set():
            now = time.time()

            try:
                # Full collection every ~2 hours
                if now - last_full >= full_interval:
                    self._bg_collecting = True
                    with self._bg_lock:
                        logger.info("[bg] Starting full collection...")
                        n = self.collector.collect_all(use_llm=True)
                        logger.info(f"[bg] Full collection: {n} new articles")
                    last_full = time.time()
                    last_rss = last_full  # full includes RSS, reset timer

                # RSS collection every ~15 minutes
                elif now - last_rss >= rss_interval:
                    self._bg_collecting = True
                    with self._bg_lock:
                        logger.info("[bg] Starting RSS collection...")
                        n = self.collector.collect_rss_only(use_llm=False)
                        logger.info(f"[bg] RSS collection: {n} new articles")
                    last_rss = time.time()

            except Exception as e:
                logger.warning(f"[bg] Collection error: {e}")
            finally:
                self._bg_collecting = False

            # Sleep in 10s increments so we can respond to stop signal quickly
            self._bg_stop.wait(timeout=10)

    def start_background_collection(self):
        """Start the background collection daemon thread."""
        if self._bg_thread is not None and self._bg_thread.is_alive():
            print("  Background collection already running.")
            return
        self._bg_stop.clear()
        self._bg_thread = threading.Thread(
            target=self._bg_collection_loop,
            daemon=True,
            name='bg-collector'
        )
        self._bg_thread.start()
        print("  Background collection started (RSS every 15 min, full every 2 hrs).")

    def stop_background_collection(self):
        """Stop the background collection daemon thread."""
        if self._bg_thread is None or not self._bg_thread.is_alive():
            print("  Background collection is not running.")
            return
        self._bg_stop.set()
        self._bg_thread.join(timeout=15)
        self._bg_thread = None
        print("  Background collection stopped.")

    def run(self):
        """Main interactive loop with background collection."""
        print(self._banner())

        # Auto-start background collection
        self.start_background_collection()

        try:
            while True:
                print(self._menu())
                choice = input("\nSelect option: ").strip()
                if choice == '0':
                    print("\n  Stopping background collection...")
                    self.stop_background_collection()
                    print("\nExiting. Goodbye.\n")
                    break
                self._dispatch(choice)
        except KeyboardInterrupt:
            print("\n\n  Interrupted — stopping background collection...")
            self.stop_background_collection()
            print("  Goodbye.\n")

    def _banner(self) -> str:
        return f"""
{'=' * 60}
  Blue H2 / Ammonia Intelligence Monitor
  Powered by DecarbIQ Market Analyzer
{'=' * 60}
  Date: {datetime.now().strftime('%B %d, %Y')}
  Extension: {self.extension.name if self.extension else 'None loaded'}
"""

    def _menu(self) -> str:
        ext_name = self.extension.name if self.extension else 'None'
        return f"""
--- NEWS COLLECTION ---
  1. Run full collection (RSS + search + scrapers + LLM)
  2. RSS-only collection (with LLM filtering)
  3. View recent articles (by score/date/region)

--- MARKET SIGNALS ---
  4. Parse recent articles -> project signals
  5. Analyze existing DB (parse + FID, no new collection)
  6. View competitive landscape (by developer / capacity)

--- DECARBIQ MARKET ASSESSMENT ---
  7. Assess recent news impact (via DecarbIQ model)
  8. View gas price outlook (P10/P50/P90, 2025-2035)
  9. View cumulative pipeline impact on gas demand
 10. Sensitivity explorer (what-if on model variables)

--- DASHBOARDS ---
 11. Competitive intelligence dashboard
 12. Deal flow tracker
 13. Risk register

--- REPORTS ---
 14. Generate market intelligence HTML report
 15. Generate client extension report

--- CLIENT EXTENSIONS [{ext_name}] ---
 16. Configure client extension module

  0. Exit"""

    def _dispatch(self, choice: str):
        handlers = {
            '1': self._collect_all,
            '2': self._collect_rss,
            '3': self._view_recent_articles,
            '4': self._parse_signals,
            '5': self._view_pipeline,
            '6': self._view_competitive,
            '7': self._assess_news,
            '8': self._gas_outlook,
            '9': self._cumulative_impact,
            '10': self._sensitivity_explorer,
            '11': self._dashboard_competitive,
            '12': self._dashboard_deals,
            '13': self._dashboard_risks,
            '14': self._generate_html_report,
            '15': self._generate_extension_report,
            '16': self._configure_extension,
        }
        fn = handlers.get(choice)
        if fn:
            try:
                fn()
            except Exception as e:
                print(f"\n  Error: {e}\n")
        else:
            print("\n  Invalid option.\n")

    # -- 1. Full collection --------------------------------------------------
    def _collect_all(self):
        if self._bg_collecting:
            print("\n  Background collection in progress — waiting for it to finish...")
        with self._bg_lock:
            n = self.collector.collect_all(use_llm=True)
        print(f"\n  Stored {n} new articles.\n")

        # Broad SEC EDGAR filing scan (EFTS + RSS)
        print("  Scanning SEC EDGAR for hydrogen/ammonia filings...")
        try:
            efts_result = self.fid_engine.collect_filings()
            print(f"  EFTS: {efts_result.get('filings_new', 0)} new filings stored "
                  f"({efts_result.get('new_companies_discovered', 0)} new companies)\n")
        except Exception as e:
            print(f"  EFTS error: {e}\n")

        # Broad EPA permit scan
        print("  Scanning EPA ECHO for hydrogen/ammonia facilities...")
        try:
            epa_result = self.fid_engine.collect_permits()
            print(f"  EPA: {epa_result.get('filings_new', 0)} new facilities stored\n")
        except Exception as e:
            print(f"  EPA error: {e}\n")

        # Broad DOE award scan
        print("  Scanning DOE USASpending + LPO/H2Hub pages...")
        try:
            doe_result = self.fid_engine.collect_doe_awards()
            print(f"  DOE: {doe_result.get('filings_new', 0)} new awards stored\n")
        except Exception as e:
            print(f"  DOE error: {e}\n")

        # Broad federal regulatory filing scan
        print("  Scanning regulations.gov (FERC/DOE/EPA/PHMSA)...")
        try:
            reg_result = self.fid_engine.collect_regulatory_filings()
            print(f"  Regulatory: {reg_result.get('filings_new', 0)} new filings stored\n")
        except Exception as e:
            print(f"  Regulatory error: {e}\n")

    # -- 2. RSS only --------------------------------------------------------
    def _collect_rss(self):
        if self._bg_collecting:
            print("\n  Background collection in progress — waiting for it to finish...")
        with self._bg_lock:
            print("\n  Collecting RSS feeds (with LLM filtering)...")
            stored = self.collector.collect_rss_only(use_llm=True)
        print(f"\n  Done: {stored} new articles stored.\n")

    # -- 3. View recent articles (generates audit HTML) -----------------------
    def _view_recent_articles(self):
        self._auto_collect()
        days = _ask_int("  Days back", 7)
        min_score = _ask_int("  Min priority score", 0)
        articles = self.collector.get_recent_articles(days=days, min_score=min_score)
        if not articles:
            print("\n  No articles found.\n")
            return

        print(f"\n  Found {len(articles)} articles. Generating audit report...")
        path = _generate_article_audit_html(articles, days, min_score,
                                             self.report_gen.output_dir,
                                             db_path=self.collector.db_path)
        print(f"  Report: {path}")
        print(f"  (Print to PDF from browser for permanent file)\n")

    # -- 4. Parse signals ----------------------------------------------------
    def _parse_signals(self):
        self._auto_collect()
        days = _ask_int("  Days back to parse", 90)

        # ---- Region selection ----
        selected_region = self._ask_region_filter()

        region_label = selected_region or 'all regions'
        print(f"\n  Parsing articles from last {days} days ({region_label})...")
        signals = self.parser.parse_recent(days=days, region=selected_region)
        if not signals:
            print(f"  No parseable signals found for {region_label}.\n")
            return
        projects, clusters = self.parser.deduplicate_to_projects(
            signals, return_clusters=True)
        print(f"  Parsed {len(signals)} signals -> {len(projects)} deduplicated projects.")

        # Run FID Probability Engine on each project (LLM-based)
        print(f"  Running FID Probability Engine on {len(projects)} projects...")
        fid_results = {}
        for p in projects:
            if not p.developer or p.developer.lower() == 'unknown':
                continue
            state = p.location_state or ''
            if len(state) != 2:
                state = ''
            key = f"{p.developer}|{state}"
            if key in fid_results:
                fid = fid_results[key]
            else:
                try:
                    fid = self.fid_engine.assess_project(
                        p.developer, state,
                        project_name=p.project_name,
                        status=p.status or 'Announced')
                    fid_results[key] = fid
                except Exception as e:
                    fid = {}
                    fid_results[key] = fid

            if fid and fid.get('probability') is not None:
                p.fid_probability = fid['probability']
                p.fid_source = fid.get('source', 'fid_engine')
                p.fid_evidence = fid.get('evidence_details', [])
                p.fid_reasoning = fid.get('reasoning', '')
                p.fid_confidence = fid.get('confidence', '')
                p.fid_stage = fid.get('stage', '')
                p.fid_evidence_count = fid.get('evidence_count', 0)

        n_engine = sum(1 for f in fid_results.values()
                       if f.get('source', '').startswith('fid_engine'))
        n_llm = sum(1 for f in fid_results.values()
                    if f.get('source') == 'fid_engine+llm')
        print(f"  FID Engine: {n_engine} developers assessed ({n_llm} via LLM).")
        print(f"  Generating audit report...")
        path = _generate_project_audit_html(
            projects, clusters, len(signals), days, self.report_gen.output_dir,
            db_path=self.collector.db_path,
            selected_region=selected_region)
        print(f"  Report: {path}")
        print(f"  (Print to PDF from browser for permanent file)\n")

    # -- 5. Analyze existing DB (no collection / no new parsing) ---------------
    def _view_pipeline(self):
        """Parse what's already in the DB and run FID analysis — no collection."""
        days = _ask_int("  Days back to analyze", 90)

        # ---- Region selection ----
        selected_region = self._ask_region_filter()

        region_label = selected_region or 'all regions'
        print(f"\n  Parsing existing articles from last {days} days "
              f"({region_label}, no new collection)...")
        signals = self.parser.parse_recent(days=days, region=selected_region)
        if not signals:
            print(f"  No parseable signals found for {region_label}.\n")
            return
        projects, clusters = self.parser.deduplicate_to_projects(
            signals, return_clusters=True)
        print(f"  Parsed {len(signals)} signals -> {len(projects)} deduplicated projects.")

        # Run FID Probability Engine on each project
        print(f"  Running FID Probability Engine on {len(projects)} projects...")
        fid_results = {}
        for p in projects:
            if not p.developer or p.developer.lower() == 'unknown':
                continue
            state = p.location_state or ''
            if len(state) != 2:
                state = ''
            key = f"{p.developer}|{state}"
            if key in fid_results:
                fid = fid_results[key]
            else:
                try:
                    fid = self.fid_engine.assess_project(
                        p.developer, state,
                        project_name=p.project_name,
                        status=p.status or 'Announced')
                    fid_results[key] = fid
                except Exception as e:
                    fid = {}
                    fid_results[key] = fid

            if fid and fid.get('probability') is not None:
                p.fid_probability = fid['probability']
                p.fid_source = fid.get('source', 'fid_engine')
                p.fid_evidence = fid.get('evidence_details', [])
                p.fid_reasoning = fid.get('reasoning', '')
                p.fid_confidence = fid.get('confidence', '')
                p.fid_stage = fid.get('stage', '')
                p.fid_evidence_count = fid.get('evidence_count', 0)

        n_engine = sum(1 for f in fid_results.values()
                       if f.get('source', '').startswith('fid_engine'))
        n_llm = sum(1 for f in fid_results.values()
                    if f.get('source') == 'fid_engine+llm')
        print(f"  FID Engine: {n_engine} developers assessed ({n_llm} via LLM).")
        print(f"  Generating audit report...")
        path = _generate_project_audit_html(
            projects, clusters, len(signals), days, self.report_gen.output_dir,
            db_path=self.collector.db_path,
            selected_region=selected_region)
        print(f"  Report: {path}")
        print(f"  (Print to PDF from browser for permanent file)\n")

    # -- 6. Competitive landscape --------------------------------------------
    def _view_competitive(self):
        self._auto_collect()
        self._auto_parse()
        projects = self.parser.get_project_pipeline()
        if not projects:
            print("  No projects (no parseable articles found).\n")
            return

        print(f"  Generating competitive landscape report...")
        path = _generate_competitive_audit_html(
            projects, self.report_gen.output_dir)
        print(f"  Report: {path}")
        print(f"  (Print to PDF from browser for permanent file)\n")

    # -- 7. Assess news impact -----------------------------------------------
    def _ask_region_filter(self) -> Optional[str]:
        """Show available regions from the DB and let user pick one.

        Returns the selected region string, or None for all regions.
        """
        import sqlite3
        conn = sqlite3.connect(self.collector.db_path)
        rows = conn.execute('''
            SELECT region, COUNT(*) as cnt FROM articles
            WHERE region IS NOT NULL AND region != ''
            GROUP BY region
            ORDER BY cnt DESC
        ''').fetchall()
        conn.close()

        if not rows:
            print("  No articles with region data in the database.")
            return None

        # Group into US regions and international
        us_regions = []
        intl_regions = []
        _US_REGION_NAMES = {
            'Gulf Coast TX', 'Gulf Coast LA', 'Appalachia', 'West Coast',
            'Pacific Northwest', 'Midwest', 'Mountain West', 'Southeast',
            'Northeast', 'Plains', 'US Other', 'Alaska', 'Hawaii',
        }
        for region, cnt in rows:
            if region in _US_REGION_NAMES:
                us_regions.append((region, cnt))
            elif region != 'Unknown':
                intl_regions.append((region, cnt))

        print("\n  ┌─────────────────────────────────────────┐")
        print("  │          SELECT REGION                   │")
        print("  └─────────────────────────────────────────┘")
        print(f"  [0] All regions")

        idx = 1
        region_map = {}

        if us_regions:
            print("  ── US Regions ──")
            for region, cnt in us_regions:
                print(f"  [{idx}] {region} ({cnt} articles)")
                region_map[idx] = region
                idx += 1

        if intl_regions:
            print("  ── International ──")
            for region, cnt in intl_regions:
                print(f"  [{idx}] {region} ({cnt} articles)")
                region_map[idx] = region
                idx += 1

        # Unknown count
        unknown_count = sum(cnt for region, cnt in rows if region == 'Unknown')
        if unknown_count:
            print(f"  [{idx}] Unknown ({unknown_count} articles)")
            region_map[idx] = 'Unknown'

        while True:
            choice = input("\n  Select region: ").strip()
            if not choice or choice == '0':
                print("  → All regions selected")
                return None
            try:
                choice_int = int(choice)
                if choice_int in region_map:
                    selected = region_map[choice_int]
                    print(f"  → {selected} selected")
                    return selected
            except ValueError:
                # Allow typing region name directly
                for region, _ in rows:
                    if choice.lower() == region.lower():
                        print(f"  → {region} selected")
                        return region
            print(f"  Invalid selection. Enter 0-{max(region_map.keys())} or a region name.")

    def _assess_news(self):
        days = _ask_int("  Days back to assess", 90)

        # ---- Region selection ----
        selected_region = self._ask_region_filter()

        signals = self.parser.parse_recent(days=days, region=selected_region)
        if not signals:
            region_label = selected_region or 'all regions'
            print(f"  No signals found for {region_label} in the last {days} days.\n")
            return

        region_label = selected_region or 'all regions'
        print(f"\n  Assessing {len(signals)} signals for {region_label} "
              f"through DecarbIQ model...")

        # ---- Batch-load evidence for all unique developers ----
        developer_names = set()
        for s in signals:
            if s.developer and s.developer.lower() != 'unknown':
                developer_names.add(s.developer)

        developer_list = list(developer_names)
        print(f"  Loading regulatory evidence for {len(developer_list)} developers...")

        evidence_by_company = self.fid_engine.batch_load_evidence(developer_list)
        fid_by_company = self.fid_engine.batch_load_assessments(developer_list)

        n_with_evidence = sum(1 for v in evidence_by_company.values() if v)
        n_with_fid = sum(1 for v in fid_by_company.values() if v)
        total_evidence = sum(len(v) for v in evidence_by_company.values())
        print(f"  Loaded {total_evidence} evidence items for {n_with_evidence} developers, "
              f"{n_with_fid} with FID assessments.")

        # ---- Build per-signal FID overrides and evidence maps ----
        fid_overrides = {}     # signal_index -> {probability, source, ...}
        evidence_map = {}      # signal_index -> [evidence dicts]
        enrichment_log = {}    # signal_index -> [enrichment notes]

        for i, s in enumerate(signals):
            # Match to FID assessment (project-level granularity)
            fid_match, match_method = _match_signal_to_fid(s, fid_by_company)
            if fid_match and fid_match.get('probability') is not None:
                fid_overrides[i] = {
                    'probability': fid_match['probability'],
                    'source': f"fid_engine:{match_method}",
                    'stage': fid_match.get('stage', ''),
                    'confidence': fid_match.get('confidence', ''),
                    'reasoning': fid_match.get('reasoning', ''),
                    'project_name': fid_match.get('project_name', ''),
                }

            # Match to regulatory evidence (project-level)
            matched_evidence = _match_signal_to_evidence(s, evidence_by_company)
            if matched_evidence:
                evidence_map[i] = matched_evidence

            # Enrich signal from evidence (technology, capacity)
            if matched_evidence:
                notes = _enrich_signal_from_evidence(s, matched_evidence)
                if notes:
                    enrichment_log[i] = notes

        n_overridden = len(fid_overrides)
        n_enriched = len(evidence_map)
        print(f"  FID overrides: {n_overridden}/{len(signals)} signals")
        print(f"  Evidence matched: {n_enriched}/{len(signals)} signals")
        if enrichment_log:
            print(f"  Enrichments applied: {len(enrichment_log)}")
            for sig_idx, notes in list(enrichment_log.items())[:5]:
                s = signals[sig_idx]
                label = s.project_name or s.developer or f'Signal #{sig_idx+1}'
                for n in notes:
                    print(f"    {label}: {n}")
            if len(enrichment_log) > 5:
                print(f"    ... and {len(enrichment_log) - 5} more")

        # ---- Run quantitative assessment with overrides ----
        assessments = self.connector.assess_batch(
            signals, fid_overrides=fid_overrides, evidence_map=evidence_map)
        print(f"  {len(assessments)} quantitative assessments complete.")

        # ---- Build signal index map (assessment.signal id → original index) ----
        signal_index_map = {}
        for i, s in enumerate(signals):
            signal_index_map[id(s)] = i

        # ---- Load article full text from DB for LLM analysis ----
        _article_text_cache: Dict[int, str] = {}
        try:
            import sqlite3 as _sq
            _conn = _sq.connect(self.collector.db_path)
            _conn.row_factory = _sq.Row
            for _row in _conn.execute('SELECT id, snippet, full_text FROM articles').fetchall():
                _r = dict(_row)
                # Prefer full_text, fall back to snippet
                _article_text_cache[_r['id']] = (
                    _strip_html(_r.get('full_text') or '')
                    or _strip_html(_r.get('snippet') or ''))
            _conn.close()
        except Exception:
            pass

        # ---- LLM analysis layer (enriched with evidence) ----
        llm_backend = _check_assessment_llm()
        signal_analyses = {}
        batch_summary = ''
        if llm_backend:
            # Per-signal LLM analysis — ALL signals, grouped by region
            region_groups = {}
            for a in assessments:
                reg = a.signal.region or 'Unknown'
                region_groups.setdefault(reg, []).append(a)
            # Sort regions by total gas demand impact (descending)
            sorted_regions = sorted(region_groups.keys(),
                                    key=lambda r: sum(
                                        a.incremental_gas_demand_bcfd or 0
                                        for a in region_groups[r]),
                                    reverse=True)
            total_signals = len(assessments)
            print(f"  LLM analyzing all {total_signals} signals across "
                  f"{len(sorted_regions)} regions ({llm_backend})...")
            global_idx = 0
            for reg in sorted_regions:
                reg_signals = region_groups[reg]
                print(f"    --- {reg} ({len(reg_signals)} signals) ---")
                for a in reg_signals:
                    global_idx += 1
                    label = a.signal.project_name or a.signal.article_title[:40]
                    print(f"    [{global_idx}/{total_signals}] {label}...",
                          end='', flush=True)
                    # Look up evidence and article text for this signal
                    sig_idx = signal_index_map.get(id(a.signal))
                    reg_ev = evidence_map.get(sig_idx, []) if sig_idx is not None else []
                    fid_info = fid_overrides.get(sig_idx, {}) if sig_idx is not None else {}
                    art_text = _article_text_cache.get(a.signal.article_id, '')
                    analysis = _llm_assess_signal(a, regulatory_evidence=reg_ev,
                                                  fid_info=fid_info,
                                                  article_text=art_text)
                    if analysis:
                        signal_analyses[id(a)] = analysis
                        print(" done.")
                    else:
                        print(" (no response)")

            # Batch market outlook (with evidence coverage)
            print(f"  LLM generating market outlook summary...", end='', flush=True)
            batch_summary = _llm_assess_batch_summary(
                assessments, evidence_by_company=evidence_by_company)
            print(" done." if batch_summary else " (no response)")
        else:
            print("  Skipping LLM analysis (no assessment LLM available).")

        # TODO: GAP 4 — Interconnection queue data not yet available.
        # When project-level queue positions are scraped (e.g., ERCOT GIS,
        # PJM queue), add them here as additional enrichment.

        print(f"  Generating report...")
        path = _generate_assessment_html(
            assessments, days, self.report_gen.output_dir,
            db_path=self.collector.db_path,
            signal_analyses=signal_analyses,
            batch_summary=batch_summary,
            llm_backend=llm_backend,
            evidence_map=evidence_map,
            fid_overrides=fid_overrides,
            enrichment_log=enrichment_log,
            signal_index_map=signal_index_map,
            selected_region=selected_region)
        print(f"  Report: {path}")
        print(f"  (Print to PDF from browser for permanent file)\n")

    # -- 8. Gas outlook ------------------------------------------------------
    def _gas_outlook(self):
        outlook = self.connector.get_gas_price_outlook()
        if not outlook:
            print(f"  No Monte Carlo data loaded.")
            print(f"  Check that {self.connector.model_dir / 'geopolitical_and_macro' / 'monte_carlo_lng_v8.json'} exists.\n")
            return
        print(f"\n  {'Year':>6s} | {'P10':>8s} | {'P50':>8s} | {'P90':>8s} | {'Mean':>8s}")
        print(f"  {'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
        for yr in sorted(outlook.keys()):
            d = outlook[yr]
            def f(v): return f"${v:.2f}" if v else "N/A"
            print(f"  {yr:>6s} | {f(d.get('p10')):>8s} | {f(d.get('p50')):>8s} | "
                  f"{f(d.get('p90')):>8s} | {f(d.get('mean')):>8s}")
        print(f"\n  Source: DecarbIQ Monte Carlo v8 (10k paths, regime-switching GARCH)\n")

    # -- 9. Cumulative impact ------------------------------------------------
    def _cumulative_impact(self):
        self._auto_collect()
        self._auto_parse()
        projects = self.parser.get_project_pipeline()
        if not projects:
            print("  No projects (no parseable articles found).\n")
            return
        impact = self.connector.get_cumulative_pipeline_impact(projects)
        print(f"""
  === Cumulative Pipeline Impact ===

  Active projects:            {impact['n_projects_active']}
  Total pipeline:             {impact['total_pipeline_capacity_mtpa']:.2f} MTPA H2
  Prob-weighted capacity:     {impact['probability_weighted_capacity_mtpa']:.2f} MTPA H2
  Total gas demand:           {impact['total_gas_demand_bcfd']:.3f} bcf/d
  Weighted gas demand:        {impact['weighted_gas_demand_bcfd']:.3f} bcf/d
  Gas price impact:           ${impact['gas_price_impact_per_mmbtu']:.4f}/MMBtu
  ERCOT electricity impact:   ${impact['ercot_impact_per_mwh']:.4f}/MWh
  LCOH impact:                ${impact['lcoh_impact_per_kg']:.5f}/kg

  By region:""")
        for r, v in sorted(impact['by_region'].items(), key=lambda x: -x[1]):
            print(f"    {r:20s}: {v:.4f} bcf/d")
        print(f"\n  By technology:")
        for t, v in sorted(impact['by_technology'].items(), key=lambda x: -x[1]):
            print(f"    {t:20s}: {v:.4f} bcf/d")
        print(f"\n  Model evidence: coefficient = {impact['model_evidence']['coefficient']}")
        print(f"  Source: {impact['model_evidence']['source']}\n")

    # -- 10. Sensitivity explorer (LLM-driven) --------------------------------
    def _sensitivity_explorer(self):
        catalog = self.connector.sensitivity.get('variable_catalog', {})
        if not catalog:
            print(f"  No sensitivity catalog loaded.")
            print(f"  Check that {self.connector.model_dir / 'decarbiq_sensitivity_analysis.json'} exists.\n")
            return

        sens_matrix = self.connector.sensitivity.get('sensitivity_matrix', {})
        scenarios = self.connector.sensitivity.get('multi_shock_scenarios', [])

        # Step 1: Run assessment (same as Option 7 but silent)
        self._auto_collect()
        days = _ask_int("  Days back", 90)
        self._auto_parse(days=days)
        signals = self.parser.parse_recent(days=days)
        if not signals:
            print("  No signals — cannot suggest variables.\n")
            return
        print(f"\n  Assessing {len(signals)} signals...")
        assessments = self.connector.assess_batch(signals)

        # Step 2: LLM suggests variables based on news
        llm_backend = _check_assessment_llm()
        if not llm_backend:
            print("  No assessment LLM available — falling back to manual mode.\n")
            self._sensitivity_manual(catalog, sens_matrix)
            return

        print(f"  LLM selecting sensitivity variables ({llm_backend})...", end='', flush=True)
        suggestion = _llm_suggest_sensitivity_variables(assessments, catalog, scenarios)
        if not suggestion or not suggestion.get('variables'):
            print(" (no suggestion — falling back to manual mode)\n")
            self._sensitivity_manual(catalog, sens_matrix)
            return
        print(" done.\n")

        # Step 3: Display LLM rationale
        if suggestion.get('rationale'):
            print(f"  LLM Rationale: {suggestion['rationale']}\n")

        # Step 4: Look up sensitivity results for suggested variables
        var_results = []
        for sv in suggestion.get('variables', []):
            vname = sv.get('name', '')
            direction = sv.get('direction', 'plus_1sigma')
            reason = sv.get('reason', '')
            key = f"{vname}__{direction}"

            info = catalog.get(vname, {})
            result = sens_matrix.get(key, {})
            var_results.append({
                'name': vname,
                'direction': direction,
                'reason': reason,
                'info': info,
                'result': result,
            })

        # Step 5: Look up suggested scenarios
        scen_results = []
        for ss in suggestion.get('scenarios', []):
            sname = ss.get('name', '')
            reason = ss.get('reason', '')
            match = None
            for sc in scenarios:
                if sname.lower() in sc.get('name', '').lower():
                    match = sc
                    break
            if match:
                scen_results.append({
                    'name': match['name'],
                    'description': match.get('description', ''),
                    'reason': reason,
                    'data': match,
                })

        # Step 6: LLM interprets the results
        print(f"  LLM interpreting sensitivity results...", end='', flush=True)
        interpretation = _llm_interpret_sensitivity(
            var_results, scen_results, assessments)
        print(" done.\n" if interpretation else " (no response)\n")

        # Step 7: Generate HTML report
        print(f"  Generating sensitivity report...")
        path = _generate_sensitivity_html(
            var_results, scen_results, suggestion,
            interpretation, assessments, days,
            self.report_gen.output_dir,
            llm_backend=llm_backend)
        print(f"  Report: {path}")
        print(f"  (Print to PDF from browser for permanent file)\n")

    def _sensitivity_manual(self, catalog, sens_matrix):
        """Fallback manual variable selection (original Option 10 behavior)."""
        print("  Available variables:")
        vars_list = sorted(catalog.keys())
        for i, v in enumerate(vars_list, 1):
            cat = catalog[v].get('category', '')
            unit = catalog[v].get('unit', '')
            print(f"    {i:3d}. {v:35s} [{cat}] {unit}")

        idx_str = input(f"\n  Select variable (1-{len(vars_list)}): ").strip()
        try:
            idx = int(idx_str) - 1
            var = vars_list[idx]
        except (ValueError, IndexError):
            print("  Invalid selection.\n")
            return

        info = catalog[var]
        print(f"\n  Variable: {var}")
        print(f"  Baseline: {info.get('baseline_mean')}")
        print(f"  Sigma (1): {info.get('sigma_1')}")
        print(f"  Unit: {info.get('unit', 'N/A')}")

        for direction in ['plus_1sigma', 'minus_1sigma']:
            key = f"{var}__{direction}"
            result = sens_matrix.get(key, {})
            if result:
                dyn = result.get('dynamic', {})
                print(f"\n  {direction}:")
                print(f"    Gas: {dyn.get('delta_gas', 'N/A')}")
                print(f"    ERCOT: {dyn.get('delta_ercot', 'N/A')}")
                print(f"    CAISO: {dyn.get('delta_caiso', 'N/A')}")
        print()

    # -- 11. Competitive dashboard -------------------------------------------
    def _dashboard_competitive(self):
        self._auto_collect()
        self._auto_parse()
        projects = self.parser.get_project_pipeline()
        if not projects:
            print("  No data (no parseable articles found).\n")
            return
        active = [p for p in projects if p.status != 'Cancelled']
        print(f"\n  === Competitive Intelligence Dashboard ===")
        print(f"  Total active projects: {len(active)}")
        print(f"  Total pipeline: {sum(p.capacity_mtpa_h2 or 0 for p in active):.2f} MTPA H2\n")

        # Top developers
        devs: Dict[str, float] = {}
        for p in active:
            d = p.developer or 'Unknown'
            devs[d] = devs.get(d, 0) + (p.capacity_mtpa_h2 or 0)
        print("  Top Developers by Capacity:")
        for d, cap in sorted(devs.items(), key=lambda x: -x[1])[:10]:
            bar = '#' * int(cap * 20)
            print(f"    {d.title():25s} {cap:8.3f} MTPA  {bar}")

        # Technology split
        techs: Dict[str, int] = {}
        for p in active:
            t = p.technology or 'Unknown'
            techs[t] = techs.get(t, 0) + 1
        print(f"\n  Technology split:")
        for t, n in sorted(techs.items(), key=lambda x: -x[1]):
            print(f"    {t}: {n}")
        print()

    # -- 12. Deal flow tracker -----------------------------------------------
    def _dashboard_deals(self):
        self._auto_collect()
        self._auto_parse()
        import sqlite3
        conn = sqlite3.connect(self.collector.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT * FROM deals ORDER BY deal_date DESC LIMIT 20').fetchall()
        conn.close()
        if not rows:
            print("  No deals recorded. Deals are populated during signal parsing.\n")
            return
        print(f"\n  === Deal Flow Tracker (Last 20) ===\n")
        for r in rows:
            d = dict(r)
            val = f"${d.get('value_usd', 0):,.0f}" if d.get('value_usd') else 'N/A'
            print(f"  {d.get('deal_date', 'N/A'):12s} | {d.get('deal_type', '?'):15s} | "
                  f"{d.get('parties', '')[:40]:40s} | {val}")
        print()

    # -- 13. Risk register ---------------------------------------------------
    def _dashboard_risks(self):
        self._auto_collect()
        self._auto_parse()
        import sqlite3
        conn = sqlite3.connect(self.collector.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT * FROM risks ORDER BY last_updated DESC LIMIT 20').fetchall()
        conn.close()
        if not rows:
            print("  No risks recorded.\n")
            return
        print(f"\n  === Risk Register (Last 20) ===\n")
        for r in rows:
            d = dict(r)
            print(f"  [{d.get('impact_level', '?'):8s}] {d.get('risk_category', ''):20s} | "
                  f"{d.get('risk_description', '')[:60]}")
        print()

    # -- 14. HTML report -----------------------------------------------------
    def _generate_html_report(self):
        self._auto_collect()
        self._auto_parse()
        projects = self.parser.get_project_pipeline()
        signals = self.parser.parse_recent(days=30)
        assessments = self.connector.assess_batch(signals)

        ext_section = None
        if self.extension:
            analysis = self.extension.analyze_pipeline(projects, assessments)
            ext_section = self.extension.generate_report_section(analysis)

        path = self.report_gen.generate_html(
            projects, assessments, self.connector,
            extension_section=ext_section,
        )
        print(f"  Report: {path}\n")

    # -- 15. Extension report ------------------------------------------------
    def _generate_extension_report(self):
        if not self.extension:
            print("  No extension configured. Use option 16 first.\n")
            return
        self._auto_collect()
        self._auto_parse()
        projects = self.parser.get_project_pipeline()
        signals = self.parser.parse_recent(days=30)
        assessments = self.connector.assess_batch(signals)
        analysis = self.extension.analyze_pipeline(projects, assessments)

        # Print Q1-Q5 to console
        print(f"\n  === {self.extension.name} Analysis ===\n")
        for q in self.extension.get_questions():
            print(f"  {q}")
        print()

        q5 = analysis.get('q5_market_size', {})
        if q5:
            print(f"  Addressable Market:")
            print(f"    P10 (conservative): ${q5.get('p10_usd', 0):,.0f}")
            print(f"    P50 (base case):    ${q5.get('p50_usd', 0):,.0f}")
            print(f"    P90 (optimistic):   ${q5.get('p90_usd', 0):,.0f}")
            print(f"    Methodology: {q5.get('methodology', '')}")
        print()

        # Also generate HTML
        ext_section = self.extension.generate_report_section(analysis)
        path = self.report_gen.generate_html(
            projects, assessments, self.connector,
            extension_section=ext_section,
        )
        print(f"  Full report: {path}\n")

    # -- 16. Configure extension ---------------------------------------------
    def _configure_extension(self):
        print("\n  Available extensions:")
        print("    1. RefractoryClientExtension (refractory materials)")
        print("    2. Load from YAML config file")
        print("    3. Clear current extension")

        choice = input("  Select: ").strip()
        if choice == '1':
            self.extension = RefractoryClientExtension()
            print(f"  Loaded: {self.extension.name} (default parameters)\n")
        elif choice == '2':
            # List YAML files in config dir
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            yamls = list(_CONFIG_DIR.glob('*.yaml')) + list(_CONFIG_DIR.glob('*.yml'))
            if yamls:
                print(f"  Config files in {_CONFIG_DIR}:")
                for i, yf in enumerate(yamls, 1):
                    print(f"    {i}. {yf.name}")
                idx_str = input(f"  Select (1-{len(yamls)}): ").strip()
                try:
                    idx = int(idx_str) - 1
                    self.extension = load_extension_from_yaml(str(yamls[idx]))
                    if self.extension:
                        print(f"  Loaded: {self.extension.name}\n")
                except (ValueError, IndexError):
                    print("  Invalid selection.\n")
            else:
                path = input("  Enter YAML config path: ").strip()
                if path:
                    self.extension = load_extension_from_yaml(path)
                    if self.extension:
                        print(f"  Loaded: {self.extension.name}\n")
        elif choice == '3':
            self.extension = None
            print("  Extension cleared.\n")
        else:
            print("  Invalid option.\n")


# ============================================================================
# HELPERS
# ============================================================================

def _ask_int(prompt: str, default: int) -> int:
    val = input(f"{prompt} [{default}]: ").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"  Invalid input, using default: {default}")
        return default


# ---------------------------------------------------------------------------
# ARTICLE AUDIT REPORT (Option 3)
# ---------------------------------------------------------------------------

_WEIGHT_MAP = {'CRITICAL': 10, 'HIGH': 5, 'MEDIUM': 2, 'LOW': 1}

_CATEGORY_COLORS = {
    'CRITICAL': '#e74c3c', 'HIGH': '#e67e22',
    'MEDIUM': '#f1c40f', 'LOW': '#95a5a6',
}


def _score_breakdown(title: str, snippet: str) -> list:
    """Compute per-keyword score breakdown. Returns list of (keyword, level, points)."""
    text_lower = f"{title} {snippet}".lower()
    hits = []
    for level in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
        for kw in _COLLECTOR_KEYWORDS.get(level, []):
            if kw.lower() in text_lower:
                hits.append((kw, level, _WEIGHT_MAP[level]))
    return hits


def _strip_html(text: str) -> str:
    """Strip HTML tags and decode entities (e.g., from RSS snippets already in DB)."""
    if not text or '<' not in text:
        return text
    import re
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, 'html.parser')
    clean = soup.get_text(separator=' ', strip=True)
    return re.sub(r'\s{2,}', ' ', clean).strip()


def _truncate_summary(article: dict) -> str:
    """Build a truncated summary from snippet and/or full_text (no LLM)."""
    full = article.get('full_text') or ''
    snippet = article.get('snippet') or ''

    if full:
        text = _strip_html(full.strip())
        cutoff = min(600, len(text))
        if cutoff < len(text):
            last_period = text.rfind('.', 0, cutoff + 50)
            if last_period > 200:
                cutoff = last_period + 1
        return text[:cutoff].strip()
    elif snippet:
        clean = _strip_html(snippet.strip())
        if not clean or clean == snippet.strip()[:5]:
            # Snippet was just HTML with no useful text — show title instead
            title = article.get('title', '')
            if title:
                return title
        return clean if clean else '(No summary available)'
    else:
        return '(No summary available — article body was not fetched. Run full collection to fetch high-priority articles.)'


# ---------------------------------------------------------------------------
# CLAUDE SUMMARIZER  (caches summaries in the articles table)
# ---------------------------------------------------------------------------

_ANTHROPIC_AVAILABLE = False
try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _anthropic = None  # type: ignore[assignment]


class _ClaudeSummarizer:
    """Summarize articles via Claude Haiku. Caches results in SQLite.

    Requires ANTHROPIC_API_KEY env var. Falls back to truncation if unavailable.
    """
    _client = None
    _db_ready = False

    @classmethod
    def _ensure_column(cls, db_path: str):
        """Add 'ai_summary' column to articles table if missing."""
        if cls._db_ready:
            return
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute('ALTER TABLE articles ADD COLUMN ai_summary TEXT')
            conn.commit()
            conn.close()
        except Exception:
            pass  # column already exists
        cls._db_ready = True

    @classmethod
    def _get_client(cls):
        if cls._client is not None:
            return cls._client
        if not _ANTHROPIC_AVAILABLE:
            return None
        import os
        if not os.environ.get('ANTHROPIC_API_KEY'):
            return None
        try:
            cls._client = _anthropic.Anthropic()
            return cls._client
        except Exception:
            return None

    @classmethod
    def summarize(cls, article: dict, db_path: str) -> str:
        """Return an AI summary for *article*, using cache or Claude API.

        Falls back to truncation if API unavailable or call fails.
        """
        # 1. Check cache
        cls._ensure_column(db_path)
        art_id = article.get('id')
        if art_id:
            import sqlite3
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                'SELECT ai_summary FROM articles WHERE id = ?', (art_id,)
            ).fetchone()
            conn.close()
            if row and row[0]:
                return row[0]

        # 2. Need text to summarize
        full = article.get('full_text') or ''
        snippet = article.get('snippet') or ''
        title = article.get('title') or ''
        text = _strip_html(full) or _strip_html(snippet)

        # 3. Try Claude API
        client = cls._get_client()
        if client is None:
            if not text:
                return '(No summary available — run full collection to fetch article text, or set ANTHROPIC_API_KEY for AI summaries.)'
            return _truncate_summary(article)

        # Determine if we have real article content vs just the title
        has_body = bool(text and text.strip().lower() != title.strip().lower()
                        and len(text.strip()) > len(title.strip()) + 20)

        if has_body:
            # Full article text available — summarize it
            input_text = text[:4000]
            prompt = (
                f"Summarize this blue hydrogen / ammonia industry news article "
                f"in 2-3 sentences. Focus on: who (company), what (project, "
                f"capacity, technology), where (location), and status (FID, EPC, "
                f"construction, etc.). Be factual and concise.\n\n"
                f"Title: {title}\n\n"
                f"Article text:\n{input_text}"
            )
        else:
            # Only title available — ask Claude to explain what the headline means
            prompt = (
                f"Based on this news headline about the blue hydrogen / ammonia "
                f"industry, provide a 1-2 sentence factual summary of what this "
                f"news likely covers. Focus on: who (company), what (event/decision), "
                f"and why it matters for the hydrogen market. Do NOT speculate "
                f"beyond what the headline implies. If the headline is unclear, "
                f"say so briefly.\n\n"
                f"Headline: {title}"
            )

        try:
            resp = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=200 if has_body else 120,
                messages=[{'role': 'user', 'content': prompt}],
            )
            summary = resp.content[0].text.strip()
        except Exception as e:
            print(f"    Claude API error for article {art_id}: {e}")
            if not text:
                return '(Summary unavailable — API error)'
            return _truncate_summary(article)

        # 4. Cache in DB
        if art_id:
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                conn.execute(
                    'UPDATE articles SET ai_summary = ? WHERE id = ?',
                    (summary, art_id))
                conn.commit()
                conn.close()
            except Exception:
                pass  # non-critical

        return summary


def _make_summary(article: dict, db_path: str = None) -> str:
    """Build article summary — uses Claude if available, else truncation."""
    if db_path:
        return _ClaudeSummarizer.summarize(article, db_path)
    return _truncate_summary(article)


def _extract_relevant_excerpt(article: dict, signal) -> str:
    """Extract the paragraph(s) from article text most relevant to the detected signal.

    Shows what the parser actually read.  Priority:
      1. full_text paragraphs that contain signal key-terms (developer,
         project name, capacity, technology, location).
      2. snippet (RSS description) if it has real content (>80 chars).
      3. Honest fallback noting what was actually available.
    """
    import re as _re

    snippet = _strip_html(article.get('snippet') or '')
    full_text = _strip_html(article.get('full_text') or '')
    title = article.get('title') or ''

    # Build search terms from the signal
    terms = []
    if signal.developer:
        terms.append(signal.developer.lower())
    if signal.project_name and signal.project_name != signal.developer:
        terms.append(signal.project_name.lower())
    if signal.capacity_raw:
        cap_nums = _re.findall(r'\d+(?:,\d{3})*(?:\.\d+)?', signal.capacity_raw)
        terms.extend(cap_nums)
    if signal.technology and signal.technology != 'Unknown':
        terms.append(signal.technology.lower())
    if signal.location_state:
        terms.append(signal.location_state.lower())

    # Boilerplate patterns (SEC navigation, site chrome, newsletter TOC headers)
    _BOILERPLATE = [
        'skip to main content', 'directory list', 'search options',
        'about what we do', 'commissioners securities',
        'cookie policy', 'privacy policy', 'terms of use',
        'subscribe to', 'sign up for', 'log in', 'copyright ©',
        'all rights reserved', 'toggle navigation', 'main menu',
        'footer', 'breadcrumb', 'sidebar', 'advertisement',
        'inside this issue', 'inside this issue',
    ]

    # --- 1. Try full_text paragraphs first (the actual article body) ---
    full_text_useful = False
    if full_text and len(full_text) > 50:
        paragraphs = _re.split(r'\n\s*\n|\r\n\s*\r\n', full_text)
        if len(paragraphs) <= 2:
            paragraphs = full_text.split('\n')

        # Filter boilerplate
        clean_paras = []
        for p in paragraphs:
            p = p.strip()
            if len(p) < 30:
                continue
            p_lower = p.lower()
            if any(skip in p_lower for skip in _BOILERPLATE):
                continue
            clean_paras.append(p)

        if clean_paras:
            full_text_useful = True

            if terms:
                # Score paragraphs by how many signal terms they contain
                scored = []
                for p in clean_paras:
                    p_lower = p.lower()
                    score = sum(1 for t in terms if t in p_lower)
                    if score > 0:
                        scored.append((score, len(p), p))
                scored.sort(key=lambda x: (-x[0], -x[1]))

                if scored:
                    parts, total = [], 0
                    for score, _, p in scored:
                        if total + len(p) > 1200 and parts:
                            break
                        parts.append(p)
                        total += len(p)
                    return ' ... '.join(parts)

            # No term matches but full_text has real content — show first paragraphs
            parts, total = [], 0
            for p in clean_paras:
                if total + len(p) > 1200 and parts:
                    break
                parts.append(p)
                total += len(p)
            return ' ... '.join(parts)

    # --- 2. Try snippet if it has substantial content ---
    if snippet and len(snippet) > 80:
        return snippet[:1000]

    # --- 3. Honest fallback — report what the parser actually had ---
    if snippet:
        # Short/generic snippet (e.g., "SEC 8-K filing by X on date")
        return (f'{snippet}  [Note: Full article text was not available — '
                f'signal was extracted from the title and metadata only.]')
    return (f'[Full article text was not fetched for this source. '
            f'Signal was extracted from title only: "{title}"]')


def _generate_article_audit_html(articles: list, days: int, min_score: int,
                                  output_dir: Path,
                                  db_path: str = None) -> str:
    """Generate an HTML audit report for collected articles. Opens in browser."""
    import webbrowser

    now = datetime.now()
    ts = now.strftime('%Y%m%d_%H%M')
    output_dir.mkdir(parents=True, exist_ok=True)
    fpath = output_dir / f"article_audit_{ts}.html"

    # Check if Claude summarization is available
    using_claude = (db_path and _ANTHROPIC_AVAILABLE
                    and _ClaudeSummarizer._get_client() is not None)
    if using_claude:
        print(f"  Summarizing {len(articles)} articles with Claude...")
    else:
        if db_path and _ANTHROPIC_AVAILABLE:
            print("  ANTHROPIC_API_KEY not set — using truncated summaries.")
            print("  Set the key to enable AI summaries: export ANTHROPIC_API_KEY=sk-...")
        elif db_path:
            print("  anthropic library not installed — using truncated summaries.")
            print("  Install: pip3 install anthropic")

    # Build article cards
    cards = []
    for i, a in enumerate(articles, 1):
        title = a.get('title', 'Untitled')
        url = a.get('url', '#')
        source = a.get('source', 'Unknown')
        category = a.get('category', 'LOW')
        score = a.get('priority_score', 0)
        region = a.get('region', '') or 'No region'
        subregion = a.get('subregion', '')
        pub_date = a.get('published_date', '') or 'Unknown date'
        keywords_str = a.get('keywords', '')

        cat_color = _CATEGORY_COLORS.get(category, '#95a5a6')
        if using_claude:
            print(f"    [{i}/{len(articles)}] {title[:60]}...", end='', flush=True)
        summary = _make_summary(a, db_path=db_path)
        if using_claude:
            # Check if it was a cache hit or new API call
            cached = '(cached)' if a.get('id') else ''
            print(f" done {cached}")

        # Score breakdown
        hits = _score_breakdown(title, a.get('snippet', ''))
        if not hits and keywords_str:
            # Fall back to stored keywords if we can't re-derive
            hits = [(kw.strip(), '?', 0) for kw in keywords_str.split(',') if kw.strip()]

        # Group hits by level for display
        breakdown_html = ''
        if hits:
            by_level: Dict[str, list] = {}
            total_explained = 0
            for kw, level, pts in hits:
                by_level.setdefault(level, []).append((kw, pts))
                total_explained += pts

            for level in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
                if level not in by_level:
                    continue
                kws = by_level[level]
                lc = _CATEGORY_COLORS.get(level, '#999')
                kw_list = ', '.join(f'"{kw}" (+{pts})' for kw, pts in kws)
                level_total = sum(pts for _, pts in kws)
                breakdown_html += (
                    f'<div style="margin:2px 0;">'
                    f'<span style="color:{lc};font-weight:600;">{level}</span> '
                    f'({level_total} pts): {kw_list}</div>'
                )
            breakdown_html += f'<div style="margin-top:4px;font-weight:600;">Total: {total_explained} pts</div>'
        else:
            breakdown_html = '<div style="color:#999;">No keyword matches (score may come from other factors)</div>'

        location = f"{subregion}, {region}" if subregion else region

        cards.append(f"""
<div style="background:#fff; border-radius:8px; padding:18px 22px; margin:14px 0;
            box-shadow:0 2px 6px rgba(0,0,0,0.06); border-left:4px solid {cat_color};">
    <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <div style="flex:1;">
            <h3 style="margin:0 0 6px;">
                <span style="color:#999; font-size:0.85em;">#{i}</span>
                <a href="{url}" target="_blank" style="color:#1a5276; text-decoration:none;">{title}</a>
            </h3>
            <div style="font-size:0.85em; color:#636e72; margin-bottom:10px;">
                {source} | {pub_date} | {location}
            </div>
        </div>
        <div style="text-align:right; min-width:120px;">
            <div style="background:{cat_color}; color:#fff; padding:4px 12px; border-radius:12px;
                        font-weight:700; font-size:0.9em; display:inline-block;">{category}</div>
            <div style="font-size:1.3em; font-weight:700; color:#2d3436; margin-top:4px;">Score: {score}</div>
        </div>
    </div>

    <div style="margin:12px 0;">
        <div style="font-weight:600; color:#2c3e50; margin-bottom:4px;">Summary
            {'<span style="background:#8e44ad;color:#fff;padding:2px 8px;border-radius:10px;font-size:0.75em;margin-left:8px;font-weight:400;">AI Summary</span>' if using_claude and not summary.startswith('(No summary') else ''}
        </div>
        <div style="color:#444; line-height:1.5; font-size:0.92em;">{summary}</div>
    </div>

    <div style="margin:12px 0; padding:10px 14px; background:#f8f9fa; border-radius:6px;">
        <div style="font-weight:600; color:#2c3e50; margin-bottom:6px;">Score Reasoning</div>
        <div style="font-size:0.85em;">{breakdown_html}</div>
    </div>

    <div style="font-size:0.8em; margin-top:8px;">
        <a href="{url}" target="_blank" style="color:#3498db;">Open article &rarr;</a>
    </div>
</div>""")

    # Count by category
    cat_counts: Dict[str, int] = {}
    for a in articles:
        c = a.get('category', 'LOW')
        cat_counts[c] = cat_counts.get(c, 0) + 1

    summary_stats = ' | '.join(
        f'<span style="color:{_CATEGORY_COLORS.get(c, "#999")};font-weight:600;">{c}: {n}</span>'
        for c, n in sorted(cat_counts.items(),
                           key=lambda x: -_WEIGHT_MAP.get(x[0], 0))
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Article Audit — {now.strftime('%B %d, %Y')}</title>
<style>
    @media print {{
        body {{ background: #fff; }}
        .no-print {{ display: none; }}
    }}
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin: 0; padding: 0; background: #f4f6f9; color: #2d3436;
    }}
    .container {{ max-width: 900px; margin: 0 auto; padding: 20px; }}
    header {{
        background: linear-gradient(135deg, #0c2340, #1a5276);
        color: #fff; padding: 24px 32px; border-radius: 0 0 8px 8px;
    }}
    header h1 {{ margin: 0 0 4px; font-size: 1.5em; }}
    header p {{ margin: 0; opacity: 0.85; font-size: 0.9em; }}
    .stats {{
        background: #fff; padding: 12px 20px; border-radius: 8px;
        margin: 14px 0; box-shadow: 0 2px 6px rgba(0,0,0,0.06);
        font-size: 0.95em;
    }}
    footer {{
        text-align: center; padding: 16px; font-size: 0.8em; color: #95a5a6;
    }}
    a {{ color: #3498db; }}
</style>
</head>
<body>
<header>
    <h1>Article Audit Report</h1>
    <p>Last {days} days | Min score {min_score} | {len(articles)} articles |
       Generated {now.strftime('%B %d, %Y %H:%M')}</p>
    <p class="no-print" style="margin-top:8px; font-size:0.85em; opacity:0.7;">
        Tip: Use browser Print (Ctrl+P / Cmd+P) &rarr; Save as PDF for a permanent file</p>
</header>
<div class="container">
<div class="stats">
    <strong>Distribution:</strong> {summary_stats} |
    <strong>Total:</strong> {len(articles)}
</div>

<div style="margin:8px 0; padding:10px 16px; background:#fff; border-radius:8px; font-size:0.85em;
            box-shadow:0 2px 6px rgba(0,0,0,0.06);">
    <strong>Scoring weights:</strong>
    <span style="color:{_CATEGORY_COLORS['CRITICAL']};">CRITICAL = 10 pts/keyword</span> |
    <span style="color:{_CATEGORY_COLORS['HIGH']};">HIGH = 5 pts</span> |
    <span style="color:{_CATEGORY_COLORS['MEDIUM']};">MEDIUM = 2 pts</span> |
    <span style="color:{_CATEGORY_COLORS['LOW']};">LOW = 1 pt</span>
    &mdash; Category = highest-level keyword matched
</div>

{''.join(cards)}
</div>
<footer>
    Blue H2/Ammonia Intelligence Monitor &mdash; Article Audit<br>
    Keyword sets: {sum(len(v) for v in _COLLECTOR_KEYWORDS.values())} keywords across 4 priority tiers
</footer>
</body>
</html>"""

    fpath.write_text(html, encoding='utf-8')

    try:
        import webbrowser
        webbrowser.open(f'file://{fpath}')
    except Exception:
        pass

    return str(fpath)


# ---------------------------------------------------------------------------
# PROJECT SIGNALS AUDIT (Option 4)
# ---------------------------------------------------------------------------

_STATUS_COLORS = {
    'Operational': '#27ae60', 'Commissioning': '#27ae60',
    'Construction': '#2ecc71', 'EPC Award': '#2ecc71',
    'FID': '#3498db', 'FEED': '#2980b9',
    'Pre-FEED': '#f39c12', 'Announced': '#e67e22',
    'Cancelled': '#e74c3c', 'Delayed': '#c0392b',
    'Unknown': '#95a5a6',
}


def _confidence_bar(conf: float) -> str:
    """Render a small horizontal confidence bar in HTML."""
    pct = int(conf * 100)
    color = '#27ae60' if conf >= 0.6 else '#f39c12' if conf >= 0.35 else '#e74c3c'
    return (f'<div style="display:inline-flex;align-items:center;gap:6px;">'
            f'<div style="width:80px;height:10px;background:#eee;border-radius:5px;overflow:hidden;">'
            f'<div style="width:{pct}%;height:100%;background:{color};border-radius:5px;"></div>'
            f'</div><span style="font-weight:600;">{pct}%</span></div>')


def _generate_project_audit_html(projects, project_clusters, signals_count,
                                  days, output_dir, db_path=None,
                                  selected_region=None) -> str:
    """Generate an HTML audit report for parsed project signals (option 4).

    Shows per-project cards with:
      - All extracted fields
      - Confidence breakdown (3 layers)
      - Parsing audit trail (from parsing_notes)
      - Deduplication audit (from merge_notes)
      - Source articles with summaries and links
    """
    import webbrowser

    now = datetime.now()
    ts = now.strftime('%Y%m%d_%H%M')
    output_dir.mkdir(parents=True, exist_ok=True)
    fpath = output_dir / f"project_signals_audit_{ts}.html"

    # Pre-load article rows from DB for summary generation
    _article_cache: Dict[int, dict] = {}
    if db_path:
        import sqlite3
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM articles').fetchall()
            conn.close()
            _article_cache = {dict(r)['id']: dict(r) for r in rows}
        except Exception:
            pass

    # Summary stats
    total_cap = sum(p.capacity_mtpa_h2 or 0 for p in projects)
    weighted_cap = sum((p.capacity_mtpa_h2 or 0) * p.fid_probability for p in projects)
    status_counts: Dict[str, int] = {}
    for p in projects:
        s = p.status or 'Unknown'
        status_counts[s] = status_counts.get(s, 0) + 1

    # Group projects by region, sort within each by FID probability
    region_groups = {}
    for p in projects:
        reg = p.region or 'Unknown'
        region_groups.setdefault(reg, []).append(p)
    sorted_regions = sorted(region_groups.keys(),
                            key=lambda r: sum(p.capacity_mtpa_h2 or 0
                                              for p in region_groups[r]),
                            reverse=True)
    for reg in sorted_regions:
        region_groups[reg] = sorted(region_groups[reg],
                                    key=lambda x: -x.fid_probability)

    # Region color palette (same as Option 7)
    _REG_COLORS = {
        'Gulf Coast TX': '#1a5276', 'Gulf Coast LA': '#1a6e5c',
        'Appalachia': '#6c3483', 'West Coast': '#d35400',
        'Midwest': '#2e86c1', 'Pacific Northwest': '#1e8449',
        'Mountain West': '#7d6608', 'Southeast': '#a93226',
        'Northeast': '#2c3e50', 'Plains': '#b9770e',
        'Alaska': '#5d6d7e', 'Hawaii': '#45b39d', 'US Other': '#566573',
        'Unknown': '#95a5a6',
    }

    # Build project cards grouped by region
    cards = []
    global_idx = 0
    for reg in sorted_regions:
        reg_projects = region_groups[reg]
        reg_cap = sum(p.capacity_mtpa_h2 or 0 for p in reg_projects)
        reg_color = _REG_COLORS.get(reg, '#566573')
        cards.append(f"""
<div style="margin:24px 0 8px;padding:12px 18px;background:linear-gradient(135deg, {reg_color}, {reg_color}dd);
            border-radius:8px;color:#fff;">
    <h2 style="margin:0;font-size:1.2em;color:#fff;">{reg}
        <span style="font-size:0.75em;font-weight:400;margin-left:12px;">
            {len(reg_projects)} project{'s' if len(reg_projects) != 1 else ''}
            | {reg_cap:.2f} MTPA total</span>
    </h2>
</div>""")
        for p in reg_projects:
            global_idx += 1
            i = global_idx
            cap_display = f"{p.capacity_mtpa_h2:.4f} MTPA H2" if p.capacity_mtpa_h2 else "Not detected"
            if p.capacity_unit_uncertain and p.capacity_mtpa_h2:
                cap_display += ' <span style="color:#e74c3c;font-weight:700;">~UNCERTAIN</span>'

            jv_html = ''
            if p.co_developers:
                jv_html = f'<span style="color:#8e44ad;"> (JV: {", ".join(d.title() for d in p.co_developers)})</span>'

            status_color = _STATUS_COLORS.get(p.status or 'Unknown', '#95a5a6')

            # Gas demand
            gas_demand = ''
            if p.gas_demand_bcfd is not None:
                rate_label = '0.135' if p.technology == 'ATR' else '0.155'
                gas_demand = (f'<div style="margin-top:6px;font-size:0.88em;color:#555;">'
                              f'Incremental gas demand: <strong>{p.gas_demand_bcfd:.4f} bcf/d</strong> '
                              f'({p.capacity_mtpa_h2:.4f} × {rate_label} bcf/d per MTPA '
                              f'{"ATR" if p.technology == "ATR" else "SMR"})</div>')

            # FID Probability display with LLM assessment details
            fid_pct = f'<strong>{p.fid_probability:.0%}</strong>'
            fid_source = getattr(p, 'fid_source', None)
            fid_evidence = getattr(p, 'fid_evidence', [])
            fid_confidence = getattr(p, 'fid_confidence', '')
            fid_reasoning = getattr(p, 'fid_reasoning', '')
            fid_stage = getattr(p, 'fid_stage', '')
            fid_ev_count = getattr(p, 'fid_evidence_count', 0)

            # Confidence badge color
            _conf_colors = {'HIGH': '#27ae60', 'MEDIUM': '#f39c12', 'LOW': '#e74c3c', 'NONE': '#95a5a6'}
            conf_color = _conf_colors.get(fid_confidence, '#95a5a6')

            if fid_source and fid_source.startswith('fid_engine'):
                if fid_source == 'fid_engine+llm':
                    src_label = 'LLM'
                    src_color = '#2980b9'
                elif fid_source == 'fid_engine_cached':
                    src_label = 'Cached'
                    src_color = '#27ae60'
                elif fid_source == 'no_evidence_found':
                    src_label = 'No Evidence'
                    src_color = '#e74c3c'
                else:
                    src_label = 'Default'
                    src_color = '#95a5a6'
                source_badge = (f'<span style="background:{src_color};color:#fff;padding:1px 6px;'
                                f'border-radius:8px;font-size:0.78em;margin-left:6px;">{src_label}</span>')
                conf_badge = ''
                if fid_confidence:
                    conf_badge = (f' <span style="background:{conf_color};color:#fff;padding:1px 6px;'
                                  f'border-radius:8px;font-size:0.78em;">{fid_confidence}</span>')
                stage_line = ''
                if fid_stage:
                    stage_line = (f'<div style="margin-top:3px;font-size:0.82em;color:#555;">'
                                  f'LLM stage: <strong>{fid_stage}</strong> '
                                  f'({fid_ev_count} evidence docs)</div>')
                ev_lines = ''
                if fid_evidence:
                    ev_items = ''.join(f'<div style="margin:1px 0;">- {e}</div>' for e in fid_evidence[:5])
                    ev_lines = (f'<div style="margin-top:3px;font-size:0.82em;color:#555;">'
                                f'{ev_items}</div>')
                reason_line = ''
                if fid_reasoning:
                    reason_line = (f'<div style="margin-top:3px;font-size:0.80em;color:#777;'
                                   f'font-style:italic;">{fid_reasoning}</div>')
                fid_display = f'{fid_pct} {source_badge}{conf_badge}{stage_line}{ev_lines}{reason_line}'
            elif fid_source == 'no_evidence_found':
                no_ev_badge = ('<span style="background:#e74c3c;color:#fff;padding:1px 6px;'
                               'border-radius:8px;font-size:0.78em;margin-left:6px;">No Evidence</span>')
                fid_display = f'{fid_pct} {no_ev_badge}'
            else:
                default_badge = ('<span style="background:#95a5a6;color:#fff;padding:1px 6px;'
                                 'border-radius:8px;font-size:0.78em;margin-left:6px;">Default</span>')
                fid_display = f'{fid_pct} {default_badge}'

            # Fields grid
            fields = [
                ('Developer', f"{(p.developer or 'N/A').title()}{jv_html}"),
                ('Capacity', cap_display),
                ('Raw capacity text', f"<code>{p.capacity_raw or 'N/A'}</code>"),
                ('Technology', p.technology or 'Unknown'),
                ('Product', p.product or 'Hydrogen'),
                ('Location', f"{p.location_subregion or '—'}, {p.location_state or '—'} → <em>{p.region or 'Unknown'}</em>"),
                ('Status', f'<span style="background:{status_color};color:#fff;padding:2px 8px;'
                           f'border-radius:10px;font-size:0.85em;">{p.status or "Unknown"}</span>'),
                ('FID Probability', fid_display),
                ('EPC Contractor', (p.epc_contractor or 'N/A').title()),
                ('FID Date', p.fid_date or 'Not detected'),
                ('COD Date', p.cod_date or 'Not detected'),
            ]

            fields_html = ''
            for label, val in fields:
                fields_html += (f'<div style="padding:4px 0;border-bottom:1px solid #f0f0f0;">'
                                f'<span style="color:#777;display:inline-block;width:140px;">{label}:</span> '
                                f'{val}</div>')

            # Merge notes (dedup audit)
            merge_html = ''
            if p.merge_notes:
                merge_lines = ''.join(f'<div style="margin:1px 0;">{n}</div>' for n in p.merge_notes)
                merge_html = (f'<div style="margin:12px 0;padding:10px 14px;background:#fef9e7;'
                              f'border-radius:6px;border-left:3px solid #f39c12;">'
                              f'<div style="font-weight:600;color:#7d6608;margin-bottom:6px;">'
                              f'Deduplication Audit</div>'
                              f'<div style="font-size:0.85em;font-family:monospace;color:#555;">'
                              f'{merge_lines}</div></div>')

            # Per-signal parsing notes (from individual articles)
            signal_notes_html = ''
            cluster_sigs = project_clusters.get(p.project_name, [])
            if cluster_sigs:
                sig_items = []
                for si, sig in enumerate(cluster_sigs, 1):
                    title_trunc = sig.article_title[:80] if sig.article_title else '(no title)'
                    notes_lines = ''.join(
                        f'<div style="margin:1px 0;{"font-weight:600;" if n.startswith("---") else ""}'
                        f'{"color:#2c3e50;" if "Layer" in n else ""}">{n}</div>'
                        for n in sig.parsing_notes
                    )
                    sig_items.append(
                        f'<details style="margin:6px 0;">'
                        f'<summary style="cursor:pointer;font-size:0.9em;">'
                        f'<strong>Signal {si}:</strong> {title_trunc} '
                        f'<span style="color:#999;">({sig.article_date or "no date"})</span></summary>'
                        f'<div style="padding:6px 12px;font-size:0.82em;font-family:monospace;'
                        f'background:#fafafa;border-radius:4px;margin-top:4px;">{notes_lines}</div>'
                        f'</details>'
                    )
                signal_notes_html = (
                    f'<div style="margin:12px 0;padding:10px 14px;background:#eaf2f8;'
                    f'border-radius:6px;border-left:3px solid #3498db;">'
                    f'<div style="font-weight:600;color:#1a5276;margin-bottom:6px;">'
                    f'Parsing Audit Trail ({len(cluster_sigs)} signal{"s" if len(cluster_sigs) != 1 else ""})</div>'
                    f'{"".join(sig_items)}</div>'
                )

            # Source articles (with clickable links, summaries, and extracted facts)
            source_html = ''
            if cluster_sigs:
                items = ''
                for si, sig in enumerate(cluster_sigs):
                    url = sig.article_url or '#'
                    title_text = sig.article_title or '(no title)'
                    date_text = sig.article_date or ''

                    # Relevant excerpt — paragraph(s) matching the signal's key terms
                    summary_line = ''
                    art_row = _article_cache.get(sig.article_id)
                    if art_row:
                        excerpt = _extract_relevant_excerpt(art_row, sig)
                        if (excerpt
                                and not excerpt.startswith('(')
                                and excerpt.strip().lower() != title_text.strip().lower()
                                and not excerpt.strip().lower().startswith(title_text.strip().lower()[:40])):
                            import html as _html_mod
                            safe_excerpt = _html_mod.escape(excerpt)
                            summary_line = (
                                f'<div style="font-size:0.85em;color:#444;'
                                f'margin-top:4px;line-height:1.4;'
                                f'background:#fffde7;padding:6px 10px;border-radius:4px;'
                                f'border-left:3px solid #f0c040;">'
                                f'<span style="font-size:0.78em;color:#888;'
                                f'text-transform:uppercase;letter-spacing:0.5px;">'
                                f'Relevant excerpt</span><br>{safe_excerpt}</div>')

                    # Extracted facts line
                    facts = []
                    if sig.developer:
                        facts.append(sig.developer.title())
                    if sig.capacity_raw:
                        facts.append(sig.capacity_raw)
                    if sig.status:
                        facts.append(sig.status)
                    if sig.location_subregion or sig.region:
                        facts.append(sig.location_subregion or sig.region)
                    facts_str = ' | '.join(facts)
                    facts_line = (f'<div style="font-size:0.82em;color:#888;margin-top:2px;">'
                                  f'Extracted: {facts_str}</div>') if facts_str else ''

                    items += (f'<li style="margin:10px 0;padding-bottom:8px;'
                              f'border-bottom:1px solid #eee;">'
                              f'<a href="{url}" target="_blank" style="color:#1a5276;'
                              f'font-weight:600;">{title_text}</a>'
                              f' <span style="color:#999;font-size:0.82em;">{date_text}</span>'
                              f'{summary_line}{facts_line}</li>')
                source_html = (f'<div style="margin:12px 0;padding:10px 14px;background:#f8f9fa;'
                               f'border-radius:6px;">'
                               f'<div style="font-weight:600;color:#2c3e50;margin-bottom:6px;">'
                               f'Source Articles ({len(cluster_sigs)})</div>'
                               f'<ol style="margin:0;padding-left:20px;font-size:0.88em;">{items}</ol></div>')

            cards.append(f"""
<div style="background:#fff; border-radius:8px; padding:18px 22px; margin:16px 0;
            box-shadow:0 2px 6px rgba(0,0,0,0.06); border-left:4px solid {status_color};">
    <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <div style="flex:1;">
            <h3 style="margin:0 0 4px;">
                <span style="color:#999;font-size:0.85em;">#{i}</span>
                {p.project_name}
            </h3>
            <div style="font-size:0.85em;color:#636e72;">
                {(p.developer or 'Unknown').title()} | {p.region or 'Unknown'} | {p.technology or 'Unknown'}
            </div>
        </div>
        <div style="text-align:right;min-width:160px;">
            <div style="background:{status_color};color:#fff;padding:4px 12px;border-radius:12px;
                        font-weight:700;font-size:0.9em;display:inline-block;">{p.status or '?'}</div>
            <div style="margin-top:6px;">FID: {fid_pct}
                <span style="font-size:0.72em;color:{conf_color};">{'●' + fid_confidence if fid_confidence else '●default'}</span>
            </div>
            <div style="margin-top:4px;">Confidence: {_confidence_bar(p.confidence)}</div>
        </div>
    </div>

    <div style="margin:14px 0;">{fields_html}</div>
    {gas_demand}
    {merge_html}
    {signal_notes_html}
    {source_html}
</div>""")

    # Status distribution badge row
    status_badges = ' '.join(
        f'<span style="background:{_STATUS_COLORS.get(s, "#95a5a6")};color:#fff;'
        f'padding:3px 10px;border-radius:12px;font-size:0.85em;margin:2px;">'
        f'{s}: {n}</span>'
        for s, n in sorted(status_counts.items(),
                           key=lambda x: -_STATUS_ORDER.get(x[0], 0))
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Project Signals Audit{f' — {selected_region}' if selected_region else ''} — {now.strftime('%B %d, %Y')}</title>
<style>
    @media print {{ body {{ background: #fff; }} .no-print {{ display: none; }}
        details {{ open; }} details > summary {{ list-style: none; }}
    }}
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin: 0; padding: 0; background: #f4f6f9; color: #2d3436;
    }}
    .container {{ max-width: 960px; margin: 0 auto; padding: 20px; }}
    header {{
        background: linear-gradient(135deg, #0c2340, #1a5276);
        color: #fff; padding: 24px 32px; border-radius: 0 0 8px 8px;
    }}
    header h1 {{ margin: 0 0 4px; font-size: 1.5em; }}
    header p {{ margin: 0; opacity: 0.85; font-size: 0.9em; }}
    .summary-box {{
        background: #fff; padding: 14px 20px; border-radius: 8px;
        margin: 14px 0; box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }}
    .stat-grid {{
        display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 12px; margin: 12px 0;
    }}
    .stat-card {{
        background: #f8f9fa; border-radius: 8px; padding: 12px 16px; text-align: center;
    }}
    .stat-card .value {{ font-size: 1.5em; font-weight: 700; color: #0c2340; }}
    .stat-card .label {{ font-size: 0.8em; color: #636e72; margin-top: 2px; }}
    .legend {{
        background: #fff; padding: 12px 18px; border-radius: 8px;
        margin: 10px 0; font-size: 0.85em; box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }}
    footer {{
        text-align: center; padding: 16px; font-size: 0.8em; color: #95a5a6;
    }}
    code {{ background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }}
</style>
</head>
<body>
<header>
    <h1>Project Signals Audit Report{f' — {selected_region}' if selected_region else ''}</h1>
    <p>{'Region: ' + selected_region + ' | ' if selected_region else ''}Last {days} days | {signals_count} signals → {len(projects)} deduplicated projects |
       Generated {now.strftime('%B %d, %Y %H:%M')}</p>
    <p class="no-print" style="margin-top:8px;font-size:0.85em;opacity:0.7;">
        Tip: Use browser Print (Ctrl+P / Cmd+P) → Save as PDF</p>
</header>
<div class="container">

<div class="stat-grid">
    <div class="stat-card"><div class="value">{len(projects)}</div><div class="label">Projects</div></div>
    <div class="stat-card"><div class="value">{signals_count}</div><div class="label">Signals Parsed</div></div>
    <div class="stat-card"><div class="value">{total_cap:.2f}</div><div class="label">Total Capacity (MTPA)</div></div>
    <div class="stat-card"><div class="value">{weighted_cap:.2f}</div><div class="label">Prob-Weighted (MTPA)</div></div>
</div>

<div class="summary-box">
    <strong>Pipeline by Status:</strong> {status_badges}
</div>

<div class="legend">
    <strong>FID Probability</strong>: LLM-assessed probability from actual regulatory filings
    (SEC EDGAR text, EPA permits, DOE LPO). The LLM reads filing language to determine
    project stage and probability. Badges: <strong>LLM</strong> = assessed from evidence,
    <strong>Cached</strong> = recent assessment reused, <strong>Default</strong> = status-based fallback,
    <strong>No Evidence</strong> = no public filings found.
    Confidence: <strong style="color:#27ae60;">HIGH</strong> = strong multi-source evidence,
    <strong style="color:#f39c12;">MEDIUM</strong> = partial evidence,
    <strong style="color:#e74c3c;">LOW</strong> = limited or uncertain evidence.
    <br><br>
    <strong>Confidence Model</strong> (3 layers):<br>
    Layer 1 — Completeness: developer +0.15, capacity +0.15, tech +0.10, status +0.10,
    location +0.10, EPC +0.10 (base 0.10)<br>
    Layer 2 — Article quality: CRITICAL +0.15, HIGH +0.10, MEDIUM +0.05 | confirmed status +0.05<br>
    Layer 3 — Corroboration: 2+ arts ×1.05, 3+ ×1.10, 5+ ×1.15, 8+ ×1.20, 12+ ×1.25<br>
    Decayed at 5%/month from publication date
    <br><br>
    <strong>Capacity normalization</strong>: NH3→H2 = ×0.178 (IEA 2019) |
    TPD→MTPA = ×365/1e6 | Gas demand: SMR=0.155 bcf/d per MTPA, ATR=0.135 (NETL 2010, IEAGHG 2017)
</div>

{''.join(cards)}

</div>
<footer>
    Blue H2/Ammonia Intelligence Monitor — Project Signals Audit<br>
    Parsing: 7-fix pipeline (name extraction, context-aware status, capacity disambiguation,
    multi-developer, 3-layer confidence, corroboration, unit ambiguity)
</footer>
</body>
</html>"""

    fpath.write_text(html, encoding='utf-8')
    try:
        webbrowser.open(f'file://{fpath}')
    except Exception:
        pass
    return str(fpath)


# ---------------------------------------------------------------------------
# PIPELINE VIEW AUDIT (Option 5)
# ---------------------------------------------------------------------------

def _generate_pipeline_view_html(projects, group_label, grouped, output_dir) -> str:
    """Generate an HTML audit report for the project pipeline view (option 5).

    Grouped by region/status/technology with per-group aggregate stats and
    per-project detail rows.
    """
    import webbrowser

    now = datetime.now()
    ts = now.strftime('%Y%m%d_%H%M')
    output_dir.mkdir(parents=True, exist_ok=True)
    fpath = output_dir / f"pipeline_view_{ts}.html"

    total_cap = sum(p.capacity_mtpa_h2 or 0 for p in projects)
    weighted_cap = sum((p.capacity_mtpa_h2 or 0) * p.fid_probability for p in projects)

    # Build group sections
    group_sections = []
    for group_name, projs in grouped.items():
        projs_sorted = sorted(projs, key=lambda x: -x.fid_probability)
        grp_cap = sum(p.capacity_mtpa_h2 or 0 for p in projs)
        grp_wcap = sum((p.capacity_mtpa_h2 or 0) * p.fid_probability for p in projs)
        grp_gas = sum(p.gas_demand_bcfd or 0 for p in projs)

        rows_html = ''
        for j, p in enumerate(projs_sorted, 1):
            cap = f"{p.capacity_mtpa_h2:.4f}" if p.capacity_mtpa_h2 else "N/A"
            uncert = ' ~' if p.capacity_unit_uncertain else ''
            jv = f' <span style="color:#8e44ad;">(+{", ".join(d.title() for d in p.co_developers)})</span>' if p.co_developers else ''
            sc = _STATUS_COLORS.get(p.status or 'Unknown', '#95a5a6')
            gas_d = f"{p.gas_demand_bcfd:.4f}" if p.gas_demand_bcfd else "—"

            # Merge summary (one-line)
            arts = len(p.source_article_ids) if p.source_article_ids else 0
            merge_note = ''
            if p.merge_notes:
                # Find the corroboration line
                for mn in p.merge_notes:
                    if 'Confidence:' in mn:
                        merge_note = f'<div style="font-size:0.78em;color:#777;margin-top:2px;">{mn.strip()}</div>'
                        break

            rows_html += f"""<tr style="border-bottom:1px solid #f0f0f0;">
    <td style="padding:8px 6px;font-weight:600;">{p.project_name}</td>
    <td>{(p.developer or 'N/A').title()}{jv}</td>
    <td style="text-align:right;"><code>{cap}{uncert}</code></td>
    <td>{p.technology or '?'}</td>
    <td><span style="background:{sc};color:#fff;padding:2px 8px;border-radius:10px;
         font-size:0.82em;">{p.status or '?'}</span></td>
    <td style="text-align:center;font-weight:600;">{p.fid_probability:.0%}</td>
    <td style="text-align:center;">{_confidence_bar(p.confidence)}</td>
    <td style="text-align:right;">{gas_d}</td>
    <td style="text-align:center;">{arts}</td>
    <td>{p.region}</td>
</tr>
<tr><td colspan="10" style="padding:0 6px 6px 20px;">{merge_note}</td></tr>"""

        group_sections.append(f"""
<div style="margin:20px 0;">
    <h3 style="border-bottom:2px solid #0c2340;padding-bottom:6px;">
        {group_name}
        <span style="font-weight:400;font-size:0.8em;color:#636e72;">
            — {len(projs)} projects | {grp_cap:.2f} MTPA total |
            {grp_wcap:.2f} MTPA weighted | {grp_gas:.3f} bcf/d gas demand
        </span>
    </h3>
    <table style="width:100%;border-collapse:collapse;font-size:0.88em;">
    <tr style="background:#f4f6f9;">
        <th style="text-align:left;padding:6px;">Project</th>
        <th style="text-align:left;">Developer</th>
        <th style="text-align:right;">Capacity</th>
        <th>Tech</th>
        <th>Status</th>
        <th>FID Prob</th>
        <th>Confidence</th>
        <th style="text-align:right;">Gas bcf/d</th>
        <th>Arts</th>
        <th>Region</th>
    </tr>
    {rows_html}
    </table>
</div>""")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pipeline View — {now.strftime('%B %d, %Y')}</title>
<style>
    @media print {{ body {{ background: #fff; }} .no-print {{ display: none; }} }}
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin: 0; padding: 0; background: #f4f6f9; color: #2d3436;
    }}
    .container {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
    header {{
        background: linear-gradient(135deg, #0c2340, #1a5276);
        color: #fff; padding: 24px 32px; border-radius: 0 0 8px 8px;
    }}
    header h1 {{ margin: 0 0 4px; font-size: 1.5em; }}
    header p {{ margin: 0; opacity: 0.85; font-size: 0.9em; }}
    .stat-grid {{
        display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 12px; margin: 14px 0;
    }}
    .stat-card {{
        background: #fff; border-radius: 8px; padding: 12px 16px; text-align: center;
        box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }}
    .stat-card .value {{ font-size: 1.5em; font-weight: 700; color: #0c2340; }}
    .stat-card .label {{ font-size: 0.8em; color: #636e72; margin-top: 2px; }}
    table {{ background: #fff; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.06); }}
    th {{ padding: 8px 6px; text-align: left; }}
    code {{ background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }}
    footer {{ text-align: center; padding: 16px; font-size: 0.8em; color: #95a5a6; }}
</style>
</head>
<body>
<header>
    <h1>Project Pipeline — Grouped by {group_label}</h1>
    <p>{len(projects)} projects | Generated {now.strftime('%B %d, %Y %H:%M')}</p>
    <p class="no-print" style="margin-top:8px;font-size:0.85em;opacity:0.7;">
        Tip: Ctrl+P / Cmd+P → Save as PDF</p>
</header>
<div class="container">

<div class="stat-grid">
    <div class="stat-card"><div class="value">{len(projects)}</div><div class="label">Total Projects</div></div>
    <div class="stat-card"><div class="value">{total_cap:.2f}</div><div class="label">Total Capacity (MTPA)</div></div>
    <div class="stat-card"><div class="value">{weighted_cap:.2f}</div><div class="label">Prob-Weighted (MTPA)</div></div>
    <div class="stat-card"><div class="value">{len(grouped)}</div><div class="label">Groups</div></div>
</div>

{''.join(group_sections)}

<div style="background:#fff;padding:12px 18px;border-radius:8px;margin:14px 0;font-size:0.85em;
            box-shadow:0 2px 6px rgba(0,0,0,0.06);">
    <strong>Column notes:</strong>
    Capacity ~ = unit uncertain (H2 vs NH3 ambiguous) |
    Gas bcf/d = SMR×0.155, ATR×0.135 per MTPA (NETL 2010) |
    FID Prob: per-project (FID Engine) or status-based default |
    Confidence = completeness + article quality + corroboration (decayed 5%/mo)
</div>

</div>
<footer>Blue H2/Ammonia Intelligence Monitor — Pipeline View</footer>
</body>
</html>"""

    fpath.write_text(html, encoding='utf-8')
    try:
        webbrowser.open(f'file://{fpath}')
    except Exception:
        pass
    return str(fpath)


# ---------------------------------------------------------------------------
# COMPETITIVE LANDSCAPE AUDIT (Option 6)
# ---------------------------------------------------------------------------

def _generate_competitive_audit_html(projects, output_dir) -> str:
    """Generate an HTML audit report for the competitive landscape (option 6).

    Groups projects by developer, shows capacity share, technology preferences,
    regional presence, and per-project detail rows with audit info.
    """
    import webbrowser

    now = datetime.now()
    ts = now.strftime('%Y%m%d_%H%M')
    output_dir.mkdir(parents=True, exist_ok=True)
    fpath = output_dir / f"competitive_landscape_{ts}.html"

    active = [p for p in projects if p.status != 'Cancelled']
    total_cap = sum(p.capacity_mtpa_h2 or 0 for p in active)
    total_wcap = sum((p.capacity_mtpa_h2 or 0) * p.fid_probability for p in active)

    # Group by developer
    by_dev: Dict[str, list] = {}
    for p in active:
        d = (p.developer or 'Unknown').title()
        by_dev.setdefault(d, []).append(p)

    # Sort by total capacity descending
    dev_sorted = sorted(by_dev.items(), key=lambda x: -sum(p.capacity_mtpa_h2 or 0 for p in x[1]))

    dev_cards = []
    for rank, (dev_name, projs) in enumerate(dev_sorted, 1):
        dev_cap = sum(p.capacity_mtpa_h2 or 0 for p in projs)
        dev_wcap = sum((p.capacity_mtpa_h2 or 0) * p.fid_probability for p in projs)
        dev_gas = sum(p.gas_demand_bcfd or 0 for p in projs)
        share_pct = (dev_cap / total_cap * 100) if total_cap > 0 else 0

        # Technology breakdown
        tech_counts: Dict[str, int] = {}
        for p in projs:
            t = p.technology or 'Unknown'
            tech_counts[t] = tech_counts.get(t, 0) + 1
        tech_str = ' | '.join(f'{t}: {n}' for t, n in sorted(tech_counts.items(), key=lambda x: -x[1]))

        # Region breakdown
        region_counts: Dict[str, int] = {}
        for p in projs:
            r = p.region or 'Unknown'
            region_counts[r] = region_counts.get(r, 0) + 1
        region_str = ' | '.join(f'{r}: {n}' for r, n in sorted(region_counts.items(), key=lambda x: -x[1]))

        # Status breakdown
        sts: Dict[str, int] = {}
        for p in projs:
            s = p.status or 'Unknown'
            sts[s] = sts.get(s, 0) + 1
        status_badges = ' '.join(
            f'<span style="background:{_STATUS_COLORS.get(s, "#95a5a6")};color:#fff;'
            f'padding:2px 8px;border-radius:10px;font-size:0.82em;">{s}: {n}</span>'
            for s, n in sorted(sts.items(), key=lambda x: -_STATUS_ORDER.get(x[0], 0))
        )

        # Share bar
        bar_width = max(2, int(share_pct * 3))
        share_bar = (f'<div style="width:{bar_width}px;height:14px;background:#3498db;'
                     f'border-radius:4px;display:inline-block;vertical-align:middle;"></div>'
                     f' <strong>{share_pct:.1f}%</strong>')

        # Project table rows
        proj_rows = ''
        for p in sorted(projs, key=lambda x: -x.fid_probability):
            cap = f"{p.capacity_mtpa_h2:.4f}" if p.capacity_mtpa_h2 else "N/A"
            uncert = '~' if p.capacity_unit_uncertain else ''
            sc = _STATUS_COLORS.get(p.status or 'Unknown', '#95a5a6')
            arts = len(p.source_article_ids) if p.source_article_ids else 0
            jv = f' (+{", ".join(d.title() for d in p.co_developers)})' if p.co_developers else ''

            # Compact merge audit (one-liner)
            merge_line = ''
            if p.merge_notes:
                for mn in p.merge_notes:
                    if 'Status resolution' in mn or 'Confidence:' in mn:
                        merge_line += f'<div style="font-size:0.78em;color:#888;">{mn.strip()}</div>'

            proj_rows += f"""<tr style="border-bottom:1px solid #f0f0f0;">
    <td style="padding:6px;">{p.project_name}</td>
    <td style="text-align:right;"><code>{cap}{uncert}</code></td>
    <td>{p.technology or '?'}</td>
    <td><span style="background:{sc};color:#fff;padding:2px 6px;border-radius:8px;
         font-size:0.8em;">{p.status or '?'}</span></td>
    <td style="text-align:center;font-weight:600;">{p.fid_probability:.0%}</td>
    <td style="text-align:center;">{_confidence_bar(p.confidence)}</td>
    <td>{p.region}</td>
    <td style="text-align:center;">{arts}</td>
    <td style="font-size:0.82em;">{(p.epc_contractor or '—').title()}{jv}</td>
</tr>
<tr><td colspan="9" style="padding:0 6px 4px 20px;">{merge_line}</td></tr>"""

        dev_cards.append(f"""
<div style="background:#fff;border-radius:8px;padding:18px 22px;margin:16px 0;
            box-shadow:0 2px 6px rgba(0,0,0,0.06);">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div>
            <h3 style="margin:0 0 4px;">
                <span style="color:#999;font-size:0.85em;">#{rank}</span> {dev_name}
            </h3>
            <div style="font-size:0.85em;color:#636e72;">
                {len(projs)} project{"s" if len(projs) != 1 else ""} | {tech_str} | {region_str}
            </div>
        </div>
        <div style="text-align:right;min-width:220px;">
            <div>Capacity: <strong>{dev_cap:.3f} MTPA</strong></div>
            <div>Weighted: <strong>{dev_wcap:.3f} MTPA</strong></div>
            <div>Gas demand: <strong>{dev_gas:.4f} bcf/d</strong></div>
            <div style="margin-top:4px;">Market share: {share_bar}</div>
        </div>
    </div>

    <div style="margin:10px 0;">{status_badges}</div>

    <table style="width:100%;border-collapse:collapse;font-size:0.88em;margin-top:10px;">
    <tr style="background:#f4f6f9;">
        <th style="text-align:left;padding:6px;">Project</th>
        <th style="text-align:right;">Capacity</th>
        <th>Tech</th><th>Status</th>
        <th>FID Prob</th><th>Confidence</th>
        <th>Region</th><th>Arts</th><th>EPC / JV</th>
    </tr>
    {proj_rows}
    </table>
</div>""")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Competitive Landscape — {now.strftime('%B %d, %Y')}</title>
<style>
    @media print {{ body {{ background: #fff; }} .no-print {{ display: none; }} }}
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin: 0; padding: 0; background: #f4f6f9; color: #2d3436;
    }}
    .container {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
    header {{
        background: linear-gradient(135deg, #0c2340, #1a5276);
        color: #fff; padding: 24px 32px; border-radius: 0 0 8px 8px;
    }}
    header h1 {{ margin: 0 0 4px; font-size: 1.5em; }}
    header p {{ margin: 0; opacity: 0.85; font-size: 0.9em; }}
    .stat-grid {{
        display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 12px; margin: 14px 0;
    }}
    .stat-card {{
        background: #fff; border-radius: 8px; padding: 12px 16px; text-align: center;
        box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }}
    .stat-card .value {{ font-size: 1.5em; font-weight: 700; color: #0c2340; }}
    .stat-card .label {{ font-size: 0.8em; color: #636e72; margin-top: 2px; }}
    code {{ background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }}
    footer {{ text-align: center; padding: 16px; font-size: 0.8em; color: #95a5a6; }}
</style>
</head>
<body>
<header>
    <h1>Competitive Landscape Audit</h1>
    <p>{len(active)} active projects across {len(by_dev)} developers |
       Generated {now.strftime('%B %d, %Y %H:%M')}</p>
    <p class="no-print" style="margin-top:8px;font-size:0.85em;opacity:0.7;">
        Tip: Ctrl+P / Cmd+P → Save as PDF</p>
</header>
<div class="container">

<div class="stat-grid">
    <div class="stat-card"><div class="value">{len(by_dev)}</div><div class="label">Developers</div></div>
    <div class="stat-card"><div class="value">{len(active)}</div><div class="label">Active Projects</div></div>
    <div class="stat-card"><div class="value">{total_cap:.2f}</div><div class="label">Total Capacity (MTPA)</div></div>
    <div class="stat-card"><div class="value">{total_wcap:.2f}</div><div class="label">Prob-Weighted (MTPA)</div></div>
</div>

{''.join(dev_cards)}

<div style="background:#fff;padding:12px 18px;border-radius:8px;margin:14px 0;font-size:0.85em;
            box-shadow:0 2px 6px rgba(0,0,0,0.06);">
    <strong>Methodology:</strong>
    Market share = developer capacity / total pipeline capacity.
    Prob-weighted capacity = Σ(capacity × FID probability).
    FID probability: LLM-assessed from SEC EDGAR filings, EPA permits, DOE LPO (Groq/Ollama). Fallback to status-based default.
    Gas demand: SMR=0.155, ATR=0.135 bcf/d per MTPA H2 (NETL 2010, IEAGHG 2017).
    Confidence = 3-layer model (completeness + article quality + corroboration), decayed 5%/month.
</div>

</div>
<footer>Blue H2/Ammonia Intelligence Monitor — Competitive Landscape</footer>
</body>
</html>"""

    fpath.write_text(html, encoding='utf-8')
    try:
        webbrowser.open(f'file://{fpath}')
    except Exception:
        pass
    return str(fpath)


# ---------------------------------------------------------------------------
# NEWS IMPACT ASSESSMENT REPORT (Option 7)
# ---------------------------------------------------------------------------

def _generate_assessment_html(assessments, days, output_dir, db_path=None,
                              signal_analyses=None, batch_summary='',
                              llm_backend='',
                              evidence_map=None,
                              fid_overrides=None,
                              enrichment_log=None,
                              signal_index_map=None,
                              selected_region=None) -> str:
    """Generate an HTML report for DecarbIQ market impact assessments (option 7).

    Shows per-signal cards with gas demand, price impacts, policy context,
    LLM analysis, regulatory evidence, and methodology audit trail.
    """
    signal_analyses = signal_analyses or {}
    evidence_map = evidence_map or {}
    fid_overrides = fid_overrides or {}
    enrichment_log = enrichment_log or {}
    signal_index_map = signal_index_map or {}
    import webbrowser

    now = datetime.now()
    ts = now.strftime('%Y%m%d_%H%M')
    output_dir.mkdir(parents=True, exist_ok=True)
    fpath = output_dir / f"news_impact_assessment_{ts}.html"

    # Preload article rows from DB for excerpt extraction
    _article_cache_7: Dict[int, dict] = {}
    if db_path:
        import sqlite3 as _sq3
        try:
            conn = _sq3.connect(db_path)
            conn.row_factory = _sq3.Row
            rows = conn.execute('SELECT * FROM articles').fetchall()
            conn.close()
            _article_cache_7 = {dict(r)['id']: dict(r) for r in rows}
        except Exception:
            pass

    # Group assessments by region, sort within each region by confidence then impact
    region_groups = {}
    for a in assessments:
        reg = a.signal.region or 'Unknown'
        region_groups.setdefault(reg, []).append(a)
    # Sort regions by total gas demand impact (descending)
    sorted_regions = sorted(region_groups.keys(),
                            key=lambda r: sum(
                                a.incremental_gas_demand_bcfd or 0
                                for a in region_groups[r]),
                            reverse=True)
    # Sort signals within each region: HIGH confidence first, then by gas demand
    for reg in sorted_regions:
        region_groups[reg] = sorted(
            region_groups[reg],
            key=lambda a: (
                {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(a.confidence, 0),
                a.incremental_gas_demand_bcfd or 0
            ),
            reverse=True)

    # Summary stats
    conf_counts = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
    total_gas_demand = 0.0
    total_gas_price = 0.0
    total_ercot = 0.0
    with_impact = 0
    for a in assessments:
        conf_counts[a.confidence] = conf_counts.get(a.confidence, 0) + 1
        if a.incremental_gas_demand_bcfd:
            total_gas_demand += a.incremental_gas_demand_bcfd
            total_gas_price += a.gas_price_impact_per_mmbtu or 0
            total_ercot += a.ercot_impact_per_mwh or 0
            with_impact += 1

    conf_colors = {'HIGH': '#27ae60', 'MEDIUM': '#f39c12', 'LOW': '#e74c3c'}

    # Region color palette for headers
    _REGION_COLORS = {
        'Gulf Coast TX': '#1a5276', 'Gulf Coast LA': '#1a6e5c',
        'Appalachia': '#6c3483', 'West Coast': '#d35400',
        'Midwest': '#2e86c1', 'Pacific Northwest': '#1e8449',
        'Mountain West': '#7d6608', 'Southeast': '#a93226',
        'Northeast': '#2c3e50', 'Plains': '#b9770e',
        'International': '#1b4f72', 'Alaska': '#5d6d7e',
        'Hawaii': '#45b39d', 'US Other': '#566573',
        'Unknown': '#95a5a6',
    }

    # Build assessment cards grouped by region
    cards = []
    global_idx = 0
    for reg in sorted_regions:
        reg_assessments = region_groups[reg]
        reg_gas = sum(a.incremental_gas_demand_bcfd or 0 for a in reg_assessments)
        reg_color = _REGION_COLORS.get(reg, '#566573')
        cards.append(f"""
<div style="margin:24px 0 8px;padding:12px 18px;background:linear-gradient(135deg, {reg_color}, {reg_color}dd);
            border-radius:8px;color:#fff;">
    <h2 style="margin:0;font-size:1.2em;color:#fff;">{reg}
        <span style="font-size:0.75em;font-weight:400;margin-left:12px;">
            {len(reg_assessments)} signal{'s' if len(reg_assessments) != 1 else ''}
            | +{reg_gas:.4f} bcf/d total</span>
    </h2>
</div>""")
        for a in reg_assessments:
            global_idx += 1
            i = global_idx
            s = a.signal
            conf_color = conf_colors.get(a.confidence, '#95a5a6')
            status_color = _STATUS_COLORS.get(s.status or 'Unknown', '#95a5a6')

            # Impact metrics
            impact_html = ''
            if a.incremental_gas_demand_bcfd is not None:
                impact_html = f"""
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
                        gap:8px;margin:10px 0;">
                <div style="background:#eaf2f8;padding:8px 12px;border-radius:6px;text-align:center;">
                    <div style="font-size:1.2em;font-weight:700;color:#1a5276;">
                        +{a.incremental_gas_demand_bcfd:.4f}</div>
                    <div style="font-size:0.78em;color:#636e72;">bcf/d gas demand</div>
                </div>
                <div style="background:#fef9e7;padding:8px 12px;border-radius:6px;text-align:center;">
                    <div style="font-size:1.2em;font-weight:700;color:#7d6608;">
                        +${a.gas_price_impact_per_mmbtu:.5f}</div>
                    <div style="font-size:0.78em;color:#636e72;">HH $/MMBtu</div>
                </div>
                <div style="background:#f5eef8;padding:8px 12px;border-radius:6px;text-align:center;">
                    <div style="font-size:1.2em;font-weight:700;color:#6c3483;">
                        +${a.ercot_impact_per_mwh:.4f}</div>
                    <div style="font-size:0.78em;color:#636e72;">ERCOT $/MWh</div>
                </div>
            </div>"""

            # Look up per-signal evidence data
            sig_idx = signal_index_map.get(id(s))
            card_evidence = evidence_map.get(sig_idx, []) if sig_idx is not None else []
            card_fid = fid_overrides.get(sig_idx, {}) if sig_idx is not None else {}
            card_enrichments = enrichment_log.get(sig_idx, []) if sig_idx is not None else []

            # FID source badge
            fid_src_label = (card_fid.get('source', '')
                             or a.model_evidence.get('fid_source', 'static_status_lookup'))
            fid_color = '#27ae60' if 'fid_engine' in fid_src_label else '#95a5a6'
            fid_badge = (f'<span style="background:{fid_color};color:#fff;padding:2px 8px;'
                         f'border-radius:10px;font-size:0.85em;">{fid_src_label}</span>')

            # Signal fields
            fields = [
                ('Project', s.project_name or 'N/A'),
                ('Developer', (s.developer or 'N/A').title()),
                ('Capacity', f"{s.capacity_mtpa_h2:.4f} MTPA H2" if s.capacity_mtpa_h2 else 'Not detected'),
                ('Technology', s.technology or 'Unknown'),
                ('Status', f'<span style="background:{status_color};color:#fff;padding:2px 8px;'
                           f'border-radius:10px;font-size:0.85em;">{s.status or "Unknown"}</span>'),
                ('FID Source', fid_badge),
                ('Region', f"{s.location_subregion or '—'}, {s.location_state or '—'} → <em>{s.region or 'Unknown'}</em>"),
                ('Year of Impact', str(a.year_of_impact)),
            ]
            if card_evidence:
                fields.append(('Evidence', f'{len(card_evidence)} regulatory documents'))
            if s.epc_contractor:
                fields.append(('EPC Contractor', s.epc_contractor.title()))
            if s.offtake_buyer:
                fields.append(('Offtake Buyer', s.offtake_buyer.title()))
            if s.deal_value_usd:
                fields.append(('Deal Value', f"${s.deal_value_usd:,.0f}"))

            fields_html = ''
            for label, val in fields:
                fields_html += (f'<div style="padding:3px 0;border-bottom:1px solid #f0f0f0;">'
                                f'<span style="color:#777;display:inline-block;width:130px;">'
                                f'{label}:</span> {val}</div>')

            # Policy context
            policy_html = ''
            if a.policy_scenario_match or a.policy_variables_affected:
                policy_parts = []
                if a.policy_scenario_match:
                    policy_parts.append(f'<strong>Scenario:</strong> {a.policy_scenario_match}')
                if a.policy_variables_affected:
                    policy_parts.append(f'<strong>Variables:</strong> {", ".join(a.policy_variables_affected)}')
                policy_html = (f'<div style="margin:10px 0;padding:8px 14px;background:#e8f8f5;'
                               f'border-radius:6px;border-left:3px solid #1abc9c;font-size:0.88em;">'
                               f'{"<br>".join(policy_parts)}</div>')

            # Methodology notes
            notes_html = ''
            if a.methodology_notes:
                notes_items = ''.join(f'<li>{n}</li>' for n in a.methodology_notes)
                notes_html = (f'<details style="margin:8px 0;">'
                              f'<summary style="cursor:pointer;font-size:0.88em;color:#555;">'
                              f'Methodology Notes ({len(a.methodology_notes)})</summary>'
                              f'<ul style="font-size:0.82em;color:#666;margin:6px 0;'
                              f'padding-left:20px;">{notes_items}</ul></details>')

            # Literature sources
            lit_html = ''
            if a.literature_sources:
                lit_items = ''.join(f'<li>{src}</li>' for src in a.literature_sources)
                lit_html = (f'<details style="margin:4px 0;">'
                            f'<summary style="cursor:pointer;font-size:0.85em;color:#888;">'
                            f'Literature Sources ({len(a.literature_sources)})</summary>'
                            f'<ul style="font-size:0.8em;color:#999;margin:6px 0;'
                            f'padding-left:20px;">{lit_items}</ul></details>')

            # LLM analysis for this signal
            llm_html = ''
            llm_text = signal_analyses.get(id(a), '')
            if llm_text:
                # Convert newlines to <br> for HTML display
                llm_formatted = llm_text.replace('\n', '<br>')
                llm_html = (f'<div style="margin:10px 0;padding:10px 14px;background:#fdf2e9;'
                            f'border-radius:6px;border-left:3px solid #e67e22;">'
                            f'<div style="font-weight:600;color:#935116;margin-bottom:6px;">'
                            f'LLM Market Analysis</div>'
                            f'<div style="font-size:0.88em;color:#444;line-height:1.5;">'
                            f'{llm_formatted}</div></div>')

            # Enrichment notes badge
            enrichment_html = ''
            if card_enrichments:
                enrich_text = ' | '.join(card_enrichments)
                enrichment_html = (
                    f'<div style="margin:4px 0;padding:4px 10px;font-size:0.82em;'
                    f'color:#2980b9;background:#eaf6fd;border-radius:4px;">'
                    f'&#8505; {enrich_text}</div>')

            # Regulatory evidence collapsible section
            reg_evidence_html = ''
            if card_evidence:
                ev_items = []
                for e in card_evidence:
                    e_source = e.get('source', 'unknown')
                    e_type = e.get('document_type', '?')
                    e_date = e.get('document_date', '?')
                    e_stage = e.get('stage', '')
                    e_project = e.get('project_name', '')
                    e_excerpt = (e.get('raw_text_excerpt', '') or '')
                    e_url = e.get('document_url', '')
                    stage_badge = (f' <span style="background:#e8daef;padding:1px 6px;'
                                   f'border-radius:8px;font-size:0.9em;">{e_stage}</span>'
                                   if e_stage else '')
                    url_link = (f'<br><a href="{e_url}" target="_blank" '
                                f'style="color:#3498db;font-size:0.9em;">View source</a>'
                                if e_url else '')
                    ev_items.append(
                        f'<div style="padding:6px;background:#f0f7ff;border-radius:4px;'
                        f'margin:4px 0;border-left:2px solid #3498db;">'
                        f'<strong>[{e_source} / {e_type}]</strong> {e_date}{stage_badge}<br>'
                        f'{f"<em>{e_project}</em><br>" if e_project else ""}'
                        f'<span style="color:#666;">{e_excerpt}</span>'
                        f'{url_link}</div>')
                reg_evidence_html = (
                    f'<details style="margin:8px 0;">'
                    f'<summary style="cursor:pointer;font-size:0.88em;color:#555;">'
                    f'Regulatory Evidence ({len(card_evidence)} documents)</summary>'
                    f'<div style="font-size:0.82em;margin:6px 0;">'
                    f'{"".join(ev_items)}</div></details>')

            # Article link + relevant excerpt
            url = s.article_url or '#'
            title_text = s.article_title or '(no title)'
            date_text = s.article_date or ''

            # Build Source Article section with link and relevant excerpt
            source_article_html = ''
            art_row_7 = _article_cache_7.get(s.article_id)
            if art_row_7:
                excerpt = _extract_relevant_excerpt(art_row_7, s)
                excerpt_block = ''
                if (excerpt
                        and not excerpt.startswith('(')
                        and excerpt.strip().lower() != title_text.strip().lower()
                        and not excerpt.strip().lower().startswith(
                            title_text.strip().lower()[:40])):
                    import html as _html_mod
                    safe_excerpt = _html_mod.escape(excerpt)
                    excerpt_block = (
                        f'<div style="font-size:0.85em;color:#444;'
                        f'margin-top:6px;line-height:1.4;'
                        f'background:#fffde7;padding:6px 10px;border-radius:4px;'
                        f'border-left:3px solid #f0c040;">'
                        f'<span style="font-size:0.78em;color:#888;'
                        f'text-transform:uppercase;letter-spacing:0.5px;">'
                        f'Relevant excerpt</span><br>{safe_excerpt}</div>')
                source_article_html = (
                    f'<div style="margin:12px 0;padding:10px 14px;background:#f8f9fa;'
                    f'border-radius:6px;">'
                    f'<div style="font-weight:600;color:#2c3e50;margin-bottom:6px;">'
                    f'Source Article</div>'
                    f'<a href="{url}" target="_blank" style="color:#1a5276;'
                    f'font-weight:600;font-size:0.9em;">{title_text}</a>'
                    f' <span style="color:#999;font-size:0.82em;">{date_text}</span>'
                    f'{excerpt_block}</div>')
            elif url != '#':
                # No article in DB but we have a URL
                source_article_html = (
                    f'<div style="margin:12px 0;padding:10px 14px;background:#f8f9fa;'
                    f'border-radius:6px;">'
                    f'<div style="font-weight:600;color:#2c3e50;margin-bottom:6px;">'
                    f'Source Article</div>'
                    f'<a href="{url}" target="_blank" style="color:#1a5276;'
                    f'font-weight:600;font-size:0.9em;">{title_text}</a>'
                    f' <span style="color:#999;font-size:0.82em;">{date_text}</span>'
                    f'</div>')

            cards.append(f"""
<div style="background:#fff;border-radius:8px;padding:18px 22px;margin:16px 0;
            box-shadow:0 2px 6px rgba(0,0,0,0.06);border-left:4px solid {conf_color};">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div style="flex:1;">
            <h3 style="margin:0 0 4px;">
                <span style="color:#999;font-size:0.85em;">#{i}</span>
                <a href="{url}" target="_blank" style="color:#1a5276;text-decoration:none;">
                    {title_text}</a>
            </h3>
            <div style="font-size:0.85em;color:#636e72;">
                {(s.developer or 'Unknown').title()} | {s.region or 'Unknown'} |
                {s.technology or 'Unknown'}
                <span style="color:#999;margin-left:8px;">{date_text}</span>
            </div>
        </div>
        <div style="text-align:right;min-width:140px;">
            <div style="background:{conf_color};color:#fff;padding:4px 12px;border-radius:12px;
                        font-weight:700;font-size:0.9em;display:inline-block;">
                {a.confidence}</div>
            <div style="margin-top:6px;">Confidence: {_confidence_bar(s.confidence)}</div>
        </div>
    </div>

    {impact_html}
    {enrichment_html}
    <div style="margin:10px 0;font-size:0.9em;">{fields_html}</div>
    {policy_html}
    {llm_html}
    {reg_evidence_html}
    {source_article_html}
    {notes_html}
    {lit_html}
</div>""")

    # Confidence distribution badges
    conf_badges = ' '.join(
        f'<span style="background:{conf_colors.get(c, "#95a5a6")};color:#fff;'
        f'padding:3px 10px;border-radius:12px;font-size:0.85em;margin:2px;">'
        f'{c}: {n}</span>'
        for c, n in [('HIGH', conf_counts['HIGH']),
                     ('MEDIUM', conf_counts['MEDIUM']),
                     ('LOW', conf_counts['LOW'])]
    )

    # Build LLM sections as strings before the main f-string
    llm_model_label = _GROQ_MODEL if llm_backend == 'groq' else _OLLAMA_MODEL
    llm_method_line = ''
    if llm_backend:
        llm_method_line = (f'<br><strong>LLM Assessment:</strong> '
                           f'{llm_backend.upper()} ({llm_model_label})')

    batch_summary_html = ''
    if batch_summary:
        summary_formatted = batch_summary.replace('\n', '<br>')
        batch_summary_html = (
            f'<div style="background:#fff;border-radius:8px;padding:18px 22px;margin:16px 0;'
            f'box-shadow:0 2px 6px rgba(0,0,0,0.06);border-left:4px solid #2c3e50;">'
            f'<h2 style="margin:0 0 12px;color:#2c3e50;font-size:1.2em;">'
            f'Market Outlook Summary</h2>'
            f'<div style="font-size:0.92em;color:#333;line-height:1.6;">'
            f'{summary_formatted}</div>'
            f'<div style="margin-top:8px;font-size:0.78em;color:#999;">'
            f'Generated by {llm_backend.upper()} ({llm_model_label})</div></div>'
        )

    # Evidence coverage summary box
    evidence_coverage_html = ''
    if evidence_map:
        n_with_ev = sum(1 for ev in evidence_map.values() if ev)
        n_fid_override = len(fid_overrides)
        total_ev_docs = sum(len(ev) for ev in evidence_map.values())
        ev_sources = set()
        for ev_list in evidence_map.values():
            for e in ev_list:
                ev_sources.add(e.get('source', 'unknown'))
        n_enriched = len(enrichment_log)
        evidence_coverage_html = (
            f'<div class="summary-box" style="font-size:0.85em;border-left:3px solid #3498db;">'
            f'<strong>Regulatory Evidence Coverage:</strong> '
            f'{n_with_ev} of {len(assessments)} signals matched to evidence '
            f'({total_ev_docs} documents) '
            f'| {n_fid_override} signals with FID engine overrides'
            f'{f" | {n_enriched} signals enriched (tech/capacity)" if n_enriched else ""}'
            f'<br><span style="color:#777;">Sources: {", ".join(sorted(ev_sources))}</span>'
            f'</div>')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>News Impact Assessment{f' — {selected_region}' if selected_region else ''} — {now.strftime('%B %d, %Y')}</title>
<style>
    @media print {{ body {{ background: #fff; }} .no-print {{ display: none; }}
        details {{ open; }} details > summary {{ list-style: none; }}
    }}
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin: 0; padding: 0; background: #f4f6f9; color: #2d3436;
    }}
    .container {{ max-width: 960px; margin: 0 auto; padding: 20px; }}
    header {{
        background: linear-gradient(135deg, #0c2340, #1a5276);
        color: #fff; padding: 24px 32px; border-radius: 0 0 8px 8px;
    }}
    header h1 {{ margin: 0 0 4px; font-size: 1.5em; }}
    header p {{ margin: 0; opacity: 0.85; font-size: 0.9em; }}
    .stat-grid {{
        display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 12px; margin: 14px 0;
    }}
    .stat-card {{
        background: #fff; border-radius: 8px; padding: 12px 16px; text-align: center;
        box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }}
    .stat-card .value {{ font-size: 1.5em; font-weight: 700; color: #0c2340; }}
    .stat-card .label {{ font-size: 0.8em; color: #636e72; margin-top: 2px; }}
    .summary-box {{
        background: #fff; padding: 14px 20px; border-radius: 8px;
        margin: 14px 0; box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }}
    footer {{
        text-align: center; padding: 16px; font-size: 0.8em; color: #95a5a6;
    }}
</style>
</head>
<body>
<header>
    <h1>News Impact Assessment Report{f' — {selected_region}' if selected_region else ''}</h1>
    <p>{'Region: ' + selected_region + ' | ' if selected_region else ''}Last {days} days | {len(assessments)} signals assessed through DecarbIQ model |
       Generated {now.strftime('%B %d, %Y %H:%M')}</p>
    <p class="no-print" style="margin-top:8px;font-size:0.85em;opacity:0.7;">
        Tip: Use browser Print (Ctrl+P / Cmd+P) → Save as PDF</p>
</header>
<div class="container">

<div class="stat-grid">
    <div class="stat-card"><div class="value">{len(assessments)}</div>
        <div class="label">Signals Assessed</div></div>
    <div class="stat-card"><div class="value">{with_impact}</div>
        <div class="label">With Gas Impact</div></div>
    <div class="stat-card"><div class="value">+{total_gas_demand:.4f}</div>
        <div class="label">Total bcf/d</div></div>
    <div class="stat-card"><div class="value">+${total_gas_price:.5f}</div>
        <div class="label">HH Impact ($/MMBtu)</div></div>
    <div class="stat-card"><div class="value">+${total_ercot:.4f}</div>
        <div class="label">ERCOT Impact ($/MWh)</div></div>
</div>

<div class="summary-box">
    <strong>Assessment Confidence:</strong> {conf_badges}
</div>

<div class="summary-box" style="font-size:0.85em;">
    <strong>Methodology:</strong>
    Gas demand = capacity (MTPA) × conversion factor (SMR=0.155, ATR=0.135 bcf/d per MTPA).
    Price impact from DecarbIQ trained model coefficient.
    Prob-weighted by FID probability (FID Engine or status-based default).
    Confidence = signal extraction quality × model evidence strength.
    {llm_method_line}
</div>

{evidence_coverage_html}

{batch_summary_html}

{''.join(cards)}

</div>
<footer>
    Blue H2/Ammonia Intelligence Monitor — DecarbIQ Market Impact Assessment
</footer>
</body>
</html>"""

    fpath.write_text(html, encoding='utf-8')
    try:
        webbrowser.open(f'file://{fpath}')
    except Exception:
        pass
    return str(fpath)


# ---------------------------------------------------------------------------
# SENSITIVITY ANALYSIS REPORT (Option 10)
# ---------------------------------------------------------------------------

def _generate_sensitivity_html(var_results, scen_results, suggestion,
                                interpretation, assessments, days,
                                output_dir, llm_backend='') -> str:
    """Generate HTML report for LLM-driven sensitivity analysis (option 10)."""
    import webbrowser

    now = datetime.now()
    ts = now.strftime('%Y%m%d_%H%M')
    output_dir.mkdir(parents=True, exist_ok=True)
    fpath = output_dir / f"sensitivity_analysis_{ts}.html"

    llm_model_label = _GROQ_MODEL if llm_backend == 'groq' else _OLLAMA_MODEL

    # Variable cards
    var_cards = []
    for vr in var_results:
        info = vr['info']
        result = vr['result']
        dyn = result.get('dynamic', {})
        lin = result.get('linear', {})
        ci = dyn.get('confidence_intervals', {})

        direction_label = '+1σ' if 'plus' in vr['direction'] else '-1σ'
        delta_input = result.get('delta_input', info.get('sigma_1', '?'))

        # Impact color
        gas_val = dyn.get('delta_gas', 0) or 0
        gas_color = '#e74c3c' if gas_val > 0 else '#27ae60' if gas_val < 0 else '#95a5a6'

        # CI display
        ci_html = ''
        gas_ci = ci.get('gas_price', {})
        if gas_ci:
            ci_html = (f'<div style="font-size:0.82em;color:#888;margin-top:4px;">'
                       f'90% CI: [{gas_ci.get("ci_90", ["?","?"])[0]:.2f}, '
                       f'{gas_ci.get("ci_90", ["?","?"])[1]:.2f}] $/MMBtu</div>')

        var_cards.append(f"""
<div style="background:#fff;border-radius:8px;padding:16px 20px;margin:12px 0;
            box-shadow:0 2px 6px rgba(0,0,0,0.06);border-left:4px solid {gas_color};">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div style="flex:1;">
            <h3 style="margin:0 0 4px;font-size:1.05em;">
                {vr['name']}
                <span style="background:#dfe6e9;padding:2px 8px;border-radius:8px;
                             font-size:0.8em;margin-left:8px;">{direction_label}</span>
            </h3>
            <div style="font-size:0.85em;color:#636e72;">
                Category: {info.get('category', '?')} | Unit: {info.get('unit', '?')} |
                Δ input: {delta_input}
            </div>
            <div style="font-size:0.88em;color:#555;margin-top:6px;font-style:italic;">
                {vr['reason']}</div>
        </div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
                gap:8px;margin:12px 0;">
        <div style="background:#eaf2f8;padding:8px 12px;border-radius:6px;text-align:center;">
            <div style="font-size:1.1em;font-weight:700;color:{gas_color};">
                {dyn.get('delta_gas', 'N/A')}</div>
            <div style="font-size:0.78em;color:#636e72;">Δ Gas ($/MMBtu)</div>
            {ci_html}
        </div>
        <div style="background:#fef9e7;padding:8px 12px;border-radius:6px;text-align:center;">
            <div style="font-size:1.1em;font-weight:700;color:#7d6608;">
                {dyn.get('delta_ercot', 'N/A')}</div>
            <div style="font-size:0.78em;color:#636e72;">Δ ERCOT ($/MWh)</div>
        </div>
        <div style="background:#f5eef8;padding:8px 12px;border-radius:6px;text-align:center;">
            <div style="font-size:1.1em;font-weight:700;color:#6c3483;">
                {dyn.get('delta_caiso', 'N/A')}</div>
            <div style="font-size:0.78em;color:#636e72;">Δ CAISO ($/MWh)</div>
        </div>
    </div>

    <div style="font-size:0.82em;color:#999;">
        Regime: {dyn.get('regime_before', '?')} → {dyn.get('regime_after', '?')} |
        Lag: {dyn.get('lag_fraction', '?')} |
        Seasonal mult: {dyn.get('seasonal_multiplier', '?')}
    </div>
</div>""")

    # Scenario cards
    scen_cards = []
    for sr in scen_results:
        data = sr['data']
        dyn = data.get('dynamic', {})
        details = data.get('details', [])
        flags = data.get('data_quality_flags', [])

        detail_rows = ''
        for d in details:
            detail_rows += (f'<tr><td style="padding:3px 8px;">{d["variable"]}</td>'
                            f'<td style="text-align:right;">{d.get("delta", "?")}</td>'
                            f'<td style="text-align:right;">{d.get("dynamic_gas", "?")}</td>'
                            f'<td style="text-align:right;">{d.get("dynamic_ercot", "?")}</td>'
                            f'<td style="text-align:right;">{d.get("dynamic_caiso", "?")}</td></tr>')

        flags_html = ''
        if flags:
            flags_html = ('<div style="margin-top:8px;font-size:0.78em;color:#e67e22;">'
                          + '<br>'.join(flags) + '</div>')

        scen_cards.append(f"""
<div style="background:#fff;border-radius:8px;padding:16px 20px;margin:12px 0;
            box-shadow:0 2px 6px rgba(0,0,0,0.06);border-left:4px solid #2c3e50;">
    <h3 style="margin:0 0 4px;font-size:1.05em;">{sr['name']}</h3>
    <div style="font-size:0.85em;color:#636e72;">{sr['description']}
        | Horizon: {data.get('horizon', '?')}</div>
    <div style="font-size:0.88em;color:#555;margin-top:6px;font-style:italic;">
        {sr['reason']}</div>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
                gap:8px;margin:12px 0;">
        <div style="background:#eaf2f8;padding:8px 12px;border-radius:6px;text-align:center;">
            <div style="font-size:1.1em;font-weight:700;">{dyn.get('delta_gas', 'N/A')}</div>
            <div style="font-size:0.78em;color:#636e72;">Δ Gas ($/MMBtu)</div>
        </div>
        <div style="background:#fef9e7;padding:8px 12px;border-radius:6px;text-align:center;">
            <div style="font-size:1.1em;font-weight:700;">{dyn.get('delta_ercot', 'N/A')}</div>
            <div style="font-size:0.78em;color:#636e72;">Δ ERCOT ($/MWh)</div>
        </div>
        <div style="background:#f5eef8;padding:8px 12px;border-radius:6px;text-align:center;">
            <div style="font-size:1.1em;font-weight:700;">{dyn.get('delta_caiso', 'N/A')}</div>
            <div style="font-size:0.78em;color:#636e72;">Δ CAISO ($/MWh)</div>
        </div>
    </div>

    <details style="margin:6px 0;">
        <summary style="cursor:pointer;font-size:0.88em;color:#555;">
            Variable Breakdown ({len(details)} components)</summary>
        <table style="width:100%;border-collapse:collapse;font-size:0.82em;margin-top:6px;">
        <tr style="background:#f4f6f9;"><th style="text-align:left;padding:3px 8px;">Variable</th>
            <th style="text-align:right;">Δ Input</th><th style="text-align:right;">Δ Gas</th>
            <th style="text-align:right;">Δ ERCOT</th><th style="text-align:right;">Δ CAISO</th></tr>
        {detail_rows}
        </table>
    </details>
    {flags_html}
</div>""")

    # Interpretation section
    interp_html = ''
    if interpretation:
        interp_formatted = interpretation.replace('\n', '<br>')
        interp_html = (
            f'<div style="background:#fff;border-radius:8px;padding:18px 22px;margin:16px 0;'
            f'box-shadow:0 2px 6px rgba(0,0,0,0.06);border-left:4px solid #e67e22;">'
            f'<h2 style="margin:0 0 12px;color:#935116;font-size:1.15em;">'
            f'LLM Interpretation</h2>'
            f'<div style="font-size:0.92em;color:#333;line-height:1.6;">'
            f'{interp_formatted}</div>'
            f'<div style="margin-top:8px;font-size:0.78em;color:#999;">'
            f'Generated by {llm_backend.upper()} ({llm_model_label})</div></div>'
        )

    # Rationale
    rationale_html = ''
    if suggestion.get('rationale'):
        rationale_html = (
            f'<div style="background:#fff;padding:14px 20px;border-radius:8px;margin:14px 0;'
            f'box-shadow:0 2px 6px rgba(0,0,0,0.06);">'
            f'<strong>LLM Variable Selection Rationale:</strong> '
            f'{suggestion["rationale"]}</div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sensitivity Analysis — {now.strftime('%B %d, %Y')}</title>
<style>
    @media print {{ body {{ background: #fff; }} .no-print {{ display: none; }}
        details {{ open; }} details > summary {{ list-style: none; }}
    }}
    body {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        margin: 0; padding: 0; background: #f4f6f9; color: #2d3436;
    }}
    .container {{ max-width: 960px; margin: 0 auto; padding: 20px; }}
    header {{
        background: linear-gradient(135deg, #0c2340, #1a5276);
        color: #fff; padding: 24px 32px; border-radius: 0 0 8px 8px;
    }}
    header h1 {{ margin: 0 0 4px; font-size: 1.5em; }}
    header p {{ margin: 0; opacity: 0.85; font-size: 0.9em; }}
    .stat-grid {{
        display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 12px; margin: 14px 0;
    }}
    .stat-card {{
        background: #fff; border-radius: 8px; padding: 12px 16px; text-align: center;
        box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }}
    .stat-card .value {{ font-size: 1.5em; font-weight: 700; color: #0c2340; }}
    .stat-card .label {{ font-size: 0.8em; color: #636e72; margin-top: 2px; }}
    footer {{
        text-align: center; padding: 16px; font-size: 0.8em; color: #95a5a6;
    }}
</style>
</head>
<body>
<header>
    <h1>LLM-Driven Sensitivity Analysis</h1>
    <p>Last {days} days | {len(assessments)} signals assessed |
       {len(var_results)} variables + {len(scen_results)} scenarios tested |
       Generated {now.strftime('%B %d, %Y %H:%M')}</p>
    <p class="no-print" style="margin-top:8px;font-size:0.85em;opacity:0.7;">
        Tip: Use browser Print (Ctrl+P / Cmd+P) → Save as PDF</p>
</header>
<div class="container">

<div class="stat-grid">
    <div class="stat-card"><div class="value">{len(var_results)}</div>
        <div class="label">Variables Tested</div></div>
    <div class="stat-card"><div class="value">{len(scen_results)}</div>
        <div class="label">Scenarios Tested</div></div>
    <div class="stat-card"><div class="value">{len(assessments)}</div>
        <div class="label">News Signals</div></div>
</div>

{rationale_html}
{interp_html}

<h2 style="margin:20px 0 8px;color:#2c3e50;font-size:1.15em;">
    Variable Sensitivities ({len(var_results)})</h2>
{''.join(var_cards)}

{'<h2 style="margin:20px 0 8px;color:#2c3e50;font-size:1.15em;">Multi-Shock Scenarios (' + str(len(scen_results)) + ')</h2>' + chr(10).join(scen_cards) if scen_cards else ''}

<div style="background:#fff;padding:12px 18px;border-radius:8px;margin:14px 0;font-size:0.85em;
            box-shadow:0 2px 6px rgba(0,0,0,0.06);">
    <strong>Methodology:</strong>
    Variables selected by {llm_backend.upper()} ({llm_model_label}) based on recent news signals.
    Sensitivity = 1σ shock through DecarbIQ multi-stage pipeline
    (saturation → lag → seasonal → regime → gas → cross-market → electricity → feedback → uncertainty).
    Dynamic results include regime detection, seasonal multipliers, and feedback loops.
    Source: decarbiq_sensitivity_analysis.json
</div>

</div>
<footer>
    Blue H2/Ammonia Intelligence Monitor — LLM-Driven Sensitivity Analysis
</footer>
</body>
</html>"""

    fpath.write_text(html, encoding='utf-8')
    try:
        webbrowser.open(f'file://{fpath}')
    except Exception:
        pass
    return str(fpath)


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    app = BlueH2Intelligence()
    app.run()
