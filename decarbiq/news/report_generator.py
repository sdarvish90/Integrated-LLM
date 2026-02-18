#!/usr/bin/env python3
"""
Report Generator — HTML and Markdown reports for Blue H2 Intelligence
======================================================================
Separated from blue_h2_intelligence.py to keep the menu clean and allow
swapping report formats (HTML, Markdown, PDF) independently.

Sections:
  1. Executive Summary
  2. Gas Price Outlook
  3. Project Pipeline
  4. Market Assessment
  5. Competitive Landscape
  6. Client Extension Section (if configured)
  7. Methodology & Sources

Usage:
    from report_generator import ReportGenerator
    gen = ReportGenerator()
    path = gen.generate_html(projects, assessments, connector)
"""
from __future__ import annotations

import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from news_market_signal_parser import Project, STATUS_FID_PROBABILITY, EQUIPMENT_LEAD_TIMES
from decarbiq_market_connector import MarketAssessment, DecarbIQMarketConnector

_HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# CSS THEME
# ---------------------------------------------------------------------------

_CSS = """
body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    margin: 0; padding: 0; background: #f4f6f9; color: #2d3436;
}
.container { max-width: 1100px; margin: 0 auto; padding: 20px; }
header {
    background: linear-gradient(135deg, #0c2340, #1a5276);
    color: #fff; padding: 30px 40px; border-radius: 0 0 8px 8px;
}
header h1 { margin: 0 0 6px; font-size: 1.8em; }
header p { margin: 0; opacity: 0.85; font-size: 0.95em; }
h2 {
    color: #1a5276; border-bottom: 2px solid #3498db;
    padding-bottom: 6px; margin-top: 32px;
}
h3 { color: #2c3e50; margin-top: 24px; }
table {
    width: 100%; border-collapse: collapse; margin: 12px 0;
    font-size: 0.9em;
}
th {
    background: #1a5276; color: #fff; padding: 10px 8px;
    text-align: left; font-weight: 600;
}
td { padding: 8px; border-bottom: 1px solid #ddd; }
tr:nth-child(even) { background: #f8f9fa; }
tr:hover { background: #edf2f7; }
.stat-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px; margin: 16px 0;
}
.stat-card {
    background: #fff; border-radius: 8px; padding: 16px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.08); text-align: center;
}
.stat-card .value { font-size: 1.6em; font-weight: 700; color: #1a5276; }
.stat-card .label { font-size: 0.85em; color: #636e72; margin-top: 4px; }
.badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 0.8em; font-weight: 600;
}
.badge-high { background: #e74c3c; color: #fff; }
.badge-medium { background: #f39c12; color: #fff; }
.badge-low { background: #27ae60; color: #fff; }
.evidence-box {
    background: #f8f9fa; border-left: 3px solid #3498db;
    padding: 10px 14px; margin: 8px 0; font-size: 0.85em;
    font-family: monospace; white-space: pre-wrap;
}
.section { background: #fff; border-radius: 8px; padding: 20px 24px;
    margin: 16px 0; box-shadow: 0 2px 6px rgba(0,0,0,0.06); }
.note { font-size: 0.85em; color: #636e72; font-style: italic; }
footer {
    text-align: center; padding: 20px; font-size: 0.8em; color: #95a5a6;
    margin-top: 30px;
}
"""


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def _fmt(val, fmt='.3f', suffix=''):
    if val is None:
        return 'N/A'
    return f"{val:{fmt}}{suffix}"


def _confidence_badge(conf: str) -> str:
    cls = {'HIGH': 'badge-high', 'MEDIUM': 'badge-medium'}.get(conf, 'badge-low')
    return f'<span class="badge {cls}">{conf}</span>'


def _status_color(status: str) -> str:
    colors = {
        'Operational': '#27ae60', 'Commissioning': '#2ecc71',
        'Construction': '#3498db', 'EPC Award': '#2980b9',
        'FID': '#8e44ad', 'FEED': '#f39c12', 'Pre-FEED': '#e67e22',
        'Announced': '#95a5a6', 'Cancelled': '#e74c3c', 'Delayed': '#d35400',
    }
    c = colors.get(status, '#95a5a6')
    return f'<span style="color:{c};font-weight:600;">{status}</span>'


# ---------------------------------------------------------------------------
# REPORT GENERATOR CLASS
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Generate HTML and Markdown intelligence reports."""

    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else _HERE / 'reports'
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ====================================================================
    # HTML REPORT
    # ====================================================================

    def generate_html(
        self,
        projects: List[Project],
        assessments: List[MarketAssessment],
        connector: DecarbIQMarketConnector,
        extension_section: Optional[str] = None,
        open_browser: bool = True,
    ) -> str:
        """Generate full HTML report. Returns file path."""
        now = datetime.now()
        ts = now.strftime('%Y%m%d_%H%M')
        fname = f"blue_h2_report_{ts}.html"
        fpath = self.output_dir / fname

        sections = [
            self._render_executive_summary(projects, connector),
            self._render_gas_outlook(connector),
            self._render_pipeline_table(projects),
            self._render_assessment_section(assessments),
            self._render_competitive_landscape(projects),
        ]
        if extension_section:
            sections.append(extension_section)
        sections.append(self._render_methodology(assessments))

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Blue H2/Ammonia Market Intelligence — {now.strftime('%B %d, %Y')}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
    <h1>Blue H2 / Ammonia Market Intelligence</h1>
    <p>Generated {now.strftime('%B %d, %Y at %H:%M')} | DecarbIQ Model v2</p>
</header>
<div class="container">
{''.join(sections)}
</div>
<footer>
    Generated by Blue H2 Intelligence Monitor &mdash; powered by DecarbIQ Market Analyzer<br>
    All projections use trained model coefficients. See Methodology section for sources.
</footer>
</body>
</html>"""

        fpath.write_text(html, encoding='utf-8')
        print(f"\n  Report saved: {fpath}")

        if open_browser:
            try:
                webbrowser.open(f'file://{fpath}')
            except Exception:
                pass

        return str(fpath)

    # -- Section renderers --------------------------------------------------

    def _render_executive_summary(self, projects: List[Project],
                                   connector: DecarbIQMarketConnector) -> str:
        active = [p for p in projects if p.status != 'Cancelled']
        total_cap = sum(p.capacity_mtpa_h2 or 0 for p in active)
        weighted_cap = sum((p.capacity_mtpa_h2 or 0) * p.fid_probability for p in active)

        impact = connector.get_cumulative_pipeline_impact(projects)
        gas_demand = impact.get('weighted_gas_demand_bcfd', 0)
        gas_price = impact.get('gas_price_impact_per_mmbtu', 0)

        by_status = {}
        for p in active:
            s = p.status or 'Unknown'
            by_status[s] = by_status.get(s, 0) + 1

        status_html = ' | '.join(f"{s}: {n}" for s, n in
                                  sorted(by_status.items(),
                                         key=lambda x: -STATUS_FID_PROBABILITY.get(x[0], 0)))

        return f"""
<div class="section">
<h2>1. Executive Summary</h2>
<div class="stat-grid">
    <div class="stat-card">
        <div class="value">{len(active)}</div>
        <div class="label">Active Projects</div>
    </div>
    <div class="stat-card">
        <div class="value">{total_cap:.2f}</div>
        <div class="label">Total Pipeline (MTPA H2)</div>
    </div>
    <div class="stat-card">
        <div class="value">{weighted_cap:.2f}</div>
        <div class="label">Prob-Weighted Cap (MTPA)</div>
    </div>
    <div class="stat-card">
        <div class="value">{gas_demand:.3f}</div>
        <div class="label">Weighted Gas Demand (bcf/d)</div>
    </div>
    <div class="stat-card">
        <div class="value">${gas_price:.4f}</div>
        <div class="label">Gas Price Impact ($/MMBtu)</div>
    </div>
</div>
<p><strong>By status:</strong> {status_html}</p>
</div>"""

    def _render_gas_outlook(self, connector: DecarbIQMarketConnector) -> str:
        outlook = connector.get_gas_price_outlook()
        if not outlook:
            return '<div class="section"><h2>2. Gas Price Outlook</h2><p>No MC data loaded.</p></div>'

        rows = ''
        for yr in sorted(outlook.keys()):
            d = outlook[yr]
            rows += f"""<tr>
                <td>{yr}</td>
                <td>{_fmt(d.get('p10'), '.2f')}</td>
                <td><strong>{_fmt(d.get('p50'), '.2f')}</strong></td>
                <td>{_fmt(d.get('p90'), '.2f')}</td>
                <td>{_fmt(d.get('mean'), '.2f')}</td>
            </tr>"""

        return f"""
<div class="section">
<h2>2. Gas Price Outlook (Henry Hub, $/MMBtu)</h2>
<p class="note">Source: DecarbIQ Monte Carlo (10,000 paths, regime-switching GARCH)</p>
<table>
<tr><th>Year</th><th>P10</th><th>P50</th><th>P90</th><th>Mean</th></tr>
{rows}
</table>
</div>"""

    def _render_pipeline_table(self, projects: List[Project]) -> str:
        if not projects:
            return '<div class="section"><h2>3. Project Pipeline</h2><p>No projects parsed.</p></div>'

        rows = ''
        for p in sorted(projects, key=lambda x: -x.fid_probability):
            cap = _fmt(p.capacity_mtpa_h2, '.3f') if p.capacity_mtpa_h2 else 'N/A'
            rows += f"""<tr>
                <td>{p.project_name}</td>
                <td>{p.developer or 'N/A'}</td>
                <td>{cap}</td>
                <td>{p.technology or 'N/A'}</td>
                <td>{p.product or 'N/A'}</td>
                <td>{_status_color(p.status or 'Unknown')}</td>
                <td>{p.fid_probability:.0%}</td>
                <td>{p.region or 'N/A'}</td>
                <td>{p.epc_contractor or 'N/A'}</td>
            </tr>"""

        return f"""
<div class="section">
<h2>3. Project Pipeline</h2>
<table>
<tr><th>Project</th><th>Developer</th><th>Capacity (MTPA H2)</th><th>Technology</th>
<th>Product</th><th>Status</th><th>FID Prob</th><th>Region</th><th>EPC</th></tr>
{rows}
</table>
</div>"""

    def _render_assessment_section(self, assessments: List[MarketAssessment]) -> str:
        if not assessments:
            return '<div class="section"><h2>4. Market Assessments</h2><p>No assessments.</p></div>'

        items = ''
        for a in sorted(assessments, key=lambda x: x.signal.confidence, reverse=True)[:20]:
            sig = a.signal
            ev = a.model_evidence

            impact_lines = []
            if a.incremental_gas_demand_bcfd is not None:
                impact_lines.append(f"Gas demand: +{a.incremental_gas_demand_bcfd:.4f} bcf/d")
            if a.gas_price_impact_per_mmbtu is not None:
                impact_lines.append(f"HH gas price: +${a.gas_price_impact_per_mmbtu:.5f}/MMBtu")
            if a.ercot_impact_per_mwh is not None:
                impact_lines.append(f"ERCOT electricity: +${a.ercot_impact_per_mwh:.4f}/MWh")
            if a.lcoh_impact_usd_per_kg is not None:
                impact_lines.append(f"Blue H2 LCOH: +${a.lcoh_impact_usd_per_kg:.5f}/kg")
            if a.policy_scenario_match:
                impact_lines.append(f"Policy scenario: {a.policy_scenario_match}")

            impact_html = '<br>'.join(impact_lines) if impact_lines else 'No quantifiable impact'

            evidence_text = '\n'.join(f"  {k}: {v}" for k, v in ev.items()) if ev else 'None'
            notes_html = '<br>'.join(a.methodology_notes) if a.methodology_notes else ''

            items += f"""
<div style="margin-bottom:16px; padding:12px; border:1px solid #e0e0e0; border-radius:6px;">
    <h3 style="margin-top:0;">{sig.article_title or 'Untitled'} {_confidence_badge(a.confidence)}</h3>
    <p><strong>Developer:</strong> {sig.developer or 'N/A'} |
       <strong>Status:</strong> {_status_color(sig.status or 'Unknown')} |
       <strong>Capacity:</strong> {_fmt(sig.capacity_mtpa_h2, '.3f', ' MTPA H2')} |
       <strong>Impact year:</strong> {a.year_of_impact}</p>
    <p><strong>Market Impact:</strong><br>{impact_html}</p>
    {f'<p><strong>Notes:</strong><br>{notes_html}</p>' if notes_html else ''}
    <details><summary>Model Evidence Trail</summary>
    <div class="evidence-box">{evidence_text}</div>
    </details>
</div>"""

        return f"""
<div class="section">
<h2>4. Market Assessments (Top 20)</h2>
<p class="note">Each assessment traces through DecarbIQ model coefficients. Click "Model Evidence Trail" for audit.</p>
{items}
</div>"""

    def _render_competitive_landscape(self, projects: List[Project]) -> str:
        active = [p for p in projects if p.status != 'Cancelled']
        if not active:
            return '<div class="section"><h2>5. Competitive Landscape</h2><p>No data.</p></div>'

        # By developer
        by_dev: Dict[str, Dict] = {}
        for p in active:
            d = p.developer or 'Unknown'
            if d not in by_dev:
                by_dev[d] = {'count': 0, 'cap': 0.0, 'weighted_cap': 0.0, 'regions': set()}
            by_dev[d]['count'] += 1
            by_dev[d]['cap'] += p.capacity_mtpa_h2 or 0
            by_dev[d]['weighted_cap'] += (p.capacity_mtpa_h2 or 0) * p.fid_probability
            by_dev[d]['regions'].add(p.region or 'Unknown')

        dev_rows = ''
        for d, info in sorted(by_dev.items(), key=lambda x: -x[1]['cap']):
            dev_rows += f"""<tr>
                <td>{d.title()}</td>
                <td>{info['count']}</td>
                <td>{info['cap']:.3f}</td>
                <td>{info['weighted_cap']:.3f}</td>
                <td>{', '.join(sorted(info['regions']))}</td>
            </tr>"""

        # By region
        by_region: Dict[str, Dict] = {}
        for p in active:
            r = p.region or 'Unknown'
            if r not in by_region:
                by_region[r] = {'count': 0, 'cap': 0.0, 'weighted_cap': 0.0}
            by_region[r]['count'] += 1
            by_region[r]['cap'] += p.capacity_mtpa_h2 or 0
            by_region[r]['weighted_cap'] += (p.capacity_mtpa_h2 or 0) * p.fid_probability

        region_rows = ''
        for r, info in sorted(by_region.items(), key=lambda x: -x[1]['cap']):
            region_rows += f"""<tr>
                <td>{r}</td>
                <td>{info['count']}</td>
                <td>{info['cap']:.3f}</td>
                <td>{info['weighted_cap']:.3f}</td>
            </tr>"""

        return f"""
<div class="section">
<h2>5. Competitive Landscape</h2>

<h3>By Developer</h3>
<table>
<tr><th>Developer</th><th>Projects</th><th>Total Cap (MTPA)</th><th>Weighted Cap</th><th>Regions</th></tr>
{dev_rows}
</table>

<h3>By Region</h3>
<table>
<tr><th>Region</th><th>Projects</th><th>Total Cap (MTPA)</th><th>Weighted Cap</th></tr>
{region_rows}
</table>
</div>"""

    def _render_methodology(self, assessments: List[MarketAssessment]) -> str:
        # Collect all unique sources cited across assessments
        all_sources: set = set()
        for a in assessments:
            all_sources.update(a.literature_sources)

        sources_html = '\n'.join(f'<li>{s}</li>' for s in sorted(all_sources)) if all_sources else '<li>No assessments generated</li>'

        return f"""
<div class="section">
<h2>7. Methodology & Sources</h2>

<h3>Model Components Used</h3>
<ul>
    <li><strong>DecarbIQ Projection Map</strong> — P10/P50/P90 gas &amp; electricity, 2025-2035, trained regression coefficients</li>
    <li><strong>Monte Carlo LNG v8</strong> — 10,000-path regime-switching GARCH simulation for Henry Hub</li>
    <li><strong>Sensitivity Analysis</strong> — 28 variables, 68 shocks, 9-stage pipeline with feedback rules</li>
    <li><strong>Event Frequency Model</strong> — Probability-weighted disruption scenarios (hurricane, polar vortex, geopolitical)</li>
</ul>

<h3>Key Coefficients</h3>
<table>
<tr><th>Parameter</th><th>Value</th><th>Source</th></tr>
<tr><td>HH gas demand coefficient</td><td>-0.0452 $/MMBtu per bcf/d</td><td>DecarbIQ regression (hh_full)</td></tr>
<tr><td>ERCOT gas passthrough</td><td>$6.94/MWh per $/MMBtu</td><td>DecarbIQ regression</td></tr>
<tr><td>CAISO gas passthrough</td><td>$9.51/MWh per $/MMBtu</td><td>DecarbIQ regression</td></tr>
<tr><td>SMR gas consumption</td><td>0.155 bcf/d per MTPA H2</td><td>NETL (2010) Table 3-1</td></tr>
<tr><td>ATR gas consumption</td><td>0.135 bcf/d per MTPA H2</td><td>IEAGHG (2017)</td></tr>
<tr><td>Gas → LCOH sensitivity</td><td>$0.14/kg per $/MMBtu</td><td>NETL H2A (2022)</td></tr>
<tr><td>NH3 → H2 mass ratio</td><td>0.178</td><td>IEA "Future of Hydrogen" (2019)</td></tr>
<tr><td>45Q credit impact</td><td>$0.79/kg H2</td><td>IRC §45Q; IEAGHG (2017)</td></tr>
</table>

<h3>Literature Sources Cited in Assessments</h3>
<ul>
{sources_html}
</ul>

<h3>FID Probability by Status</h3>
<table>
<tr><th>Status</th><th>FID Probability</th><th>Source</th></tr>
{''.join(f"<tr><td>{s}</td><td>{p:.0%}</td><td>Default baseline (override via FID Probability Engine)</td></tr>"
         for s, p in sorted(STATUS_FID_PROBABILITY.items(), key=lambda x: -x[1]))}
</table>

<p class="note">All model outputs reference specific JSON keys in the DecarbIQ package.
Each assessment includes a "Model Evidence Trail" with exact coefficient sources.</p>
</div>"""

    # ====================================================================
    # MARKDOWN REPORT
    # ====================================================================

    def generate_markdown(
        self,
        projects: List[Project],
        assessments: List[MarketAssessment],
        connector: DecarbIQMarketConnector,
        extension_section: Optional[str] = None,
    ) -> str:
        """Generate Markdown report. Returns file path."""
        now = datetime.now()
        ts = now.strftime('%Y%m%d_%H%M')
        fname = f"blue_h2_report_{ts}.md"
        fpath = self.output_dir / fname

        active = [p for p in projects if p.status != 'Cancelled']
        impact = connector.get_cumulative_pipeline_impact(projects)

        lines = [
            f"# Blue H2 / Ammonia Market Intelligence",
            f"*Generated {now.strftime('%B %d, %Y at %H:%M')} | DecarbIQ Model v2*\n",
            "---\n",
            "## 1. Executive Summary\n",
            f"- **Active projects:** {len(active)}",
            f"- **Total pipeline:** {sum(p.capacity_mtpa_h2 or 0 for p in active):.2f} MTPA H2",
            f"- **Prob-weighted capacity:** {impact.get('probability_weighted_capacity_mtpa', 0):.2f} MTPA",
            f"- **Weighted gas demand:** {impact.get('weighted_gas_demand_bcfd', 0):.3f} bcf/d",
            f"- **Gas price impact:** ${impact.get('gas_price_impact_per_mmbtu', 0):.4f}/MMBtu\n",
        ]

        # Gas outlook
        lines.append("## 2. Gas Price Outlook ($/MMBtu)\n")
        outlook = connector.get_gas_price_outlook()
        if outlook:
            lines.append("| Year | P10 | P50 | P90 | Mean |")
            lines.append("|------|-----|-----|-----|------|")
            for yr in sorted(outlook.keys()):
                d = outlook[yr]
                lines.append(f"| {yr} | {_fmt(d.get('p10'), '.2f')} | {_fmt(d.get('p50'), '.2f')} | {_fmt(d.get('p90'), '.2f')} | {_fmt(d.get('mean'), '.2f')} |")
            lines.append("")

        # Pipeline
        lines.append("## 3. Project Pipeline\n")
        lines.append("| Project | Developer | Cap (MTPA) | Tech | Status | FID Prob | Region |")
        lines.append("|---------|-----------|------------|------|--------|----------|--------|")
        for p in sorted(projects, key=lambda x: -x.fid_probability):
            cap = f"{p.capacity_mtpa_h2:.3f}" if p.capacity_mtpa_h2 else "N/A"
            lines.append(f"| {p.project_name} | {p.developer or 'N/A'} | {cap} | {p.technology or 'N/A'} | {p.status or '?'} | {p.fid_probability:.0%} | {p.region or 'N/A'} |")
        lines.append("")

        # Assessments
        lines.append("## 4. Market Assessments\n")
        for a in sorted(assessments, key=lambda x: x.signal.confidence, reverse=True)[:20]:
            sig = a.signal
            lines.append(f"### {sig.article_title or 'Untitled'} [{a.confidence}]\n")
            if a.incremental_gas_demand_bcfd is not None:
                lines.append(f"- Gas demand: +{a.incremental_gas_demand_bcfd:.4f} bcf/d")
            if a.gas_price_impact_per_mmbtu is not None:
                lines.append(f"- HH gas price: +${a.gas_price_impact_per_mmbtu:.5f}/MMBtu")
            if a.ercot_impact_per_mwh is not None:
                lines.append(f"- ERCOT: +${a.ercot_impact_per_mwh:.4f}/MWh")
            if a.lcoh_impact_usd_per_kg is not None:
                lines.append(f"- LCOH: +${a.lcoh_impact_usd_per_kg:.5f}/kg")
            for note in a.methodology_notes:
                lines.append(f"- {note}")
            lines.append("")

        # Extension
        if extension_section:
            lines.append(extension_section)
            lines.append("")

        # Methodology
        lines.append("## 7. Methodology & Sources\n")
        lines.append("All projections use trained DecarbIQ model coefficients (no hardcoded impact %).\n")
        all_sources: set = set()
        for a in assessments:
            all_sources.update(a.literature_sources)
        for s in sorted(all_sources):
            lines.append(f"- {s}")
        lines.append("")

        content = '\n'.join(lines)
        fpath.write_text(content, encoding='utf-8')
        print(f"\n  Markdown report saved: {fpath}")
        return str(fpath)
