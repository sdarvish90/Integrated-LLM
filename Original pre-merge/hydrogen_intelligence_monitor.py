#!/usr/bin/env python3
"""
Enhanced Hydrogen Intelligence Monitoring System v2.0
- Deep web search (10 pages, not just recent Google)
- 50+ articles per update (configurable)
- Multiple news aggregators (RSS + web scraping)
- Improved deduplication
- Historical trend analysis
"""

import os
import ssl
import certifi

# Fix SSL certificate verification on macOS
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

import feedparser
import sqlite3
import schedule
import time
import smtplib
import requests
import json
import urllib.request
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urljoin, urlparse
import re
import hashlib

# Configure SSL context globally
ssl_context = ssl.create_default_context(cafile=certifi.where())
urllib.request.install_opener(
    urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl_context)
    )
)

# TEA baseline integration
from pathlib import Path
from tea_baseline_adapter import ShareRunConfig, ShareBaselineAdapter

def load_or_run_tea_baseline(location: str):
    cfg = ShareRunConfig(
        locations_root=Path("/Users/Shadi/Dropbox/SHARE_Model_LLM"),
        share_entrypoint="SHARE_Model_main_v1.py",
        baseline_output_name="baseline.json",
        share_output_hint="outputs.json",
    )
    tea = ShareBaselineAdapter(cfg)
    return tea.get_baseline(location)

def get_tea_baselines():
    """Load TEA baselines for all available locations."""
    default_baselines = {
        'Oman': {'npv': 580, 'lcoh': 1.90, 'timeline': 48, 'capex': 400, 'competitors': 7},
        'Chile': {'npv': 380, 'lcoh': 2.25, 'timeline': 54, 'capex': 450, 'competitors': 12},
        'Houston': {'npv': 120, 'lcoh': 2.30, 'timeline': 36, 'capex': 350, 'competitors': 15}
    }

    location_map = {
        'Duqm_Oman': 'Oman',
        'Magallines_Chile': 'Chile',
        'Houston_USA': 'Houston'
    }

    baselines = default_baselines.copy()

    for location_folder, region_name in location_map.items():
        try:
            tea = load_or_run_tea_baseline(location_folder)
            if tea:
                baselines[region_name] = {
                    'npv': tea.get('npv') if tea.get('npv') is not None else default_baselines[region_name]['npv'],
                    'lcoh': tea.get('lcoh_usd_per_kg') if tea.get('lcoh_usd_per_kg') is not None else default_baselines[region_name]['lcoh'],
                    'capex': tea.get('capex') if tea.get('capex') is not None else default_baselines[region_name]['capex'],
                    'timeline': default_baselines[region_name]['timeline'],
                    'competitors': default_baselines[region_name]['competitors'],
                    'irr': tea.get('irr_pct'),
                    'lcoa': tea.get('lcoa_usd_per_kg'),
                }
                print(f"✓ Loaded TEA baseline for {region_name}: NPV=${baselines[region_name]['npv']:.1f}M, LCOH=${baselines[region_name]['lcoh']:.2f}/kg")
        except Exception as e:
            print(f"⚠ Could not load TEA for {location_folder}: {e}")

    return baselines

TEA_BASELINES = None  # Initialized in main()

# ============================================================================
# ENHANCED WEB SEARCH - DEEP SEARCH (10 PAGES)
# ============================================================================

def deep_web_search(query, num_pages=10, results_per_page=10):
    """
    Deep web search using multiple search engines
    Goes 10 pages deep (not just page 1)
    
    Returns up to num_pages * results_per_page results
    """
    all_results = []
    
    print(f"  🔍 Deep searching: '{query}' (up to {num_pages} pages)...")
    
    # Method 1: DuckDuckGo (good for recent news)
    ddg_results = _search_duckduckgo(query, num_pages)
    all_results.extend(ddg_results)
    
    # Method 2: Google News RSS (comprehensive coverage)
    google_news_results = _search_google_news_rss(query)
    all_results.extend(google_news_results)
    
    # Deduplicate by URL
    seen_urls = set()
    unique_results = []
    
    for result in all_results:
        url = result.get('url', '')
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_results.append(result)
    
    print(f"    Found {len(unique_results)} unique results")
    
    return unique_results[:num_pages * results_per_page]

def _search_duckduckgo(query, num_pages=10):
    """
    Search DuckDuckGo HTML (multiple pages)
    """
    results = []
    
    try:
        for page in range(num_pages):
            # DuckDuckGo uses different parameters for pagination
            # Note: DuckDuckGo HTML doesn't support deep pagination well
            # We'll try a few pages but it may return fewer results
            
            if page == 0:
                search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            else:
                # Try to get next page (DuckDuckGo has limited pagination)
                search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&s={page * 30}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(search_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            result_divs = soup.find_all('div', class_='result')
            
            if not result_divs:
                break  # No more results
            
            for div in result_divs:
                try:
                    title_elem = div.find('a', class_='result__a')
                    title = title_elem.get_text(strip=True) if title_elem else 'No title'
                    url = title_elem.get('href', '') if title_elem else ''
                    snippet_elem = div.find('a', class_='result__snippet')
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ''
                    
                    if url and title:
                        results.append({
                            'title': title,
                            'url': url,
                            'snippet': snippet,
                            'source': 'duckduckgo'
                        })
                except Exception as e:
                    continue
            
            # Rate limiting
            time.sleep(1)
    
    except Exception as e:
        print(f"    DuckDuckGo search error: {str(e)}")
    
    return results

def _search_google_news_rss(query):
    """
    Search using Google News RSS (no pagination limits)
    """
    results = []
    
    try:
        # Google News RSS URL
        rss_url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl=US&ceid=US:en"
        
        feed = feedparser.parse(rss_url)
        
        for entry in feed.entries[:50]:  # Google News RSS returns up to ~50 results
            results.append({
                'title': entry.get('title', 'No title'),
                'url': entry.get('link', ''),
                'snippet': entry.get('summary', ''),
                'source': 'google_news',
                'published': entry.get('published', '')
            })
    
    except Exception as e:
        print(f"    Google News RSS error: {str(e)}")
    
    return results

# ============================================================================
# NEWS AGGREGATOR SCRAPERS
# ============================================================================

def scrape_decarbonfuse():
    """
    Scrape Decarbonfuse.com/issues for hydrogen news
    """
    results = []
    
    try:
        url = 'https://decarbonfuse.com/issues'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find article elements (inspect site to get correct selectors)
        # This is a generic approach - may need adjustment based on site structure
        articles = soup.find_all('article')
        
        for article in articles[:30]:  # Top 30 articles
            try:
                title_elem = article.find(['h1', 'h2', 'h3'])
                link_elem = article.find('a')
                
                if title_elem and link_elem:
                    title = title_elem.get_text(strip=True)
                    link = urljoin(url, link_elem.get('href', ''))
                    
                    results.append({
                        'title': title,
                        'url': link,
                        'snippet': '',
                        'source': 'decarbonfuse'
                    })
            except:
                continue
        
        print(f"  ✓ Scraped {len(results)} articles from Decarbonfuse")
    
    except Exception as e:
        print(f"  ✗ Decarbonfuse scraping error: {str(e)}")
    
    return results

def scrape_hydrogen_insight():
    """
    Scrape Hydrogen Insight for latest news
    """
    results = []
    
    try:
        url = 'https://www.hydrogeninsight.com'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find article links
        articles = soup.find_all('article', limit=30)
        
        for article in articles:
            try:
                title_elem = article.find(['h1', 'h2', 'h3', 'h4'])
                link_elem = article.find('a')
                
                if title_elem and link_elem:
                    title = title_elem.get_text(strip=True)
                    link = urljoin(url, link_elem.get('href', ''))
                    
                    results.append({
                        'title': title,
                        'url': link,
                        'snippet': '',
                        'source': 'hydrogen_insight'
                    })
            except:
                continue
        
        print(f"  ✓ Scraped {len(results)} articles from Hydrogen Insight")
    
    except Exception as e:
        print(f"  ✗ Hydrogen Insight scraping error: {str(e)}")
    
    return results

def scrape_recharge_news():
    """
    Scrape Recharge News energy transition section
    """
    results = []
    
    try:
        url = 'https://www.rechargenews.com/energy-transition'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        articles = soup.find_all('article', limit=30)
        
        for article in articles:
            try:
                title_elem = article.find(['h1', 'h2', 'h3'])
                link_elem = article.find('a')
                
                if title_elem and link_elem:
                    title = title_elem.get_text(strip=True)
                    link = urljoin(url, link_elem.get('href', ''))
                    
                    results.append({
                        'title': title,
                        'url': link,
                        'snippet': '',
                        'source': 'recharge_news'
                    })
            except:
                continue
        
        print(f"  ✓ Scraped {len(results)} articles from Recharge News")
    
    except Exception as e:
        print(f"  ✗ Recharge News scraping error: {str(e)}")
    
    return results

# ============================================================================
# ENHANCED RSS FEEDS (EXPANDED LIST)
# ============================================================================

RSS_FEEDS = {
    # Google News aggregators (comprehensive)
    'Google News - Hydrogen Energy': 'https://news.google.com/rss/search?q=hydrogen+energy+OR+%22green+hydrogen%22&hl=en&gl=US&ceid=US:en',
    'Google News - Hydrogen Projects': 'https://news.google.com/rss/search?q=%22hydrogen+project%22+OR+FID+OR+%22final+investment%22&hl=en&gl=US&ceid=US:en',
    'Google News - Green Ammonia': 'https://news.google.com/rss/search?q=%22green+ammonia%22+OR+%22blue+ammonia%22&hl=en&gl=US&ceid=US:en',
    'Google News - Oman Hydrogen': 'https://news.google.com/rss/search?q=Oman+hydrogen+OR+Duqm+OR+Hydrom&hl=en&gl=US&ceid=US:en',
    'Google News - Chile Hydrogen': 'https://news.google.com/rss/search?q=Chile+hydrogen+OR+Magallanes&hl=en&gl=US&ceid=US:en',
    'Google News - Electrolyzer': 'https://news.google.com/rss/search?q=electrolyzer+OR+%22Nel+Hydrogen%22+OR+%22ITM+Power%22&hl=en&gl=US&ceid=US:en',
    
    # Hydrogen-specific RSS feeds
    'Hydrogen Insight RSS': 'https://www.hydrogeninsight.com/feed',
    'H2 View RSS': 'https://www.h2-view.com/feed',
    'Fuel Cells Works RSS': 'https://fuelcellsworks.com/feed',
    
    # Energy news RSS feeds
    'Reuters Energy': 'https://www.reuters.com/business/energy/rss',
    'Bloomberg Energy': 'https://www.bloomberg.com/feed',
    
    # Regional RSS feeds
    'Oman Observer': 'https://www.omanobserver.om/feed/',
    'Times of Oman': 'https://timesofoman.com/rss',
}

# (Keep original keyword categories)
KEYWORDS = {
    'CRITICAL': [
        'FID', 'final investment decision', 'financial close',
        'offtake agreement', 'binding contract', 'signed contract',
        '45V', 'OBBB', 'construction deadline',
    ],
    
    'HIGH': [
        'Oman', 'Duqm', 'Hydrom', 'ACME',
        'Chile', 'Magallanes',
        'Houston',
        'electrolyzer', 'PEM', 'alkaline',
        'subsidy', 'tax credit', 'IRA',
        'LCOH', 'levelized cost',
    ],
    
    'MEDIUM': [
        'green hydrogen', 'green ammonia',
        'renewable energy', 'wind', 'solar',
        'production capacity', 'GW',
        'energy transition', 'decarbonization',
        'Nel', 'ITM Power', 'Plug Power',
        'shipping', 'offtaker',
    ],
    
    'LOW': [
        'hydrogen', 'H2',
        'clean energy', 'zero emission',
        'fuel cell', 'mobility',
    ]
}

DATABASE_NAME = 'hydrogen_intelligence_v2.db'

# ============================================================================
# DATABASE SETUP (ENHANCED SCHEMA)
# ============================================================================

def setup_database():
    """Enhanced database schema with deduplication tracking"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
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
    
    # Create indexes for better performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_url_hash ON articles(url_hash)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON articles(category)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_published_date ON articles(published_date)')
    
    conn.commit()
    conn.close()

def calculate_url_hash(url):
    """Calculate SHA256 hash of URL for deduplication"""
    return hashlib.sha256(url.encode()).hexdigest()

# ============================================================================
# ENHANCED ARTICLE COLLECTION (50+ ARTICLES)
# ============================================================================

def collect_articles_enhanced(min_articles=50, max_articles=100):
    """
    Enhanced article collection from multiple sources
    
    Sources:
    1. RSS feeds (existing + new)
    2. Deep web search (10 pages)
    3. News aggregator scraping
    
    Returns up to max_articles, stops at min_articles if sources exhausted
    """
    print(f"\n{'='*70}")
    print(f"ENHANCED ARTICLE COLLECTION")
    print(f"Target: {min_articles}-{max_articles} articles")
    print(f"{'='*70}\n")
    
    all_articles = []
    
    # 1. RSS Feeds
    print("📡 Fetching RSS feeds...")
    rss_articles = fetch_rss_feeds_enhanced()
    all_articles.extend(rss_articles)
    print(f"  ✓ RSS: {len(rss_articles)} articles\n")
    
    # 2. Deep Web Search
    print("🔍 Deep web search (10 pages per query)...")
    search_queries = [
        'green hydrogen FID 2025',
        'electrolyzer cost reduction',
        'Oman hydrogen project',
        'Chile hydrogen Magallanes',
        'Houston hydrogen infrastructure',
        'green ammonia offtake',
        '45V tax credit hydrogen',
        'LCOH levelized cost hydrogen',
    ]
    
    search_articles = []
    for query in search_queries:
        results = deep_web_search(query, num_pages=5, results_per_page=10)
        search_articles.extend(results)
        
        if len(all_articles) + len(search_articles) >= max_articles:
            break
        
        time.sleep(2)  # Rate limiting
    
    all_articles.extend(search_articles)
    print(f"  ✓ Deep search: {len(search_articles)} articles\n")
    
    # 3. News Aggregator Scraping
    print("🌐 Scraping news aggregators...")
    
    aggregator_articles = []
    
    try:
        decarbonfuse = scrape_decarbonfuse()
        aggregator_articles.extend(decarbonfuse)
    except:
        pass
    
    try:
        h2_insight = scrape_hydrogen_insight()
        aggregator_articles.extend(h2_insight)
    except:
        pass
    
    try:
        recharge = scrape_recharge_news()
        aggregator_articles.extend(recharge)
    except:
        pass
    
    all_articles.extend(aggregator_articles)
    print(f"  ✓ Aggregators: {len(aggregator_articles)} articles\n")
    
    # Deduplicate by URL
    print("🔄 Deduplicating...")
    unique_articles = deduplicate_articles(all_articles)
    print(f"  ✓ {len(all_articles)} → {len(unique_articles)} unique articles\n")
    
    # Categorize and score
    print("📊 Categorizing and scoring...")
    categorized = []
    for article in unique_articles:
        category, score, keywords, region = categorize_article(article['title'], article.get('snippet', ''))
        article['category'] = category
        article['priority_score'] = score
        article['keywords'] = ','.join(keywords)
        article['region'] = region
        categorized.append(article)
    
    # Sort by priority
    categorized.sort(key=lambda x: x['priority_score'], reverse=True)
    
    # Limit to max_articles
    final_articles = categorized[:max_articles]
    
    print(f"  ✓ Final count: {len(final_articles)} articles")
    print(f"     CRITICAL: {sum(1 for a in final_articles if a['category'] == 'CRITICAL')}")
    print(f"     HIGH: {sum(1 for a in final_articles if a['category'] == 'HIGH')}")
    print(f"     MEDIUM: {sum(1 for a in final_articles if a['category'] == 'MEDIUM')}")
    print(f"     LOW: {sum(1 for a in final_articles if a['category'] == 'LOW')}\n")
    
    # Store in database
    stored = store_articles(final_articles)
    
    print(f"{'='*70}")
    print(f"✅ Collection complete: {stored} new articles stored")
    print(f"{'='*70}\n")
    
    return stored

def fetch_rss_feeds_enhanced():
    """Enhanced RSS feed fetching"""
    articles = []
    
    for feed_name, feed_url in RSS_FEEDS.items():
        try:
            print(f"  Fetching: {feed_name}...")
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:20]:  # Top 20 from each feed
                articles.append({
                    'title': entry.get('title', 'No title'),
                    'url': entry.get('link', ''),
                    'snippet': entry.get('summary', ''),
                    'source': feed_name,
                    'published': entry.get('published', '')
                })
        
        except Exception as e:
            print(f"    Error: {str(e)}")
            continue
    
    return articles

def deduplicate_articles(articles):
    """
    Deduplicate articles by URL hash
    """
    seen_hashes = set()
    unique = []
    
    for article in articles:
        url = article.get('url', '')
        if not url:
            continue
        
        url_hash = calculate_url_hash(url)
        
        if url_hash not in seen_hashes:
            seen_hashes.add(url_hash)
            article['url_hash'] = url_hash
            unique.append(article)
    
    return unique

def categorize_article(title, snippet=''):
    """
    Categorize article and calculate priority score
    (Keep original logic)
    """
    text = f"{title} {snippet}".lower()
    
    found_keywords = []
    total_score = 0
    category = 'LOW'
    region = None
    
    # Check CRITICAL keywords
    for keyword in KEYWORDS['CRITICAL']:
        if keyword.lower() in text:
            found_keywords.append(keyword)
            total_score += 10
            category = 'CRITICAL'
    
    # Check HIGH keywords
    for keyword in KEYWORDS['HIGH']:
        if keyword.lower() in text:
            found_keywords.append(keyword)
            total_score += 5
            if category != 'CRITICAL':
                category = 'HIGH'
    
    # Check MEDIUM keywords
    for keyword in KEYWORDS['MEDIUM']:
        if keyword.lower() in text:
            found_keywords.append(keyword)
            total_score += 2
            if category not in ['CRITICAL', 'HIGH']:
                category = 'MEDIUM'
    
    # Check LOW keywords
    for keyword in KEYWORDS['LOW']:
        if keyword.lower() in text:
            found_keywords.append(keyword)
            total_score += 1
    
    # Detect region
    if 'oman' in text or 'duqm' in text:
        region = 'Oman'
    elif 'chile' in text or 'magallanes' in text:
        region = 'Chile'
    elif 'houston' in text or 'texas' in text:
        region = 'Houston'
    
    return category, total_score, list(set(found_keywords)), region

def store_articles(articles):
    """Store articles in database"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    new_count = 0
    
    for article in articles:
        try:
            cursor.execute('''
                INSERT INTO articles 
                (title, url, url_hash, source, snippet, category, priority_score, 
                 published_date, fetched_date, keywords, region)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                article['title'],
                article['url'],
                article['url_hash'],
                article.get('source', 'unknown'),
                article.get('snippet', ''),
                article['category'],
                article['priority_score'],
                article.get('published', ''),
                datetime.now().isoformat(),
                article.get('keywords', ''),
                article.get('region', '')
            ))
            new_count += 1
        
        except sqlite3.IntegrityError:
            # Duplicate URL, skip
            continue
        except Exception as e:
            print(f"  Error storing article: {str(e)}")
            continue
    
    conn.commit()
    conn.close()
    
    return new_count

# ============================================================================
# Keep all original functions (view_recent_articles, generate_daily_digest, etc.)
# Just add option to use enhanced collection
# ============================================================================

def view_recent_articles(days=1, min_score=0):
    """View recent articles (original function)"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
    
    cursor.execute('''
        SELECT id, title, url, category, priority_score, source, published_date, region
        FROM articles
        WHERE fetched_date >= ?
        AND priority_score >= ?
        ORDER BY priority_score DESC, fetched_date DESC
    ''', (cutoff_date, min_score))
    
    articles = cursor.fetchall()
    conn.close()
    
    if not articles:
        print(f"\nNo articles found in last {days} day(s) with score >= {min_score}")
        return
    
    print(f"\n{'='*70}")
    print(f"RECENT ARTICLES (Last {days} day(s), Score >= {min_score})")
    print(f"{'='*70}\n")
    
    for article in articles:
        article_id, title, url, category, score, source, pub_date, region = article
        
        print(f"[{category}] Score: {score} | Region: {region or 'N/A'}")
        print(f"Title: {title}")
        print(f"Source: {source}")
        print(f"URL: {url}")
        if pub_date:
            print(f"Published: {pub_date}")
        print()

# ============================================================================
# MAIN EXECUTION (ENHANCED)
# ============================================================================

def main():
    """Main execution function"""
    global TEA_BASELINES

    print("\n" + "="*70)
    print("ENHANCED HYDROGEN INTELLIGENCE MONITORING SYSTEM v2.0")
    print("="*70)
    print("\nInitializing...")

    # Setup database
    setup_database()

    # Load TEA baselines
    print("\nLoading TEA baselines...")
    TEA_BASELINES = get_tea_baselines()

    # Show menu
    print("\n" + "="*70)
    print("CHOOSE MODE:")
    print("="*70)
    print("1. Run enhanced collection (50+ articles)")
    print("2. Run standard collection (original)")
    print("3. View recent articles")
    print("4. Test deep search")
    print("5. Test news aggregator scraping")
    print("6. View statistics")
    print("="*70)
    
    choice = input("\nEnter choice (1-6): ").strip()
    
    if choice == '1':
        print("\n[ENHANCED MODE] Collecting 50+ articles...\n")
        min_articles = input("Minimum articles to collect (default 50): ").strip() or "50"
        max_articles = input("Maximum articles to collect (default 100): ").strip() or "100"
        
        collect_articles_enhanced(
            min_articles=int(min_articles),
            max_articles=int(max_articles)
        )
    
    elif choice == '2':
        print("\n[STANDARD MODE] Running original RSS collection...\n")
        # Call original fetch_rss_feeds() if you keep it
        articles = fetch_rss_feeds_enhanced()
        stored = store_articles([{**a, 'category': 'LOW', 'priority_score': 1, 'keywords': '', 'region': '', 'url_hash': calculate_url_hash(a['url'])} for a in articles])
        print(f"\n✅ Stored {stored} articles")
    
    elif choice == '3':
        days = input("How many days back? (default 1): ").strip() or '1'
        min_score = input("Minimum priority score? (default 0): ").strip() or '0'
        view_recent_articles(int(days), int(min_score))
    
    elif choice == '4':
        print("\n[TEST] Deep Web Search")
        query = input("Enter search query: ").strip() or "green hydrogen FID"
        num_pages = input("Number of pages (default 5): ").strip() or "5"
        
        results = deep_web_search(query, num_pages=int(num_pages))
        
        print(f"\nFound {len(results)} results:\n")
        for i, result in enumerate(results[:10], 1):
            print(f"{i}. {result['title']}")
            print(f"   URL: {result['url']}")
            print(f"   Source: {result.get('source', 'unknown')}")
            print()
    
    elif choice == '5':
        print("\n[TEST] News Aggregator Scraping")
        
        print("\n1. Decarbonfuse")
        results = scrape_decarbonfuse()
        print(f"   Found: {len(results)} articles")
        
        print("\n2. Hydrogen Insight")
        results = scrape_hydrogen_insight()
        print(f"   Found: {len(results)} articles")
        
        print("\n3. Recharge News")
        results = scrape_recharge_news()
        print(f"   Found: {len(results)} articles")
    
    elif choice == '6':
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM articles')
        total = cursor.fetchone()[0]
        
        cursor.execute('SELECT category, COUNT(*) FROM articles GROUP BY category')
        by_category = cursor.fetchall()
        
        cursor.execute('SELECT region, COUNT(*) FROM articles WHERE region IS NOT NULL GROUP BY region')
        by_region = cursor.fetchall()
        
        conn.close()
        
        print(f"\n{'='*70}")
        print("DATABASE STATISTICS")
        print(f"{'='*70}\n")
        print(f"Total articles: {total}\n")
        
        print("By category:")
        for cat, count in by_category:
            print(f"  {cat}: {count}")
        
        print("\nBy region:")
        for reg, count in by_region:
            print(f"  {reg}: {count}")
        
        print(f"\n{'='*70}\n")
    
    else:
        print("Invalid choice")

if __name__ == "__main__":
    main()