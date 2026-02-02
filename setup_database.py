#!/usr/bin/env python3
"""
Setup script - Creates all database tables
Run this BEFORE using the hydrogen monitor
"""

import sqlite3
from datetime import datetime

DATABASE_NAME = "hydrogen_intelligence_v3.db"

def setup_database():
    """Create all database tables"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print("\n" + "="*70)
    print("SETTING UP HYDROGEN MONITOR DATABASE")
    print("="*70 + "\n")
    
    # Articles table
    print("Creating 'articles' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            url_hash TEXT,
            source TEXT,
            snippet TEXT,
            full_text TEXT,
            category TEXT,
            priority_score INTEGER,
            published_date TEXT,
            fetched_date TEXT,
            alerted BOOLEAN DEFAULT FALSE,
            keywords TEXT,
            region TEXT,
            investigated BOOLEAN DEFAULT FALSE
        )
    ''')
    
    # Investigations table
    print("Creating 'investigations' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS investigations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER,
            investigation_date TEXT,
            key_findings TEXT,
            impact_assessment TEXT,
            action_items TEXT,
            FOREIGN KEY (article_id) REFERENCES articles (id)
        )
    ''')
    
    # Competitive projects tracking
    print("Creating 'competitors' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS competitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            company TEXT,
            region TEXT,
            capacity_mw REAL,
            status TEXT,
            fid_date TEXT,
            cod_date TEXT,
            offtake_status TEXT,
            land_status TEXT,
            last_updated TEXT,
            source_article_id INTEGER,
            notes TEXT,
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    # Offtaker intelligence
    print("Creating 'offtakers' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS offtakers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            type TEXT,
            region TEXT,
            total_demand_kt REAL,
            secured_kt REAL,
            available_kt REAL,
            last_activity TEXT,
            last_updated TEXT,
            source_article_id INTEGER,
            notes TEXT,
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    # Risk register
    print("Creating 'risks' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS risks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            risk_category TEXT NOT NULL,
            risk_description TEXT,
            status TEXT,
            trend TEXT,
            impact_level TEXT,
            probability TEXT,
            last_updated TEXT,
            mitigation_actions TEXT,
            source_article_id INTEGER,
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    # Deal flow tracking
    print("Creating 'deals' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_date TEXT,
            deal_type TEXT,
            parties TEXT,
            value_usd REAL,
            region TEXT,
            description TEXT,
            signal TEXT,
            source_article_id INTEGER,
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    # Peer benchmarks
    print("Creating 'benchmarks' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS benchmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT,
            region TEXT,
            capacity_mw REAL,
            lcoh_usd_per_kg REAL,
            npv_million_usd REAL,
            timeline_months INTEGER,
            offtake_secured_pct REAL,
            electrolyzer_cost_per_kw REAL,
            power_cost_per_kwh REAL,
            status TEXT,
            last_updated TEXT,
            source_article_id INTEGER,
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    # User project configuration
    print("Creating 'user_project' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_project (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT,
            region TEXT,
            capacity_mw REAL,
            target_fid_date TEXT,
            target_cod_date TEXT,
            lcoh_assumption REAL,
            npv_baseline REAL,
            electrolyzer_cost_assumption REAL,
            power_cost_assumption REAL,
            offtake_status TEXT,
            last_updated TEXT
        )
    ''')
    
    # Create indexes for better performance
    print("Creating indexes...")
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_url_hash ON articles(url_hash)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON articles(category)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_published_date ON articles(published_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_competitors_region ON competitors(region)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_offtakers_region ON offtakers(region)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_risks_category ON risks(risk_category)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_deals_date ON deals(deal_date)')
    
    conn.commit()
    
    # Verify
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    
    print("\n" + "="*70)
    print(f"✅ DATABASE SETUP COMPLETE!")
    print("="*70)
    print(f"\nCreated {len(tables)} tables:")
    for table in tables:
        print(f"  ✓ {table}")
    
    print(f"\nDatabase file: {DATABASE_NAME}")
    print("\nYou can now run the hydrogen intelligence monitor!\n")
    
    conn.close()

if __name__ == "__main__":
    setup_database()