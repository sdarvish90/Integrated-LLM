#!/usr/bin/env python3
"""
Integrated Hydrogen Intelligence System
========================================

This system integrates all components in the following order:

1. website_dl.py - Downloads documents for the knowledge base
2. llm_training_system.py (Step 1 & 2) - Ingests and builds knowledge base
3. hydrogen_intelligence_monitor.py - Collects news and analyzes impact
   - Uses tea_baseline_adapter.py for TEA baseline data
   - Uses the knowledge base for GROUNDED analysis (no AI guessing)

Key Feature: All conclusions about project impact (NPV, LCOH, IRR changes)
are derived ONLY from data in the knowledge database with explicit citations.

Usage:
    # Full pipeline
    python hydrogen_integrated_system.py --mode full
    
    # Individual steps
    python hydrogen_integrated_system.py --mode download  # Step 1: Download docs
    python hydrogen_integrated_system.py --mode ingest    # Step 2: Ingest to KB
    python hydrogen_integrated_system.py --mode analyze   # Step 3: News analysis
    
    # Interactive mode
    python hydrogen_integrated_system.py --mode interactive
"""

from __future__ import annotations

import os
import sys
import json
import argparse
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class IntegratedConfig:
    """Configuration for the integrated system"""
    
    # Paths
    downloads_dir: Path = Path("./downloads")
    knowledge_base_dir: Path = Path("./knowledge_base_db")
    news_db_path: Path = Path("./hydrogen_monitor.db")
    sensitivity_db_path: Path = Path("./sensitivity_analysis.db")
    
    # TEA Model paths
    tea_locations_root: Path = Path("/Users/Shadi/Dropbox/SHARE_Model_LLM")
    
    # Location mapping
    location_map: Dict[str, str] = field(default_factory=lambda: {
        'Duqm_Oman': 'Oman',
        'Magallines_Chile': 'Chile', 
        'Houston_USA': 'Houston'
    })
    
    # Analysis settings
    min_confidence_for_impact: float = 0.6
    max_articles_per_analysis: int = 50
    days_back_for_analysis: int = 7
    
    # API
    anthropic_api_key: Optional[str] = os.environ.get('ANTHROPIC_API_KEY')


CONFIG = IntegratedConfig()


# ============================================================================
# SENSITIVITY ANALYSIS DATABASE
# ============================================================================

def setup_sensitivity_database():
    """
    Create database to store sensitivity analysis data from training documents.
    This is the ONLY source of truth for impact quantification.
    """
    conn = sqlite3.connect(str(CONFIG.sensitivity_db_path))
    cursor = conn.cursor()
    
    # Sensitivity factors table - stores relationships from training data
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sensitivity_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parameter_name TEXT NOT NULL,
            parameter_unit TEXT,
            baseline_value REAL,
            change_amount REAL,
            change_unit TEXT,
            npv_impact_usd_million REAL,
            npv_impact_percent REAL,
            lcoh_impact_usd_per_kg REAL,
            lcoh_impact_percent REAL,
            irr_impact_percent REAL,
            region TEXT,
            source_document TEXT NOT NULL,
            source_page INTEGER,
            source_chunk_id TEXT,
            extraction_date TEXT,
            confidence_score REAL,
            notes TEXT,
            UNIQUE(parameter_name, change_amount, region, source_document)
        )
    ''')
    
    # Market benchmarks table - stores reference prices and costs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_benchmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_name TEXT NOT NULL,
            value REAL,
            unit TEXT,
            region TEXT,
            date_reported TEXT,
            source_document TEXT NOT NULL,
            source_page INTEGER,
            extraction_date TEXT,
            UNIQUE(metric_name, region, date_reported, source_document)
        )
    ''')
    
    # Impact rules table - stores derived rules for news impact
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS impact_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_category TEXT NOT NULL,
            trigger_keywords TEXT,
            affected_parameter TEXT,
            typical_change_min REAL,
            typical_change_max REAL,
            npv_impact_formula TEXT,
            source_documents TEXT,
            confidence_level TEXT,
            notes TEXT
        )
    ''')
    
    # Analysis audit trail - tracks every conclusion made
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analysis_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_date TEXT,
            article_title TEXT,
            article_url TEXT,
            conclusion_type TEXT,
            npv_change_claimed REAL,
            lcoh_change_claimed REAL,
            irr_change_claimed REAL,
            supporting_sensitivity_ids TEXT,
            supporting_documents TEXT,
            confidence_score REAL,
            reasoning TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✓ Sensitivity analysis database initialized")


# ============================================================================
# KNOWLEDGE BASE INTEGRATION
# ============================================================================

class KnowledgeBaseManager:
    """
    Manages the knowledge base and extracts sensitivity analysis data.
    """
    
    def __init__(self, config: IntegratedConfig):
        self.config = config
        self.sensitivity_data = []
        
    def extract_sensitivity_from_documents(self, documents: List[Dict]) -> int:
        """
        Extract sensitivity analysis data from ingested documents.
        Looks for tables, charts, and text mentioning parameter impacts.
        
        Returns number of sensitivity factors extracted.
        """
        conn = sqlite3.connect(str(self.config.sensitivity_db_path))
        cursor = conn.cursor()
        
        extracted_count = 0
        
        # Patterns to identify sensitivity data
        sensitivity_patterns = [
            # Pattern: "$X/MWh change in electricity price results in $Y/kg LCOH change"
            r'(\$?[\d.]+)\s*(?:per\s+)?([A-Za-z/]+)\s+(?:change|increase|decrease|reduction)\s+(?:in|of)\s+([a-zA-Z\s]+)\s+(?:results in|leads to|causes)\s+(\$?[\d.]+)\s*(?:per\s+)?([A-Za-z/]+)\s+(?:change|impact)',
            
            # Pattern: "LCOH sensitivity to electricity: $X/kg per $Y/MWh"
            r'([A-Z]+)\s+sensitivity\s+to\s+([a-zA-Z\s]+):\s+(\$?[\d.]+)([A-Za-z/]+)\s+per\s+(\$?[\d.]+)([A-Za-z/]+)',
            
            # Pattern: "10% increase in CAPEX increases LCOH by $0.15/kg"
            r'([\d.]+)%?\s+(?:increase|decrease|change)\s+in\s+([A-Za-z\s]+)\s+(?:increases|decreases|changes)\s+([A-Z]+)\s+by\s+(\$?[\d.]+)([A-Za-z/%]+)',
        ]
        
        for doc in documents:
            text = doc.get('text', '')
            source = doc.get('source', 'unknown')
            chunk_id = f"{source}_{doc.get('chunk_id', 0)}"
            
            # Look for sensitivity tables (common format in TEA reports)
            if self._contains_sensitivity_table(text):
                factors = self._parse_sensitivity_table(text, source, chunk_id)
                for factor in factors:
                    try:
                        cursor.execute('''
                            INSERT OR REPLACE INTO sensitivity_factors
                            (parameter_name, parameter_unit, baseline_value, change_amount,
                             change_unit, npv_impact_usd_million, npv_impact_percent,
                             lcoh_impact_usd_per_kg, lcoh_impact_percent, irr_impact_percent,
                             region, source_document, source_chunk_id, extraction_date,
                             confidence_score, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            factor['parameter_name'],
                            factor.get('parameter_unit'),
                            factor.get('baseline_value'),
                            factor.get('change_amount'),
                            factor.get('change_unit'),
                            factor.get('npv_impact_usd_million'),
                            factor.get('npv_impact_percent'),
                            factor.get('lcoh_impact_usd_per_kg'),
                            factor.get('lcoh_impact_percent'),
                            factor.get('irr_impact_percent'),
                            factor.get('region'),
                            source,
                            chunk_id,
                            datetime.now().isoformat(),
                            factor.get('confidence_score', 0.8),
                            factor.get('notes')
                        ))
                        extracted_count += 1
                    except Exception as e:
                        print(f"  Warning: Could not insert factor: {e}")
            
            # Also extract market benchmarks
            benchmarks = self._extract_market_benchmarks(text, source)
            for benchmark in benchmarks:
                try:
                    cursor.execute('''
                        INSERT OR REPLACE INTO market_benchmarks
                        (metric_name, value, unit, region, date_reported,
                         source_document, extraction_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        benchmark['metric_name'],
                        benchmark['value'],
                        benchmark.get('unit'),
                        benchmark.get('region'),
                        benchmark.get('date_reported'),
                        source,
                        datetime.now().isoformat()
                    ))
                except Exception as e:
                    pass
        
        conn.commit()
        conn.close()
        
        return extracted_count
    
    def _contains_sensitivity_table(self, text: str) -> bool:
        """Check if text contains sensitivity analysis data"""
        indicators = [
            'sensitivity analysis', 'sensitivity table', 'tornado chart',
            'parameter sensitivity', 'impact on npv', 'impact on lcoh',
            '$/kg change', '%/MWh', 'capex sensitivity', 'opex sensitivity'
        ]
        text_lower = text.lower()
        return any(ind in text_lower for ind in indicators)
    
    def _parse_sensitivity_table(self, text: str, source: str, chunk_id: str) -> List[Dict]:
        """
        Parse sensitivity analysis from text.
        
        This function extracts structured sensitivity data from various formats:
        - Tables with columns like: Parameter | Base | -10% | +10% | NPV Impact
        - Inline statements like "10% CAPEX increase → $0.15/kg LCOH increase"
        """
        import re
        factors = []
        
        # Common sensitivity relationships from hydrogen TEA reports
        # These are patterns we expect to find in the training documents
        
        # Pattern 1: Electricity price sensitivity
        elec_match = re.search(
            r'electricity.*?(\d+\.?\d*).*?(?:\$/MWh|per MWh).*?(?:results?|leads?|causes?).*?(\d+\.?\d*).*?(?:\$/kg|per kg)',
            text, re.IGNORECASE | re.DOTALL
        )
        if elec_match:
            factors.append({
                'parameter_name': 'electricity_price',
                'parameter_unit': '$/MWh',
                'change_amount': float(elec_match.group(1)),
                'change_unit': '$/MWh',
                'lcoh_impact_usd_per_kg': float(elec_match.group(2)),
                'confidence_score': 0.85,
                'notes': f'Extracted from {source}'
            })
        
        # Pattern 2: CAPEX sensitivity
        capex_match = re.search(
            r'(?:capex|capital cost).*?(\d+\.?\d*)%.*?(?:change|increase|decrease).*?(?:npv|lcoh).*?(\d+\.?\d*)(?:%|M|\$/kg)',
            text, re.IGNORECASE | re.DOTALL
        )
        if capex_match:
            factors.append({
                'parameter_name': 'electrolyzer_capex',
                'change_amount': float(capex_match.group(1)),
                'change_unit': '%',
                'npv_impact_percent': float(capex_match.group(2)),
                'confidence_score': 0.80,
                'notes': f'Extracted from {source}'
            })
        
        # Pattern 3: Capacity factor sensitivity
        cf_match = re.search(
            r'capacity factor.*?(\d+\.?\d*)%.*?(?:point|percentage).*?(?:change|increase|decrease).*?lcoh.*?(\d+\.?\d*)(?:\$/kg|%)',
            text, re.IGNORECASE | re.DOTALL
        )
        if cf_match:
            factors.append({
                'parameter_name': 'capacity_factor',
                'change_amount': float(cf_match.group(1)),
                'change_unit': '% points',
                'lcoh_impact_usd_per_kg': float(cf_match.group(2)),
                'confidence_score': 0.75,
                'notes': f'Extracted from {source}'
            })
        
        # Pattern 4: Hydrogen price / offtake price sensitivity
        h2_price_match = re.search(
            r'(?:hydrogen|h2|offtake)\s*price.*?(\d+\.?\d*).*?(?:\$/kg|per kg).*?(?:change|increase|decrease).*?npv.*?(\d+\.?\d*)(?:%|M)',
            text, re.IGNORECASE | re.DOTALL
        )
        if h2_price_match:
            factors.append({
                'parameter_name': 'hydrogen_offtake_price',
                'change_amount': float(h2_price_match.group(1)),
                'change_unit': '$/kg',
                'npv_impact_usd_million': float(h2_price_match.group(2)),
                'confidence_score': 0.85,
                'notes': f'Extracted from {source}'
            })
        
        return factors
    
    def _extract_market_benchmarks(self, text: str, source: str) -> List[Dict]:
        """Extract market benchmark data (prices, costs, etc.)"""
        import re
        benchmarks = []
        
        # LCOH benchmarks
        lcoh_match = re.search(r'LCOH.*?(\d+\.?\d*)\s*(?:\$/kg|per kg)', text, re.IGNORECASE)
        if lcoh_match:
            benchmarks.append({
                'metric_name': 'LCOH',
                'value': float(lcoh_match.group(1)),
                'unit': '$/kg',
                'date_reported': datetime.now().strftime('%Y-%m-%d')
            })
        
        # Electrolyzer cost
        elec_cost_match = re.search(r'electrolyzer.*?(\d+)\s*(?:\$/kW|per kW)', text, re.IGNORECASE)
        if elec_cost_match:
            benchmarks.append({
                'metric_name': 'electrolyzer_cost',
                'value': float(elec_cost_match.group(1)),
                'unit': '$/kW',
                'date_reported': datetime.now().strftime('%Y-%m-%d')
            })
        
        return benchmarks


# ============================================================================
# GROUNDED IMPACT ANALYZER (NO AI GUESSING)
# ============================================================================

class GroundedImpactAnalyzer:
    """
    Analyzes news impact using ONLY data from the knowledge base.
    Every conclusion must have a citation to the source document.
    """
    
    def __init__(self, config: IntegratedConfig):
        self.config = config
        self.sensitivity_cache = {}
        self._load_sensitivity_data()
    
    def _load_sensitivity_data(self):
        """Load sensitivity factors from database into cache"""
        conn = sqlite3.connect(str(self.config.sensitivity_db_path))
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM sensitivity_factors')
        rows = cursor.fetchall()
        
        for row in rows:
            param_name = row[1]  # parameter_name
            if param_name not in self.sensitivity_cache:
                self.sensitivity_cache[param_name] = []
            self.sensitivity_cache[param_name].append({
                'id': row[0],
                'parameter_name': row[1],
                'parameter_unit': row[2],
                'baseline_value': row[3],
                'change_amount': row[4],
                'change_unit': row[5],
                'npv_impact_usd_million': row[6],
                'npv_impact_percent': row[7],
                'lcoh_impact_usd_per_kg': row[8],
                'lcoh_impact_percent': row[9],
                'irr_impact_percent': row[10],
                'region': row[11],
                'source_document': row[12],
                'confidence_score': row[15]
            })
        
        conn.close()
    
    def analyze_news_impact(
        self,
        news_category: str,
        news_keywords: List[str],
        news_text: str,
        region: str,
        tea_baseline: Dict
    ) -> Dict:
        """
        Analyze news impact with full citations.
        
        Returns:
            {
                'has_quantifiable_impact': bool,
                'npv_change': float,
                'lcoh_change': float,
                'irr_change': float,
                'confidence': float,
                'citations': [
                    {
                        'source': 'document_name',
                        'page': int,
                        'quote': 'relevant text',
                        'parameter_used': 'electricity_price',
                        'sensitivity_applied': '$10/MWh → $0.05/kg LCOH'
                    }
                ],
                'reasoning': str,
                'data_gaps': [str]  # What we couldn't quantify
            }
        """
        result = {
            'has_quantifiable_impact': False,
            'npv_change': 0.0,
            'lcoh_change': 0.0,
            'irr_change': 0.0,
            'confidence': 0.0,
            'citations': [],
            'reasoning': '',
            'data_gaps': []
        }
        
        # Map news categories to affected parameters
        category_param_map = {
            'POLICY': ['subsidy_rate', 'tax_credit', 'carbon_price'],
            'TECHNOLOGY': ['electrolyzer_capex', 'electrolyzer_efficiency', 'capacity_factor'],
            'MARKET': ['hydrogen_offtake_price', 'electricity_price', 'ammonia_price'],
            'FINANCING': ['interest_rate', 'capex', 'project_timeline'],
            'COMPETITION': ['market_share', 'offtake_price', 'land_availability'],
            'SUPPLY_CHAIN': ['electrolyzer_capex', 'equipment_delivery', 'construction_cost']
        }
        
        affected_params = category_param_map.get(news_category, [])
        
        # Check for specific keyword triggers
        text_lower = news_text.lower()
        
        # Extract quantitative mentions from news
        extracted_values = self._extract_values_from_news(text_lower)
        
        reasoning_parts = []
        total_confidence = 0.0
        num_factors = 0
        
        for param in affected_params:
            if param in self.sensitivity_cache:
                sensitivities = self.sensitivity_cache[param]
                
                # Filter by region if available
                region_sensitivities = [s for s in sensitivities if s['region'] == region or s['region'] is None]
                if not region_sensitivities:
                    region_sensitivities = sensitivities
                
                for sensitivity in region_sensitivities:
                    # Check if we can apply this sensitivity
                    if param in extracted_values:
                        # We have a value from the news to apply
                        news_value = extracted_values[param]
                        
                        # Calculate impact using the sensitivity factor
                        if sensitivity['lcoh_impact_usd_per_kg'] and sensitivity['change_amount']:
                            ratio = news_value / sensitivity['change_amount']
                            lcoh_impact = sensitivity['lcoh_impact_usd_per_kg'] * ratio
                            result['lcoh_change'] += lcoh_impact
                            result['has_quantifiable_impact'] = True
                            
                            result['citations'].append({
                                'source': sensitivity['source_document'],
                                'parameter_used': param,
                                'sensitivity_applied': f"{sensitivity['change_amount']} {sensitivity['change_unit']} → ${sensitivity['lcoh_impact_usd_per_kg']:.3f}/kg LCOH",
                                'news_value_used': news_value,
                                'calculated_impact': f"${lcoh_impact:.3f}/kg LCOH change"
                            })
                            
                            reasoning_parts.append(
                                f"Based on {sensitivity['source_document']}: "
                                f"{param} change of {news_value} {sensitivity['change_unit']} "
                                f"→ LCOH change of ${lcoh_impact:.3f}/kg"
                            )
                        
                        if sensitivity['npv_impact_percent'] and sensitivity['change_amount']:
                            ratio = news_value / sensitivity['change_amount']
                            npv_impact_pct = sensitivity['npv_impact_percent'] * ratio
                            npv_impact = tea_baseline.get('npv', 580) * (npv_impact_pct / 100)
                            result['npv_change'] += npv_impact
                            result['has_quantifiable_impact'] = True
                            
                            result['citations'].append({
                                'source': sensitivity['source_document'],
                                'parameter_used': param,
                                'sensitivity_applied': f"{sensitivity['change_amount']}% → {sensitivity['npv_impact_percent']:.1f}% NPV",
                                'news_value_used': news_value,
                                'calculated_impact': f"${npv_impact:.1f}M NPV change"
                            })
                        
                        total_confidence += sensitivity['confidence_score']
                        num_factors += 1
                    else:
                        # No specific value - note the data gap
                        result['data_gaps'].append(
                            f"Could not extract {param} value from news. "
                            f"Have sensitivity data from {sensitivity['source_document']} but no news value to apply."
                        )
        
        if num_factors > 0:
            result['confidence'] = total_confidence / num_factors
        
        result['reasoning'] = '\n'.join(reasoning_parts) if reasoning_parts else 'No quantifiable impact found based on available sensitivity data.'
        
        return result
    
    def _extract_values_from_news(self, text: str) -> Dict[str, float]:
        """Extract quantitative values from news text"""
        import re
        values = {}
        
        # Electricity price changes
        elec_match = re.search(r'(?:electricity|power)\s*(?:price|cost).*?(\d+\.?\d*)\s*(?:\$/MWh|per MWh)', text)
        if elec_match:
            values['electricity_price'] = float(elec_match.group(1))
        
        # Subsidy/tax credit amounts
        subsidy_match = re.search(r'(?:\$|USD\s*)(\d+\.?\d*)\s*(?:/kg|per kg).*?(?:subsidy|credit|incentive)', text)
        if subsidy_match:
            values['subsidy_rate'] = float(subsidy_match.group(1))
        
        # CAPEX changes (percentage)
        capex_match = re.search(r'(?:capex|capital cost|equipment cost).*?(?:down|up|increase|decrease|reduce).*?(\d+\.?\d*)%', text)
        if capex_match:
            values['electrolyzer_capex'] = float(capex_match.group(1))
        
        # Price changes (hydrogen/ammonia)
        h2_price_match = re.search(r'(?:hydrogen|h2|ammonia).*?price.*?(?:\$|USD\s*)(\d+\.?\d*)(?:/kg|per kg|/tonne)', text)
        if h2_price_match:
            values['hydrogen_offtake_price'] = float(h2_price_match.group(1))
        
        return values
    
    def get_available_sensitivities(self) -> Dict:
        """Return summary of available sensitivity data"""
        summary = {}
        for param, sensitivities in self.sensitivity_cache.items():
            summary[param] = {
                'num_data_points': len(sensitivities),
                'sources': list(set(s['source_document'] for s in sensitivities)),
                'regions': list(set(s['region'] for s in sensitivities if s['region']))
            }
        return summary


# ============================================================================
# INTEGRATED NEWS ANALYZER
# ============================================================================

class IntegratedNewsAnalyzer:
    """
    Main analyzer that combines knowledge base with news analysis.
    """
    
    def __init__(self, config: IntegratedConfig):
        self.config = config
        self.impact_analyzer = GroundedImpactAnalyzer(config)
        self.kb_manager = KnowledgeBaseManager(config)
        
    def analyze_article_with_citations(
        self,
        article: Dict,
        tea_baseline: Dict
    ) -> Dict:
        """
        Analyze a single article with full citation trail.
        """
        title = article.get('title', '')
        snippet = article.get('snippet', '')
        category = article.get('category', 'LOW')
        keywords = article.get('keywords', '').split(',')
        region = article.get('region', 'Oman')
        
        full_text = f"{title} {snippet}"
        
        # Get grounded impact analysis
        impact = self.impact_analyzer.analyze_news_impact(
            news_category=self._map_category_to_type(category, keywords),
            news_keywords=keywords,
            news_text=full_text,
            region=region,
            tea_baseline=tea_baseline
        )
        
        # Build result with full audit trail
        result = {
            'article': {
                'title': title,
                'url': article.get('url'),
                'category': category,
                'region': region
            },
            'impact': impact,
            'baseline_used': tea_baseline,
            'analysis_timestamp': datetime.now().isoformat(),
            'is_quantified': impact['has_quantifiable_impact'],
            'audit_trail': {
                'sensitivity_data_used': len(impact['citations']),
                'citations': impact['citations'],
                'data_gaps': impact['data_gaps']
            }
        }
        
        # Record in audit log
        self._record_audit(article, impact)
        
        return result
    
    def _map_category_to_type(self, category: str, keywords: List[str]) -> str:
        """Map article category/keywords to impact type"""
        keyword_str = ' '.join(keywords).lower()
        
        if any(k in keyword_str for k in ['45v', 'ira', 'subsidy', 'tax credit', 'policy']):
            return 'POLICY'
        elif any(k in keyword_str for k in ['electrolyzer', 'pem', 'alkaline', 'efficiency']):
            return 'TECHNOLOGY'
        elif any(k in keyword_str for k in ['price', 'offtake', 'market', 'demand']):
            return 'MARKET'
        elif any(k in keyword_str for k in ['financing', 'loan', 'equity', 'investment']):
            return 'FINANCING'
        elif any(k in keyword_str for k in ['competitor', 'rival', 'cancellation', 'delay']):
            return 'COMPETITION'
        elif any(k in keyword_str for k in ['supply chain', 'delivery', 'equipment']):
            return 'SUPPLY_CHAIN'
        else:
            return 'MARKET'
    
    def _record_audit(self, article: Dict, impact: Dict):
        """Record analysis in audit trail"""
        try:
            conn = sqlite3.connect(str(self.config.sensitivity_db_path))
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO analysis_audit
                (analysis_date, article_title, article_url, conclusion_type,
                 npv_change_claimed, lcoh_change_claimed, irr_change_claimed,
                 supporting_sensitivity_ids, supporting_documents, confidence_score, reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now().isoformat(),
                article.get('title', '')[:200],
                article.get('url', ''),
                'QUANTIFIED' if impact['has_quantifiable_impact'] else 'UNQUANTIFIED',
                impact['npv_change'],
                impact['lcoh_change'],
                impact['irr_change'],
                json.dumps([c['source'] for c in impact['citations']]),
                json.dumps(list(set(c['source'] for c in impact['citations']))),
                impact['confidence'],
                impact['reasoning'][:500]
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"  Warning: Could not record audit: {e}")
    
    def generate_impact_report(
        self,
        articles: List[Dict],
        tea_baseline: Dict,
        region: str
    ) -> Dict:
        """
        Generate comprehensive impact report with citations.
        """
        results = []
        
        print(f"\n{'='*70}")
        print(f"ANALYZING {len(articles)} ARTICLES FOR {region}")
        print(f"{'='*70}\n")
        
        for i, article in enumerate(articles, 1):
            print(f"[{i}/{len(articles)}] {article.get('title', '')[:50]}...")
            result = self.analyze_article_with_citations(article, tea_baseline)
            results.append(result)
            
            if result['is_quantified']:
                print(f"  ✓ Quantified: NPV ${result['impact']['npv_change']:+.1f}M, "
                      f"LCOH ${result['impact']['lcoh_change']:+.3f}/kg")
                print(f"    Citations: {len(result['impact']['citations'])} source(s)")
            else:
                print(f"  ○ Not quantified (insufficient data)")
        
        # Aggregate results
        total_npv = sum(r['impact']['npv_change'] for r in results)
        total_lcoh = sum(r['impact']['lcoh_change'] for r in results)
        quantified_count = sum(1 for r in results if r['is_quantified'])
        
        # Collect all citations
        all_citations = []
        for r in results:
            all_citations.extend(r['impact']['citations'])
        
        unique_sources = list(set(c['source'] for c in all_citations))
        
        report = {
            'summary': {
                'region': region,
                'articles_analyzed': len(articles),
                'articles_with_quantified_impact': quantified_count,
                'total_npv_change_usd_million': total_npv,
                'total_lcoh_change_usd_per_kg': total_lcoh,
                'baseline_npv': tea_baseline.get('npv'),
                'baseline_lcoh': tea_baseline.get('lcoh'),
                'net_npv_after_news': tea_baseline.get('npv', 0) + total_npv,
                'net_lcoh_after_news': tea_baseline.get('lcoh', 0) + total_lcoh
            },
            'citation_summary': {
                'total_citations': len(all_citations),
                'unique_source_documents': unique_sources,
                'citation_details': all_citations
            },
            'detailed_results': results,
            'report_timestamp': datetime.now().isoformat()
        }
        
        # Print summary
        print(f"\n{'='*70}")
        print(f"IMPACT SUMMARY FOR {region}")
        print(f"{'='*70}")
        print(f"\nBaseline: NPV ${tea_baseline.get('npv', 'N/A'):.1f}M | LCOH ${tea_baseline.get('lcoh', 'N/A'):.2f}/kg")
        print(f"\nNews Impact (from {quantified_count} quantified articles):")
        print(f"  NPV Change:  ${total_npv:+.1f}M ({total_npv/tea_baseline.get('npv', 1)*100:+.1f}%)")
        print(f"  LCOH Change: ${total_lcoh:+.3f}/kg ({total_lcoh/tea_baseline.get('lcoh', 1)*100:+.1f}%)")
        print(f"\nNet Position:")
        print(f"  NPV:  ${tea_baseline.get('npv', 0) + total_npv:.1f}M")
        print(f"  LCOH: ${tea_baseline.get('lcoh', 0) + total_lcoh:.2f}/kg")
        print(f"\n📚 Source Documents Used: {len(unique_sources)}")
        for source in unique_sources:
            print(f"  • {source}")
        print(f"{'='*70}\n")
        
        return report


# ============================================================================
# PIPELINE ORCHESTRATOR
# ============================================================================

class HydrogenIntelligencePipeline:
    """
    Orchestrates the full pipeline:
    1. Download documents (website_dl.py)
    2. Ingest and build knowledge base (llm_training_system.py)
    3. Analyze news with grounded citations (hydrogen_intelligence_monitor.py)
    """
    
    def __init__(self, config: IntegratedConfig = None):
        self.config = config or CONFIG
        
    def run_full_pipeline(
        self,
        region: str = 'Oman',
        days_back: int = 7,
        skip_download: bool = False,
        skip_ingest: bool = False
    ):
        """Run the complete pipeline"""
        
        print("\n" + "="*70)
        print("HYDROGEN INTELLIGENCE INTEGRATED PIPELINE")
        print("="*70)
        print(f"\nTarget Region: {region}")
        print(f"Analysis Period: Last {days_back} days")
        print(f"Timestamp: {datetime.now().isoformat()}")
        print("="*70 + "\n")
        
        # Step 0: Setup databases
        print("STEP 0: Setting up databases...")
        setup_sensitivity_database()
        
        # Step 1: Download documents
        if not skip_download:
            print("\nSTEP 1: Downloading documents...")
            self._run_download()
        else:
            print("\nSTEP 1: Skipped (--skip-download)")
        
        # Step 2: Ingest to knowledge base
        if not skip_ingest:
            print("\nSTEP 2: Ingesting documents and building knowledge base...")
            self._run_ingest()
        else:
            print("\nSTEP 2: Skipped (--skip-ingest)")
        
        # Step 3: Load TEA baseline
        print("\nSTEP 3: Loading TEA baseline...")
        tea_baseline = self._load_tea_baseline(region)
        
        # Step 4: Collect and analyze news
        print("\nSTEP 4: Collecting and analyzing news...")
        report = self._run_analysis(region, days_back, tea_baseline)
        
        # Step 5: Generate output
        print("\nSTEP 5: Generating report...")
        self._save_report(report, region)
        
        return report
    
    def _run_download(self):
        """Run website_dl.py to download documents"""
        try:
            import subprocess
            
            # Check if sites.yaml exists
            yaml_path = Path("sites.yaml")
            if not yaml_path.exists():
                print("  Creating default sites.yaml...")
                self._create_default_sites_yaml()
            
            # Run website_dl.py
            result = subprocess.run(
                [sys.executable, "website_dl.py"],
                capture_output=True,
                text=True,
                timeout=600
            )
            
            if result.returncode == 0:
                print("  ✓ Download complete")
            else:
                print(f"  ⚠ Download had issues: {result.stderr[:200]}")
                
        except FileNotFoundError:
            print("  ⚠ website_dl.py not found. Skipping download step.")
        except Exception as e:
            print(f"  ⚠ Download error: {e}")
    
    def _run_ingest(self):
        """Run document ingestion and knowledge base building"""
        try:
            # Import the ingester
            from llm_training_system import DocumentIngester, KnowledgeBase
            
            # Ingest documents
            training_folder = self.config.downloads_dir
            if not training_folder.exists():
                print(f"  ⚠ Training folder not found: {training_folder}")
                return
            
            ingester = DocumentIngester(training_folder)
            documents = ingester.ingest_all()
            
            # Build knowledge base
            kb = KnowledgeBase(str(self.config.knowledge_base_dir))
            kb.build(documents)
            
            # Extract sensitivity data
            print("\n  Extracting sensitivity analysis data...")
            kb_manager = KnowledgeBaseManager(self.config)
            extracted = kb_manager.extract_sensitivity_from_documents(documents)
            print(f"  ✓ Extracted {extracted} sensitivity factors")
            
        except ImportError as e:
            print(f"  ⚠ Could not import llm_training_system: {e}")
        except Exception as e:
            print(f"  ⚠ Ingestion error: {e}")
    
    def _load_tea_baseline(self, region: str) -> Dict:
        """Load TEA baseline for the specified region"""
        try:
            from tea_baseline_adapter import ShareRunConfig, ShareBaselineAdapter
            
            # Find the folder for this region
            folder_map = {v: k for k, v in self.config.location_map.items()}
            folder_name = folder_map.get(region)
            
            if not folder_name:
                raise ValueError(f"Unknown region: {region}")
            
            cfg = ShareRunConfig(
                locations_root=self.config.tea_locations_root,
                share_entrypoint="SHARE_Model_main_v1.py",
                baseline_output_name="baseline.json",
                share_output_hint="outputs.json",
            )
            
            adapter = ShareBaselineAdapter(cfg)
            baseline_data = adapter.get_baseline(folder_name)
            
            baseline = {
                'npv': baseline_data.get('npv', 580),
                'lcoh': baseline_data.get('lcoh_usd_per_kg', 1.90),
                'irr': baseline_data.get('irr_pct', 10),
                'capex': baseline_data.get('capex', 400),
                'lcoa': baseline_data.get('lcoa_usd_per_kg'),
            }
            
            print(f"  ✓ Loaded baseline: NPV=${baseline['npv']:.1f}M, LCOH=${baseline['lcoh']:.2f}/kg")
            return baseline
            
        except Exception as e:
            print(f"  ⚠ Could not load TEA baseline: {e}")
            print("  Using default values...")
            return {
                'npv': 580,
                'lcoh': 1.90,
                'irr': 10,
                'capex': 400
            }
    
    def _run_analysis(self, region: str, days_back: int, tea_baseline: Dict) -> Dict:
        """Run news analysis"""
        
        # Get articles from database
        conn = sqlite3.connect(str(self.config.news_db_path))
        cursor = conn.cursor()
        
        cutoff_date = (datetime.now() - timedelta(days=days_back)).isoformat()
        
        cursor.execute('''
            SELECT title, url, snippet, category, region, priority_score, keywords
            FROM articles
            WHERE fetched_date >= ? AND (region = ? OR region IS NULL OR region = '')
            ORDER BY priority_score DESC
            LIMIT ?
        ''', (cutoff_date, region, self.config.max_articles_per_analysis))
        
        rows = cursor.fetchall()
        conn.close()
        
        articles = []
        for row in rows:
            articles.append({
                'title': row[0],
                'url': row[1],
                'snippet': row[2],
                'category': row[3],
                'region': row[4] or region,
                'priority_score': row[5],
                'keywords': row[6]
            })
        
        if not articles:
            print(f"  No articles found for {region} in the last {days_back} days")
            return {'summary': {}, 'detailed_results': []}
        
        # Run analysis
        analyzer = IntegratedNewsAnalyzer(self.config)
        report = analyzer.generate_impact_report(articles, tea_baseline, region)
        
        return report
    
    def _save_report(self, report: Dict, region: str):
        """Save report to file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"impact_report_{region}_{timestamp}.json"
        
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        
        print(f"  ✓ Report saved to: {filename}")
    
    def _create_default_sites_yaml(self):
        """Create a minimal sites.yaml for testing"""
        yaml_content = """
keywords:
  - hydrogen
  - electrolyzer
  - LCOH
  - green ammonia

output_dir: ./downloads

sites:
  - name: IEA Hydrogen Reports
    type: generic_scraper
    start_urls:
      - https://www.iea.org/reports?topic=hydrogen
"""
        with open("sites.yaml", 'w') as f:
            f.write(yaml_content)


# ============================================================================
# INTERACTIVE MODE
# ============================================================================

def interactive_mode():
    """Interactive menu for the integrated system"""
    
    config = IntegratedConfig()
    pipeline = HydrogenIntelligencePipeline(config)
    
    while True:
        print("\n" + "="*70)
        print("HYDROGEN INTELLIGENCE INTEGRATED SYSTEM")
        print("="*70)
        print("\n1. Run Full Pipeline (Download → Ingest → Analyze)")
        print("2. Download Documents Only")
        print("3. Ingest Documents Only")
        print("4. Analyze News Only")
        print("5. View Sensitivity Data Summary")
        print("6. View Analysis Audit Trail")
        print("7. Search Knowledge Base")
        print("8. Exit")
        print("="*70)
        
        choice = input("\nEnter choice (1-8): ").strip()
        
        if choice == '1':
            region = input("Region (Oman/Chile/Houston) [Oman]: ").strip() or 'Oman'
            days = int(input("Days back [7]: ").strip() or '7')
            pipeline.run_full_pipeline(region=region, days_back=days)
            
        elif choice == '2':
            pipeline._run_download()
            
        elif choice == '3':
            pipeline._run_ingest()
            
        elif choice == '4':
            region = input("Region (Oman/Chile/Houston) [Oman]: ").strip() or 'Oman'
            days = int(input("Days back [7]: ").strip() or '7')
            tea_baseline = pipeline._load_tea_baseline(region)
            pipeline._run_analysis(region, days, tea_baseline)
            
        elif choice == '5':
            analyzer = GroundedImpactAnalyzer(config)
            summary = analyzer.get_available_sensitivities()
            print("\n📊 Available Sensitivity Data:")
            for param, data in summary.items():
                print(f"\n  {param}:")
                print(f"    Data points: {data['num_data_points']}")
                print(f"    Sources: {', '.join(data['sources'])}")
                print(f"    Regions: {', '.join(data['regions']) if data['regions'] else 'Global'}")
            
        elif choice == '6':
            conn = sqlite3.connect(str(config.sensitivity_db_path))
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM analysis_audit ORDER BY analysis_date DESC LIMIT 20')
            rows = cursor.fetchall()
            conn.close()
            
            print("\n📋 Recent Analysis Audit Trail:")
            for row in rows:
                print(f"\n  [{row[1]}] {row[2][:50]}...")
                print(f"    Type: {row[4]} | NPV: ${row[5]:+.1f}M | LCOH: ${row[6]:+.3f}/kg")
                print(f"    Confidence: {row[10]:.0%}")
            
        elif choice == '7':
            query = input("Search query: ").strip()
            if query:
                try:
                    from llm_training_system import KnowledgeBase
                    kb = KnowledgeBase(str(config.knowledge_base_dir))
                    results = kb.search(query, top_k=5)
                    
                    print(f"\n🔍 Search Results for: {query}")
                    for i, doc in enumerate(results, 1):
                        print(f"\n  {i}. [{doc['metadata']['source']}]")
                        print(f"     {doc['text'][:200]}...")
                        print(f"     Similarity: {doc['similarity']:.0%}")
                except ImportError:
                    print("  ⚠ Knowledge base not available")
            
        elif choice == '8':
            print("\nGoodbye!")
            break
        
        else:
            print("Invalid choice")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Integrated Hydrogen Intelligence System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Full pipeline
    python hydrogen_integrated_system.py --mode full --region Oman --days 7
    
    # Download only
    python hydrogen_integrated_system.py --mode download
    
    # Analyze only (uses existing knowledge base)
    python hydrogen_integrated_system.py --mode analyze --region Chile --days 14
    
    # Interactive mode
    python hydrogen_integrated_system.py --mode interactive
        """
    )
    
    parser.add_argument('--mode', required=True,
                       choices=['full', 'download', 'ingest', 'analyze', 'interactive'],
                       help='Operation mode')
    
    parser.add_argument('--region', default='Oman',
                       choices=['Oman', 'Chile', 'Houston'],
                       help='Target region for analysis')
    
    parser.add_argument('--days', type=int, default=7,
                       help='Days back for news analysis')
    
    parser.add_argument('--skip-download', action='store_true',
                       help='Skip document download step')
    
    parser.add_argument('--skip-ingest', action='store_true',
                       help='Skip document ingestion step')
    
    parser.add_argument('--output', help='Output file for results')
    
    args = parser.parse_args()
    
    if args.mode == 'interactive':
        interactive_mode()
        return
    
    config = IntegratedConfig()
    pipeline = HydrogenIntelligencePipeline(config)
    
    if args.mode == 'full':
        report = pipeline.run_full_pipeline(
            region=args.region,
            days_back=args.days,
            skip_download=args.skip_download,
            skip_ingest=args.skip_ingest
        )
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(report, f, indent=2, default=str)
            print(f"\n✅ Report saved to: {args.output}")
    
    elif args.mode == 'download':
        pipeline._run_download()
    
    elif args.mode == 'ingest':
        pipeline._run_ingest()
    
    elif args.mode == 'analyze':
        setup_sensitivity_database()
        tea_baseline = pipeline._load_tea_baseline(args.region)
        report = pipeline._run_analysis(args.region, args.days, tea_baseline)
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(report, f, indent=2, default=str)


if __name__ == "__main__":
    main()
