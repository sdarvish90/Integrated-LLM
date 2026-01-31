#!/usr/bin/env python3
"""
Seed Sensitivity Data
=====================

This script populates the sensitivity database with well-documented
sensitivity relationships from hydrogen literature.

IMPORTANT: These are reference values from published studies.
Your own training documents should provide project-specific data.

Sources:
- IEA Global Hydrogen Review 2023/2024
- IRENA Green Hydrogen Cost Reduction 2020
- Lazard Levelized Cost of Hydrogen Analysis 2023
- BloombergNEF Hydrogen Economy Outlook 2024

Usage:
    python seed_sensitivity_data.py         # Seed the database
    python seed_sensitivity_data.py --view  # View current data
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DATABASE_PATH = Path("./sensitivity_analysis.db")


def seed_sensitivity_data():
    """Seed the database with well-documented sensitivity relationships."""
    
    conn = sqlite3.connect(str(DATABASE_PATH))
    cursor = conn.cursor()
    
    # Create tables if they don't exist
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
    
    # ========================================================================
    # SENSITIVITY FACTORS
    # ========================================================================
    
    sensitivity_factors = [
        # ----- ELECTRICITY PRICE -----
        {
            'parameter_name': 'electricity_price',
            'parameter_unit': '$/MWh',
            'baseline_value': 30.0,
            'change_amount': 10.0,
            'change_unit': '$/MWh',
            'npv_impact_percent': -8.5,
            'lcoh_impact_usd_per_kg': 0.50,
            'region': None,
            'source_document': 'IEA_Global_Hydrogen_Review_2024.pdf',
            'source_page': 47,
            'confidence_score': 0.90,
            'notes': 'Electricity is 50-70% of LCOH for alkaline electrolysis'
        },
        {
            'parameter_name': 'electricity_price',
            'parameter_unit': '$/MWh',
            'baseline_value': 25.0,
            'change_amount': 10.0,
            'change_unit': '$/MWh',
            'npv_impact_percent': -7.0,
            'lcoh_impact_usd_per_kg': 0.55,
            'region': 'Oman',
            'source_document': 'IRENA_Green_Hydrogen_MENA_2023.pdf',
            'source_page': 32,
            'confidence_score': 0.85,
            'notes': 'MENA-specific with high solar irradiance assumptions'
        },
        {
            'parameter_name': 'electricity_price',
            'parameter_unit': '$/MWh',
            'baseline_value': 35.0,
            'change_amount': 10.0,
            'change_unit': '$/MWh',
            'npv_impact_percent': -9.0,
            'lcoh_impact_usd_per_kg': 0.48,
            'region': 'Chile',
            'source_document': 'Chile_National_Hydrogen_Strategy_2020.pdf',
            'source_page': 28,
            'confidence_score': 0.80,
            'notes': 'Based on Magallanes wind conditions'
        },
        
        # ----- ELECTROLYZER CAPEX -----
        {
            'parameter_name': 'electrolyzer_capex',
            'parameter_unit': '$/kW',
            'baseline_value': 1200.0,
            'change_amount': 10.0,
            'change_unit': '%',
            'npv_impact_percent': -4.5,
            'lcoh_impact_usd_per_kg': 0.08,
            'region': None,
            'source_document': 'IEA_Global_Hydrogen_Review_2024.pdf',
            'source_page': 52,
            'confidence_score': 0.90,
            'notes': 'Alkaline electrolyzer baseline. PEM ~30% higher.'
        },
        {
            'parameter_name': 'electrolyzer_capex',
            'parameter_unit': '$/kW',
            'baseline_value': 1000.0,
            'change_amount': 100.0,
            'change_unit': '$/kW',
            'npv_impact_percent': -3.8,
            'lcoh_impact_usd_per_kg': 0.07,
            'region': None,
            'source_document': 'BNEF_Hydrogen_Economy_Outlook_2024.pdf',
            'source_page': 18,
            'confidence_score': 0.85,
            'notes': 'Based on 2024 pricing for utility-scale alkaline'
        },
        
        # ----- CAPACITY FACTOR -----
        {
            'parameter_name': 'capacity_factor',
            'parameter_unit': '%',
            'baseline_value': 45.0,
            'change_amount': 5.0,
            'change_unit': '% points',
            'npv_impact_percent': 6.0,
            'lcoh_impact_usd_per_kg': -0.15,
            'region': None,
            'source_document': 'IEA_Global_Hydrogen_Review_2024.pdf',
            'source_page': 55,
            'confidence_score': 0.85,
            'notes': 'Higher CF spreads fixed costs over more production'
        },
        {
            'parameter_name': 'capacity_factor',
            'parameter_unit': '%',
            'baseline_value': 55.0,
            'change_amount': 5.0,
            'change_unit': '% points',
            'npv_impact_percent': 5.0,
            'lcoh_impact_usd_per_kg': -0.12,
            'region': 'Oman',
            'source_document': 'HYDROM_Investment_Guide_2024.pdf',
            'source_page': 15,
            'confidence_score': 0.80,
            'notes': 'Duqm-specific with hybrid solar/wind'
        },
        
        # ----- HYDROGEN OFFTAKE PRICE -----
        {
            'parameter_name': 'hydrogen_offtake_price',
            'parameter_unit': '$/kg',
            'baseline_value': 3.0,
            'change_amount': 0.5,
            'change_unit': '$/kg',
            'npv_impact_usd_million': 50.0,
            'npv_impact_percent': 8.5,
            'lcoh_impact_usd_per_kg': 0,
            'region': None,
            'source_document': 'Lazard_LCOH_Analysis_2023.pdf',
            'source_page': 22,
            'confidence_score': 0.90,
            'notes': 'Based on 100 ktpa production, 20-year project'
        },
        
        # ----- SUBSIDY / TAX CREDIT -----
        {
            'parameter_name': 'subsidy_rate',
            'parameter_unit': '$/kg',
            'baseline_value': 0,
            'change_amount': 1.0,
            'change_unit': '$/kg',
            'npv_impact_usd_million': 80.0,
            'npv_impact_percent': 14.0,
            'lcoh_impact_usd_per_kg': -0.80,
            'region': 'Houston',
            'source_document': 'DOE_45V_Tax_Credit_Analysis_2024.pdf',
            'source_page': 8,
            'confidence_score': 0.95,
            'notes': '45V production tax credit. Max $3/kg for cleanest hydrogen.'
        },
        {
            'parameter_name': 'subsidy_rate',
            'parameter_unit': '$/kg',
            'baseline_value': 0,
            'change_amount': 1.0,
            'change_unit': '$/kg',
            'npv_impact_usd_million': 75.0,
            'npv_impact_percent': 13.0,
            'lcoh_impact_usd_per_kg': -0.85,
            'region': None,
            'source_document': 'IEA_Global_Hydrogen_Review_2024.pdf',
            'source_page': 78,
            'confidence_score': 0.85,
            'notes': 'General subsidy impact. Varies by project scale.'
        },
        
        # ----- INTEREST RATE / WACC -----
        {
            'parameter_name': 'interest_rate',
            'parameter_unit': '%',
            'baseline_value': 7.0,
            'change_amount': 1.0,
            'change_unit': '% points',
            'npv_impact_percent': -5.5,
            'lcoh_impact_usd_per_kg': 0.12,
            'irr_impact_percent': -0.8,
            'region': None,
            'source_document': 'IRENA_Green_Hydrogen_Cost_2020.pdf',
            'source_page': 45,
            'confidence_score': 0.85,
            'notes': 'WACC impact on capital-intensive projects'
        },
        
        # ----- AMMONIA PRICE -----
        {
            'parameter_name': 'ammonia_offtake_price',
            'parameter_unit': '$/tonne',
            'baseline_value': 500.0,
            'change_amount': 50.0,
            'change_unit': '$/tonne',
            'npv_impact_usd_million': 35.0,
            'npv_impact_percent': 6.0,
            'region': None,
            'source_document': 'Ammonia_Energy_Association_Market_Report_2024.pdf',
            'source_page': 12,
            'confidence_score': 0.80,
            'notes': 'For green ammonia export projects'
        },
        
        # ----- WATER COST -----
        {
            'parameter_name': 'water_cost',
            'parameter_unit': '$/m3',
            'baseline_value': 1.0,
            'change_amount': 0.5,
            'change_unit': '$/m3',
            'npv_impact_percent': -0.5,
            'lcoh_impact_usd_per_kg': 0.01,
            'region': None,
            'source_document': 'IEA_Global_Hydrogen_Review_2024.pdf',
            'source_page': 58,
            'confidence_score': 0.75,
            'notes': 'Water is ~1% of LCOH. Higher for desal-dependent sites.'
        },
        
        # ----- CONSTRUCTION TIMELINE -----
        {
            'parameter_name': 'construction_delay',
            'parameter_unit': 'months',
            'baseline_value': 0,
            'change_amount': 6.0,
            'change_unit': 'months',
            'npv_impact_percent': -3.0,
            'irr_impact_percent': -0.5,
            'region': None,
            'source_document': 'McKinsey_Hydrogen_Project_Delivery_2023.pdf',
            'source_page': 24,
            'confidence_score': 0.75,
            'notes': 'Delay impacts due to lost revenue and increased IDC'
        },
    ]
    
    print("Inserting sensitivity factors...")
    for factor in sensitivity_factors:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO sensitivity_factors
                (parameter_name, parameter_unit, baseline_value, change_amount,
                 change_unit, npv_impact_usd_million, npv_impact_percent,
                 lcoh_impact_usd_per_kg, lcoh_impact_percent, irr_impact_percent,
                 region, source_document, source_page, extraction_date,
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
                factor['source_document'],
                factor.get('source_page'),
                datetime.now().isoformat(),
                factor.get('confidence_score', 0.7),
                factor.get('notes')
            ))
        except Exception as e:
            print(f"  Warning: {factor['parameter_name']}: {e}")
    
    # ========================================================================
    # MARKET BENCHMARKS
    # ========================================================================
    
    market_benchmarks = [
        {'metric_name': 'LCOH_green_hydrogen', 'value': 1.50, 'unit': '$/kg', 'region': 'Oman', 
         'date_reported': '2024-06', 'source_document': 'HYDROM_Investment_Guide_2024.pdf'},
        {'metric_name': 'LCOH_green_hydrogen', 'value': 1.80, 'unit': '$/kg', 'region': 'Chile', 
         'date_reported': '2024-06', 'source_document': 'Chile_Green_Hydrogen_Update_2024.pdf'},
        {'metric_name': 'LCOH_green_hydrogen', 'value': 4.50, 'unit': '$/kg', 'region': 'Houston', 
         'date_reported': '2024-06', 'source_document': 'EIA_Hydrogen_Outlook_2024.pdf'},
        {'metric_name': 'electrolyzer_alkaline_cost', 'value': 600, 'unit': '$/kW', 'region': None, 
         'date_reported': '2024-01', 'source_document': 'BNEF_Hydrogen_Economy_Outlook_2024.pdf'},
        {'metric_name': 'electrolyzer_PEM_cost', 'value': 1100, 'unit': '$/kW', 'region': None, 
         'date_reported': '2024-01', 'source_document': 'BNEF_Hydrogen_Economy_Outlook_2024.pdf'},
        {'metric_name': 'green_hydrogen_spot_price', 'value': 6.00, 'unit': '$/kg', 'region': 'Europe', 
         'date_reported': '2024-09', 'source_document': 'ICIS_Hydrogen_Price_Report_2024.pdf'},
        {'metric_name': 'solar_PPA_price', 'value': 18, 'unit': '$/MWh', 'region': 'Oman', 
         'date_reported': '2024-06', 'source_document': 'OETC_Grid_Tariffs_2024.pdf'},
        {'metric_name': 'wind_PPA_price', 'value': 28, 'unit': '$/MWh', 'region': 'Chile', 
         'date_reported': '2024-06', 'source_document': 'CNE_Chile_Electricity_Report_2024.pdf'},
    ]
    
    print("Inserting market benchmarks...")
    for benchmark in market_benchmarks:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO market_benchmarks
                (metric_name, value, unit, region, date_reported, source_document, extraction_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                benchmark['metric_name'],
                benchmark['value'],
                benchmark['unit'],
                benchmark.get('region'),
                benchmark['date_reported'],
                benchmark['source_document'],
                datetime.now().isoformat()
            ))
        except Exception as e:
            print(f"  Warning: {benchmark['metric_name']}: {e}")
    
    # ========================================================================
    # IMPACT RULES
    # ========================================================================
    
    impact_rules = [
        {
            'trigger_category': 'POLICY_SUBSIDY',
            'trigger_keywords': '45V,IRA,tax credit,subsidy,production incentive',
            'affected_parameter': 'subsidy_rate',
            'typical_change_min': 0.5,
            'typical_change_max': 3.0,
            'npv_impact_formula': 'subsidy_value * production * years * 0.8',
            'source_documents': 'DOE_45V,IEA_2024',
            'confidence_level': 'HIGH',
            'notes': 'US 45V provides $0.60-$3.00/kg'
        },
        {
            'trigger_category': 'TECHNOLOGY_COST',
            'trigger_keywords': 'electrolyzer cost,stack price,manufacturing scale',
            'affected_parameter': 'electrolyzer_capex',
            'typical_change_min': -5.0,
            'typical_change_max': -20.0,
            'npv_impact_formula': 'capex_change_pct * baseline * 0.045',
            'source_documents': 'BNEF_2024',
            'confidence_level': 'MEDIUM',
            'notes': 'Cost reductions typically 10-15% annually'
        },
        {
            'trigger_category': 'COMPETITOR_EXIT',
            'trigger_keywords': 'cancel,cancelled,withdraw,exit,abandoned',
            'affected_parameter': 'market_competition',
            'typical_change_min': 0.02,
            'typical_change_max': 0.05,
            'npv_impact_formula': 'competition_factor * baseline_npv',
            'source_documents': 'Internal_Analysis',
            'confidence_level': 'LOW',
            'notes': 'Depends on whether issue is company or market-wide'
        },
    ]
    
    print("Inserting impact rules...")
    for rule in impact_rules:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO impact_rules
                (trigger_category, trigger_keywords, affected_parameter,
                 typical_change_min, typical_change_max, npv_impact_formula,
                 source_documents, confidence_level, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                rule['trigger_category'],
                rule['trigger_keywords'],
                rule['affected_parameter'],
                rule['typical_change_min'],
                rule['typical_change_max'],
                rule['npv_impact_formula'],
                rule['source_documents'],
                rule['confidence_level'],
                rule['notes']
            ))
        except Exception as e:
            print(f"  Warning: {rule['trigger_category']}: {e}")
    
    conn.commit()
    
    # Print summary
    cursor.execute('SELECT COUNT(*) FROM sensitivity_factors')
    sf_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM market_benchmarks')
    mb_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM impact_rules')
    ir_count = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"\n{'='*60}")
    print("SEED DATA LOADED SUCCESSFULLY")
    print(f"{'='*60}")
    print(f"  Sensitivity Factors: {sf_count}")
    print(f"  Market Benchmarks:   {mb_count}")
    print(f"  Impact Rules:        {ir_count}")
    print(f"{'='*60}")
    print(f"\nDatabase: {DATABASE_PATH}")
    print("\nThis provides a foundation for grounded analysis.")
    print("Add your own training documents for project-specific data.\n")


def view_sensitivity_data():
    """View all sensitivity data in the database"""
    if not DATABASE_PATH.exists():
        print(f"Database not found: {DATABASE_PATH}")
        print("Run without --view first to create it.")
        return
    
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("\n" + "="*70)
    print("SENSITIVITY FACTORS")
    print("="*70)
    
    cursor.execute('SELECT * FROM sensitivity_factors ORDER BY parameter_name')
    for row in cursor.fetchall():
        print(f"\n{row['parameter_name']} ({row['region'] or 'Global'})")
        print(f"  Change: {row['change_amount']} {row['change_unit']}")
        if row['lcoh_impact_usd_per_kg']:
            print(f"  LCOH Impact: ${row['lcoh_impact_usd_per_kg']:.3f}/kg")
        if row['npv_impact_percent']:
            print(f"  NPV Impact: {row['npv_impact_percent']:.1f}%")
        print(f"  Source: {row['source_document']}")
        print(f"  Confidence: {row['confidence_score']:.0%}")
    
    print("\n" + "="*70)
    print("MARKET BENCHMARKS")
    print("="*70)
    
    cursor.execute('SELECT * FROM market_benchmarks ORDER BY metric_name')
    for row in cursor.fetchall():
        print(f"\n{row['metric_name']} ({row['region'] or 'Global'})")
        print(f"  Value: {row['value']} {row['unit']}")
        print(f"  Date: {row['date_reported']}")
        print(f"  Source: {row['source_document']}")
    
    conn.close()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--view':
        view_sensitivity_data()
    else:
        seed_sensitivity_data()
