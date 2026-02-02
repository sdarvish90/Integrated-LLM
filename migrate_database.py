#!/usr/bin/env python3
"""
Database Migration - Add Unique Constraints
============================================

This script:
1. Adds UNIQUE constraints to prevent duplicate entries
2. Removes existing duplicates (keeps most recent)
3. Creates indexes for better performance

Run this ONCE after updating to the new integrated system.

Usage:
    python migrate_database.py
"""

import sqlite3
from datetime import datetime
from pathlib import Path


def migrate_hydrogen_monitor_db(db_path: str = "hydrogen_intelligence_v3.db"):
    """
    Migrate hydrogen_intelligence_v3.db to add unique constraints
    """
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print(f"\n{'='*60}")
    print(f"MIGRATING DATABASE: {db_path}")
    print(f"{'='*60}\n")
    
    # =========================================================================
    # 1. COMPETITORS TABLE - unique on (project_name, company, region)
    # =========================================================================
    print("1. Fixing competitors table...")
    
    # Check for duplicates
    cursor.execute('''
        SELECT project_name, company, region, COUNT(*) as cnt
        FROM competitors
        GROUP BY project_name, company, region
        HAVING cnt > 1
    ''')
    dupes = cursor.fetchall()
    
    if dupes:
        print(f"   Found {len(dupes)} duplicate competitor entries")
        
        # Keep only the most recent entry for each duplicate
        for project_name, company, region, cnt in dupes:
            cursor.execute('''
                DELETE FROM competitors
                WHERE id NOT IN (
                    SELECT MAX(id) FROM competitors
                    WHERE project_name = ? AND company = ? AND region = ?
                )
                AND project_name = ? AND company = ? AND region = ?
            ''', (project_name, company, region, project_name, company, region))
        
        print(f"   Removed duplicate entries")
    
    # Create new table with unique constraint
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS competitors_new (
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
            UNIQUE(project_name, company, region),
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    # Copy data
    cursor.execute('''
        INSERT OR IGNORE INTO competitors_new 
        SELECT * FROM competitors
    ''')
    
    # Swap tables
    cursor.execute('DROP TABLE competitors')
    cursor.execute('ALTER TABLE competitors_new RENAME TO competitors')
    print("   ✓ Added UNIQUE(project_name, company, region)")
    
    # =========================================================================
    # 2. OFFTAKERS TABLE - unique on (company_name, region)
    # =========================================================================
    print("\n2. Fixing offtakers table...")
    
    cursor.execute('''
        SELECT company_name, region, COUNT(*) as cnt
        FROM offtakers
        GROUP BY company_name, region
        HAVING cnt > 1
    ''')
    dupes = cursor.fetchall()
    
    if dupes:
        print(f"   Found {len(dupes)} duplicate offtaker entries")
        for company_name, region, cnt in dupes:
            cursor.execute('''
                DELETE FROM offtakers
                WHERE id NOT IN (
                    SELECT MAX(id) FROM offtakers
                    WHERE company_name = ? AND region = ?
                )
                AND company_name = ? AND region = ?
            ''', (company_name, region, company_name, region))
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS offtakers_new (
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
            UNIQUE(company_name, region),
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    cursor.execute('INSERT OR IGNORE INTO offtakers_new SELECT * FROM offtakers')
    cursor.execute('DROP TABLE offtakers')
    cursor.execute('ALTER TABLE offtakers_new RENAME TO offtakers')
    print("   ✓ Added UNIQUE(company_name, region)")
    
    # =========================================================================
    # 3. RISKS TABLE - unique on (risk_category, risk_description)
    # =========================================================================
    print("\n3. Fixing risks table...")
    
    cursor.execute('''
        SELECT risk_category, risk_description, COUNT(*) as cnt
        FROM risks
        GROUP BY risk_category, risk_description
        HAVING cnt > 1
    ''')
    dupes = cursor.fetchall()
    
    if dupes:
        print(f"   Found {len(dupes)} duplicate risk entries")
        for risk_category, risk_description, cnt in dupes:
            cursor.execute('''
                DELETE FROM risks
                WHERE id NOT IN (
                    SELECT MAX(id) FROM risks
                    WHERE risk_category = ? AND risk_description = ?
                )
                AND risk_category = ? AND risk_description = ?
            ''', (risk_category, risk_description, risk_category, risk_description))
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS risks_new (
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
            UNIQUE(risk_category, risk_description),
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    cursor.execute('INSERT OR IGNORE INTO risks_new SELECT * FROM risks')
    cursor.execute('DROP TABLE risks')
    cursor.execute('ALTER TABLE risks_new RENAME TO risks')
    print("   ✓ Added UNIQUE(risk_category, risk_description)")
    
    # =========================================================================
    # 4. DEALS TABLE - unique on (deal_date, parties, deal_type)
    # =========================================================================
    print("\n4. Fixing deals table...")
    
    cursor.execute('''
        SELECT deal_date, parties, deal_type, COUNT(*) as cnt
        FROM deals
        GROUP BY deal_date, parties, deal_type
        HAVING cnt > 1
    ''')
    dupes = cursor.fetchall()
    
    if dupes:
        print(f"   Found {len(dupes)} duplicate deal entries")
        for deal_date, parties, deal_type, cnt in dupes:
            cursor.execute('''
                DELETE FROM deals
                WHERE id NOT IN (
                    SELECT MAX(id) FROM deals
                    WHERE deal_date = ? AND parties = ? AND deal_type = ?
                )
                AND deal_date = ? AND parties = ? AND deal_type = ?
            ''', (deal_date, parties, deal_type, deal_date, parties, deal_type))
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deals_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_date TEXT,
            deal_type TEXT,
            parties TEXT,
            value_usd REAL,
            region TEXT,
            description TEXT,
            signal TEXT,
            source_article_id INTEGER,
            UNIQUE(deal_date, parties, deal_type),
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    cursor.execute('INSERT OR IGNORE INTO deals_new SELECT * FROM deals')
    cursor.execute('DROP TABLE deals')
    cursor.execute('ALTER TABLE deals_new RENAME TO deals')
    print("   ✓ Added UNIQUE(deal_date, parties, deal_type)")
    
    # =========================================================================
    # 5. BENCHMARKS TABLE - unique on (project_name, region)
    # =========================================================================
    print("\n5. Fixing benchmarks table...")
    
    cursor.execute('''
        SELECT project_name, region, COUNT(*) as cnt
        FROM benchmarks
        GROUP BY project_name, region
        HAVING cnt > 1
    ''')
    dupes = cursor.fetchall()
    
    if dupes:
        print(f"   Found {len(dupes)} duplicate benchmark entries")
        for project_name, region, cnt in dupes:
            cursor.execute('''
                DELETE FROM benchmarks
                WHERE id NOT IN (
                    SELECT MAX(id) FROM benchmarks
                    WHERE project_name = ? AND region = ?
                )
                AND project_name = ? AND region = ?
            ''', (project_name, region, project_name, region))
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS benchmarks_new (
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
            UNIQUE(project_name, region),
            FOREIGN KEY (source_article_id) REFERENCES articles (id)
        )
    ''')
    
    cursor.execute('INSERT OR IGNORE INTO benchmarks_new SELECT * FROM benchmarks')
    cursor.execute('DROP TABLE benchmarks')
    cursor.execute('ALTER TABLE benchmarks_new RENAME TO benchmarks')
    print("   ✓ Added UNIQUE(project_name, region)")
    
    # =========================================================================
    # 6. Create additional indexes
    # =========================================================================
    print("\n6. Creating indexes...")
    
    indexes = [
        ('idx_competitors_project', 'competitors', 'project_name'),
        ('idx_competitors_company', 'competitors', 'company'),
        ('idx_offtakers_company', 'offtakers', 'company_name'),
        ('idx_risks_status', 'risks', 'status'),
        ('idx_deals_parties', 'deals', 'parties'),
        ('idx_benchmarks_project', 'benchmarks', 'project_name'),
    ]
    
    for idx_name, table, column in indexes:
        try:
            cursor.execute(f'CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({column})')
            print(f"   ✓ Created index {idx_name}")
        except Exception as e:
            print(f"   ⚠ Index {idx_name}: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\n{'='*60}")
    print("✅ MIGRATION COMPLETE")
    print(f"{'='*60}")
    print("\nYour database now has proper unique constraints.")
    print("Duplicate entries will be automatically prevented.\n")


def check_database_status(db_path: str = "hydrogen_intelligence_v3.db"):
    """Check current database status"""
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print(f"\n{'='*60}")
    print(f"DATABASE STATUS: {db_path}")
    print(f"{'='*60}\n")
    
    tables = ['articles', 'competitors', 'offtakers', 'risks', 'deals', 'benchmarks']
    
    for table in tables:
        try:
            cursor.execute(f'SELECT COUNT(*) FROM {table}')
            count = cursor.fetchone()[0]
            print(f"  {table}: {count} rows")
        except:
            print(f"  {table}: (table not found)")
    
    conn.close()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--status':
        check_database_status()
    else:
        check_database_status()
        
        print("\nProceed with migration? (y/n): ", end="")
        response = input().strip().lower()
        
        if response == 'y':
            migrate_hydrogen_monitor_db()
        else:
            print("Migration cancelled.")
