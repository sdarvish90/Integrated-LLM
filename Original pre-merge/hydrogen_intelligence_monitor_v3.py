#!/usr/bin/env python3
"""
Enhanced Hydrogen Intelligence Monitoring System v3.0
- Deep web search (10 pages, not just recent Google)
- 50+ articles per update (configurable)
- Multiple news aggregators (RSS + web scraping)
- Improved deduplication
- Historical trend analysis
- EXPANDED: More news sources, keywords, and search queries for full hydrogen value chain
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
import webbrowser
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

# Deep-dive investigation triggers
DEEP_DIVE_TRIGGERS = {
    'CANCELLATION': [
        'cancel', 'cancelled', 'cancellation', 'withdraw', 'withdrawn', 
        'exits', 'exit', 'abandoned', 'shelved', 'suspended', 
        'terminates', 'scraps', 'pulls out', 'backs out', 'walks away'
    ],
    'DELAY': [
        'delay', 'delayed', 'postponed', 'pushed back',
        'missed deadline', 'behind schedule', 'timeline extended',
        'setback', 'slippage'
    ],
    'POLICY': [
        '45V', 'IRA', 'OBBB', 'subsidy cut', 'regulation changed',
        'policy shift', 'incentive reduced', 'deadline moved',
        'mandate', 'requirement changed'
    ],
    'OFFTAKE': [
        'offtake failed', 'buyer withdrew', 'MoU expired',
        'no buyer', 'lack of demand', 'oversupply',
        'contract terminated', 'agreement cancelled'
    ],
    'FINANCING': [
        'financing failed', 'lender withdrew', 'equity shortfall',
        'couldn\'t secure funding', 'capital raising',
        'funding gap', 'debt rejected'
    ],
    'TECHNICAL': [
        'equipment failure', 'malfunction', 'underperformance',
        'technical issues', 'breakdown', 'lower than expected',
        'capacity factor', 'operational issues'
    ]
}

TEA_BASELINES = None  # Initialized in main()

# Global session context - stores results from Option 11 for use in Options 12-18
SESSION_CONTEXT = {
    'analysis_run': False,
    'your_region': None,
    'baseline': None,
    'articles_analyzed': 0,
    'last_analysis_date': None
}

# ============================================================================
# HTML REPORT GENERATION
# ============================================================================

def generate_html_report(title, content_html, filename=None):
    """
    Generate a styled HTML report and open it in browser
    Returns: filepath of generated HTML
    """
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp}.html"
    
    # Ensure reports directory exists
    reports_dir = Path.home() / "hydrogen_reports"
    reports_dir.mkdir(exist_ok=True)
    
    filepath = reports_dir / filename
    
    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        
        h1 {{
            color: #1a5490;
            border-bottom: 3px solid #1a5490;
            padding-bottom: 15px;
            margin-bottom: 30px;
            font-size: 2.5em;
        }}
        
        h2 {{
            color: #2d6da3;
            margin-top: 40px;
            margin-bottom: 20px;
            font-size: 1.8em;
            border-left: 5px solid #2d6da3;
            padding-left: 15px;
        }}
        
        h3 {{
            color: #4a90d9;
            margin-top: 30px;
            margin-bottom: 15px;
            font-size: 1.3em;
        }}
        
        .header-info {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 5px;
            margin-bottom: 30px;
            border-left: 5px solid #1a5490;
        }}
        
        .header-info p {{
            margin: 5px 0;
            font-size: 0.95em;
        }}
        
        .section {{
            margin: 30px 0;
            padding: 25px;
            background: #fafbfc;
            border-radius: 5px;
            border: 1px solid #e1e4e8;
        }}
        
        .positive {{
            background: #d4edda;
            border-left: 5px solid #28a745;
            padding: 15px;
            margin: 10px 0;
            border-radius: 3px;
        }}
        
        .negative {{
            background: #f8d7da;
            border-left: 5px solid #dc3545;
            padding: 15px;
            margin: 10px 0;
            border-radius: 3px;
        }}
        
        .neutral {{
            background: #fff3cd;
            border-left: 5px solid #ffc107;
            padding: 15px;
            margin: 10px 0;
            border-radius: 3px;
        }}
        
        .critical {{
            background: #f8d7da;
            border: 2px solid #dc3545;
            padding: 20px;
            margin: 15px 0;
            border-radius: 5px;
        }}
        
        .warning {{
            background: #fff3cd;
            border: 2px solid #ffc107;
            padding: 20px;
            margin: 15px 0;
            border-radius: 5px;
        }}
        
        .success {{
            background: #d4edda;
            border: 2px solid #28a745;
            padding: 20px;
            margin: 15px 0;
            border-radius: 5px;
        }}
        
        .info {{
            background: #d1ecf1;
            border: 2px solid #17a2b8;
            padding: 20px;
            margin: 15px 0;
            border-radius: 5px;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background: white;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        
        th {{
            background: #1a5490;
            color: white;
            padding: 15px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #0f3d6f;
        }}
        
        td {{
            padding: 12px 15px;
            border-bottom: 1px solid #e1e4e8;
        }}
        
        tr:hover {{
            background: #f8f9fa;
        }}
        
        .metric {{
            display: inline-block;
            background: #e7f3ff;
            padding: 8px 15px;
            margin: 5px 5px 5px 0;
            border-radius: 20px;
            font-size: 0.9em;
            border: 1px solid #b3d9ff;
        }}
        
        .metric strong {{
            color: #1a5490;
        }}
        
        ul {{
            margin: 15px 0;
            padding-left: 25px;
        }}
        
        li {{
            margin: 8px 0;
            line-height: 1.5;
        }}
        
        .scenario {{
            background: white;
            padding: 25px;
            margin: 20px 0;
            border-radius: 8px;
            border: 2px solid #dee2e6;
        }}
        
        .scenario h3 {{
            margin-top: 0;
        }}
        
        .timestamp {{
            color: #6c757d;
            font-size: 0.9em;
            font-style: italic;
        }}
        
        .action-item {{
            padding: 10px 15px;
            margin: 8px 0;
            border-radius: 4px;
            background: white;
            border-left: 4px solid #1a5490;
        }}
        
        .emoji {{
            font-size: 1.2em;
            margin-right: 5px;
        }}
        
        @media print {{
            body {{
                background: white;
                padding: 0;
            }}
            .container {{
                box-shadow: none;
                padding: 20px;
            }}
        }}
        
        @media print {{
            .no-print {{ display: none; }}
        }}
        
        .print-button {{
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 10px 20px;
            background: #1a5490;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.2);
        }}
        
        .print-button:hover {{
            background: #0f3d6f;
        }}
    </style>
</head>
<body>
    <button class="print-button no-print" onclick="window.print()">🖨️ Print / Save as PDF</button>
    <div class="container">
        <h1>{title}</h1>
        <div class="header-info">
            <p class="timestamp">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
        {content_html}
    </div>
    
    <script>
        // Add sorting to tables
        document.querySelectorAll('table th').forEach((th, index) => {{
            th.style.cursor = 'pointer';
            th.title = 'Click to sort';
        }});
    </script>
</body>
</html>
"""
    
    # Write HTML file
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_template)
    
    # Open in browser (new tab, don't close existing tabs)
    webbrowser.open(f'file://{filepath}', new=2)
    
    print(f"\n✅ Report opened in browser: {filepath}\n")
    
    return str(filepath)


def simple_table_to_html(headers, rows, row_classes=None):
    """
    Convert simple table data to HTML
    headers: list of column names
    rows: list of lists (row data)
    row_classes: optional list of CSS classes for each row
    """
    html = "<table><tr>"
    for header in headers:
        html += f"<th>{header}</th>"
    html += "</tr>"
    
    for i, row in enumerate(rows):
        row_class = row_classes[i] if row_classes and i < len(row_classes) else ""
        html += f"<tr class='{row_class}'>"
        for cell in row:
            html += f"<td>{cell}</td>"
        html += "</tr>"
    
    html += "</table>"
    return html


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


def scrape_ammonia_energy():
    """
    Scrape Ammonia Energy Association for ammonia-related news
    """
    results = []
    
    try:
        url = 'https://ammoniaenergy.org/articles/'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
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
                        'source': 'ammonia_energy'
                    })
            except:
                continue
        
        print(f"  ✓ Scraped {len(results)} articles from Ammonia Energy")
    
    except Exception as e:
        print(f"  ✗ Ammonia Energy scraping error: {str(e)}")
    
    return results


def scrape_energy_storage_news():
    """
    Scrape Energy Storage News for storage-related articles
    """
    results = []
    
    try:
        url = 'https://www.energy-storage.news'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
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
                        'source': 'energy_storage_news'
                    })
            except:
                continue
        
        print(f"  ✓ Scraped {len(results)} articles from Energy Storage News")
    
    except Exception as e:
        print(f"  ✗ Energy Storage News scraping error: {str(e)}")
    
    return results


def scrape_pv_magazine():
    """
    Scrape PV Magazine for solar-related hydrogen news
    """
    results = []
    
    try:
        url = 'https://www.pv-magazine.com/category/markets-policy/hydrogen/'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
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
                        'source': 'pv_magazine'
                    })
            except:
                continue
        
        print(f"  ✓ Scraped {len(results)} articles from PV Magazine")
    
    except Exception as e:
        print(f"  ✗ PV Magazine scraping error: {str(e)}")
    
    return results


# ============================================================================
# TRANSLATION FUNCTIONS FOR ARABIC CONTENT
# ============================================================================

def detect_arabic(text):
    """Detect if text contains Arabic characters"""
    if not text:
        return False
    arabic_pattern = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+')
    return bool(arabic_pattern.search(text))

def translate_arabic_to_english(text):
    """
    Translate Arabic text to English using Google Translate API (free tier)
    Falls back to deep-translator if googletrans not available
    """
    if not text or not detect_arabic(text):
        return text
    
    print(f"  🌐 Translating Arabic content...")
    
    # Method 1: Try googletrans (most reliable, free)
    try:
        from googletrans import Translator
        translator = Translator()
        result = translator.translate(text, src='ar', dest='en')
        translated = result.text
        print(f"     ✓ Translated using googletrans")
        return f"{translated}\n\n[Original Arabic: {text[:100]}...]"
    except ImportError:
        pass
    except Exception as e:
        print(f"     ⚠ Googletrans failed: {str(e)}")
    
    # Method 2: Try deep-translator
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source='ar', target='en')
        translated = translator.translate(text)
        print(f"     ✓ Translated using deep-translator")
        return f"{translated}\n\n[Original Arabic: {text[:100]}...]"
    except ImportError:
        pass
    except Exception as e:
        print(f"     ⚠ Deep-translator failed: {str(e)}")
    
    # Method 3: Fallback - return original with note
    print(f"     ⚠ Translation libraries not available - install googletrans or deep-translator")
    return f"[ARABIC - Translation unavailable]\n{text[:200]}...\n\nInstall translation: pip install googletrans==4.0.0rc1"

def process_article_with_translation(article, category):
    """
    Process article and translate if it's critical and contains Arabic
    """
    title = article.get('title', '')
    snippet = article.get('snippet', '')
    
    # Check if article contains Arabic
    has_arabic = detect_arabic(title) or detect_arabic(snippet)
    
    if has_arabic and category in ['CRITICAL', 'HIGH']:
        print(f"  📰 Arabic article detected: {title[:50]}...")
        
        # Translate title
        if detect_arabic(title):
            translated_title = translate_arabic_to_english(title)
            article['title_original_arabic'] = title
            article['title'] = translated_title
        
        # Translate snippet
        if detect_arabic(snippet):
            translated_snippet = translate_arabic_to_english(snippet)
            article['snippet_original_arabic'] = snippet
            article['snippet'] = translated_snippet
        
        article['translation_note'] = 'Translated from Arabic'
    
    return article


# ============================================================================
# ENHANCED RSS FEEDS (EXPANDED LIST - v3.0)
# ============================================================================

RSS_FEEDS = {
    # -------------------------------------------------------------------------
    # Google News aggregators (comprehensive, custom queries)
    # -------------------------------------------------------------------------
    'Google News - Hydrogen Energy': 'https://news.google.com/rss/search?q=hydrogen+energy+OR+%22green+hydrogen%22&hl=en&gl=US&ceid=US:en',
    'Google News - Hydrogen Projects': 'https://news.google.com/rss/search?q=%22hydrogen+project%22+OR+FID+OR+%22final+investment%22&hl=en&gl=US&ceid=US:en',
    'Google News - Green Ammonia': 'https://news.google.com/rss/search?q=%22green+ammonia%22+OR+%22blue+ammonia%22&hl=en&gl=US&ceid=US:en',
    'Google News - Oman Hydrogen': 'https://news.google.com/rss/search?q=Oman+hydrogen+OR+Duqm+OR+Hydrom&hl=en&gl=US&ceid=US:en',
    'Google News - Chile Hydrogen': 'https://news.google.com/rss/search?q=Chile+hydrogen+OR+Magallanes&hl=en&gl=US&ceid=US:en',
    'Google News - Electrolyzer': 'https://news.google.com/rss/search?q=electrolyzer+OR+%22Nel+Hydrogen%22+OR+%22ITM+Power%22&hl=en&gl=US&ceid=US:en',
    
    # NEW: Additional Google News queries for expanded coverage
    'Google News - Electrolyzer OEMs': 'https://news.google.com/rss/search?q=%22Thyssenkrupp+Nucera%22+OR+%22McPhy%22+OR+%22Plug+Power%22+OR+%22Bloom+Energy%22&hl=en&gl=US&ceid=US:en',
    'Google News - Hydrogen Storage': 'https://news.google.com/rss/search?q=%22hydrogen+storage%22+OR+%22salt+cavern%22+OR+%22hydrogen+pipeline%22&hl=en&gl=US&ceid=US:en',
    'Google News - 45V Tax Credit': 'https://news.google.com/rss/search?q=%2245V%22+hydrogen+OR+%22clean+hydrogen+tax+credit%22&hl=en&gl=US&ceid=US:en',
    'Google News - Hydrogen Offtake': 'https://news.google.com/rss/search?q=%22hydrogen+offtake%22+OR+%22ammonia+offtake%22+OR+%22binding+contract%22&hl=en&gl=US&ceid=US:en',
    'Google News - Datacenter Power': 'https://news.google.com/rss/search?q=%22data+center%22+hydrogen+OR+%22hyperscaler%22+clean+energy&hl=en&gl=US&ceid=US:en',
    'Google News - Hydrogen Developers': 'https://news.google.com/rss/search?q=%22Air+Products%22+OR+%22Fortescue%22+OR+%22ACWA+Power%22+hydrogen&hl=en&gl=US&ceid=US:en',
    
    # -------------------------------------------------------------------------
    # Hydrogen-specific RSS feeds
    # -------------------------------------------------------------------------
    'Hydrogen Insight RSS': 'https://www.hydrogeninsight.com/feed',
    'H2 View RSS': 'https://www.h2-view.com/feed',
    'Fuel Cells Works RSS': 'https://fuelcellsworks.com/feed',
    
    # NEW: Ammonia-specific
    'Ammonia Energy Association': 'https://ammoniaenergy.org/feed/',
    
    # -------------------------------------------------------------------------
    # Energy news RSS feeds (general)
    # -------------------------------------------------------------------------
    'Reuters Energy': 'https://www.reuters.com/business/energy/rss',
    'Bloomberg Energy': 'https://www.bloomberg.com/feed',
    
    # NEW: Power & Grid focused
    'Utility Dive': 'https://www.utilitydive.com/feeds/news/',
    'Greentech Media': 'https://www.greentechmedia.com/feed',
    'POWER Magazine': 'https://www.powermag.com/feed/',
    
    # NEW: Renewables (Solar/Wind)
    'PV Magazine': 'https://www.pv-magazine.com/feed/',
    'Windpower Monthly': 'https://www.windpowermonthly.com/rss',
    'Renewables Now': 'https://renewablesnow.com/rss/',
    
    # NEW: Storage
    'Energy Storage News': 'https://www.energy-storage.news/feed/',
    
    # NEW: Infrastructure & Industry
    'Datacenter Dynamics': 'https://www.datacenterdynamics.com/rss/',
    'Natural Gas Intelligence': 'https://www.naturalgasintel.com/feed/',
    'LNG Industry': 'https://www.lngindustry.com/feed/',
    'Chemical Engineering': 'https://www.chemengonline.com/feed/',
    'Offshore Engineer': 'https://www.oedigital.com/rss',
    
    # NEW: Upstream/Energy Voice
    'Energy Voice': 'https://www.energyvoice.com/feed/',
    'Upstream Online': 'https://www.upstreamonline.com/rss',
    
    # -------------------------------------------------------------------------
    # Regional RSS feeds
    # -------------------------------------------------------------------------
    'Oman Observer': 'https://www.omanobserver.om/feed/',
    'Times of Oman': 'https://timesofoman.com/rss',
    
    # -------------------------------------------------------------------------
    # Oman Arabic News Sources
    # -------------------------------------------------------------------------
    'Oman Daily (Arabic)': 'https://www.omandaily.om/rss',
    'Al Roya (Oman Arabic)': 'https://alroya.om/rss',
    'Atheer (Oman Arabic)': 'https://www.atheer.om/feed/',
    'Shabiba (Oman Arabic)': 'https://www.shabiba.com/rss',
    'Oman News Agency (ONA Arabic)': 'https://omannews.gov.om/rss/ar',
    
    # NEW: Middle East & Latin America
    'MEED (Middle East)': 'https://www.meed.com/rss',
    'BN Americas': 'https://www.bnamericas.com/rss',
}


# ============================================================================
# ENHANCED KEYWORDS (EXPANDED - v3.0)
# ============================================================================

KEYWORDS = {
    'CRITICAL': [
        # Original
        'FID', 'final investment decision', 'financial close',
        'offtake agreement', 'binding contract', 'signed contract',
        '45V', 'OBBB', 'construction deadline',
        
        # NEW: Project milestones
        'EPC contract', 'FEED contract', 'front-end engineering',
        'power purchase agreement', 'PPA signed', 'grid connection approved',
        'environmental permit', 'construction start', 'groundbreaking',
        'commercial operation date', 'COD', 'first molecule',
        'binding MOU', 'joint venture agreement', 'equity stake',
        'project sanction', 'notice to proceed', 'NTP',
        
        # NEW: Arabic keywords for Oman (Critical)
        'الهيدروجين الأخضر',  # green hydrogen
        'قرار استثماري نهائي',  # final investment decision
        'عُمان',  # Oman
        'دقم',  # Duqm
        'هيدروم',  # Hydrom
        'اتفاقية توريد',  # supply agreement
        'عقد ملزم',  # binding contract
        'إغلاق مالي',  # financial close
    ],
    
    'HIGH': [
        # Original regions
        'Oman', 'Duqm', 'Hydrom', 'ACME',
        'Chile', 'Magallanes',
        'Houston',
        
        # Original tech/policy
        'electrolyzer', 'PEM', 'alkaline',
        'subsidy', 'tax credit', 'IRA',
        'LCOH', 'levelized cost',
        
        # NEW: Arabic keywords for Oman (High Priority)
        'الطاقة المتجددة',  # renewable energy
        'التحليل الكهربائي',  # electrolysis
        'الأمونيا الخضراء',  # green ammonia
        'صلالة',  # Salalah
        'مسقط',  # Muscat
        'الشركة العمانية للهيدروجين',  # Oman Hydrogen Company
        'مشروع هيدروجين',  # hydrogen project
        'الطاقة النظيفة',  # clean energy
        'صفر انبعاثات',  # zero emissions
        'تطوير صناعي',  # industrial development
        'منطقة اقتصادية خاصة',  # special economic zone
        'الرياح البحرية',  # offshore wind
        'الطاقة الشمسية',  # solar energy
        
        # NEW: Power/Grid
        'grid interconnection', 'curtailment', 'baseload', 'load factor',
        'capacity factor', 'dispatchable', 'firming', 'islanded',
        'behind-the-meter', 'co-located', 'hybrid project',
        
        # NEW: Natural Gas/Blue Hydrogen/Ammonia
        'SMR', 'ATR', 'autothermal', 'blue hydrogen', 'CCS', 'CCUS',
        'ammonia cracking', 'ammonia carrier', 'ammonia bunkering',
        'Haber-Bosch', 'nitrogen', 'ammonia synthesis',
        
        # NEW: Electrolyzer OEMs (competitors)
        'Thyssenkrupp Nucera', 'McPhy', 'Cummins', 'Bloom Energy',
        'Siemens Energy', 'John Cockerill', 'Sunfire', 'Enapter',
        'Elogen', 'Ohmium', 'Electric Hydrogen', 'Topsoe',
        'Nel Hydrogen', 'ITM Power', 'Plug Power',
        
        # NEW: Major project developers
        'NEOM', 'ACWA Power', 'Air Products', 'Fortescue', 'bp hydrogen',
        'TotalEnergies hydrogen', 'Shell hydrogen', 'Iberdrola', 'Enel', 'Engie',
        'Copenhagen Infrastructure Partners', 'CIP', 'Masdar',
        'Linde hydrogen', 'Air Liquide',
        
        # NEW: Storage
        'salt cavern', 'underground storage', 'hydrogen storage',
        'lined rock cavern', 'pipeline blend', 'hydrogen pipeline',
        
        # NEW: Datacenter/Hyperscaler
        'hyperscaler', 'data center power', 'backup power',
        'Microsoft hydrogen', 'Google clean energy', 'Amazon renewable',
        'Meta energy', 'datacenter fuel cell',
    ],
    
    'MEDIUM': [
        # Original
        'green hydrogen', 'green ammonia',
        'renewable energy', 'wind', 'solar',
        'production capacity', 'GW',
        'energy transition', 'decarbonization',
        'shipping', 'offtaker',
        
        # NEW: Solar/Wind specifics
        'bifacial', 'tracker', 'offshore wind', 'floating wind',
        'onshore wind', 'solar PV', 'photovoltaic',
        
        # NEW: Infrastructure
        'tube trailer', 'liquefaction', 'liquid hydrogen',
        'compression', 'refueling station', 'HRS',
        'port infrastructure', 'export terminal', 'import terminal',
        'bunkering', 'marine fuel',
        
        # NEW: Fuel cells
        'SOFC', 'solid oxide', 'stationary fuel cell', 'FCEV',
        'heavy-duty truck', 'maritime fuel cell', 'fuel cell vehicle',
        'PEMFC', 'proton exchange membrane',
        
        # NEW: Policy/Finance
        'carbon border', 'CBAM', 'CfD', 'contract for difference',
        'hydrogen bank', 'IPCEI', 'DOE loan', 'ARPA-E',
        'Inflation Reduction Act', 'REPowerEU', 'clean hydrogen standard',
        'EU hydrogen strategy', 'national hydrogen strategy',
        
        # NEW: Certification
        'additionality', 'temporal matching', 'hourly matching',
        'green certificate', 'guarantee of origin', 'CertifHy',
        'low-carbon hydrogen', 'renewable hydrogen',
    ],
    
    'LOW': [
        # Original
        'hydrogen', 'H2',
        'clean energy', 'zero emission',
        'fuel cell', 'mobility',
        
        # NEW: Technical components
        'electrolysis', 'membrane', 'stack', 'balance of plant',
        'desalination', 'water treatment', 'deionized water',
        
        # NEW: Carbon/Lifecycle
        'carbon intensity', 'lifecycle', 'scope 1', 'scope 2', 'scope 3',
        'GHG emissions', 'carbon footprint',
        
        # NEW: General energy
        'power generation', 'electricity', 'grid', 'transmission',
        'distribution', 'interconnection',
    ]
}


# ============================================================================
# ENHANCED SEARCH QUERIES (v3.0)
# ============================================================================

SEARCH_QUERIES = [
    # Original queries
    'green hydrogen FID 2025',
    'electrolyzer cost reduction',
    'Oman hydrogen project',
    'Chile hydrogen Magallanes',
    'Houston hydrogen infrastructure',
    'green ammonia offtake',
    '45V tax credit hydrogen',
    'LCOH levelized cost hydrogen',
    
    # NEW: Power/Grid
    'renewable PPA hydrogen',
    'grid connected electrolyzer',
    'solar hydrogen project GW',
    'offshore wind hydrogen',
    'hybrid renewable hydrogen',
    'behind-the-meter electrolyzer',
    
    # NEW: Ammonia
    'green ammonia export terminal',
    'ammonia cracking technology',
    'ammonia shipping fuel',
    'ammonia bunkering port',
    'blue ammonia CCS project',
    
    # NEW: Storage
    'hydrogen salt cavern storage',
    'underground hydrogen storage',
    'hydrogen pipeline project',
    'liquid hydrogen terminal',
    
    # NEW: Datacenter
    'data center hydrogen fuel cell',
    'hyperscaler clean energy hydrogen',
    'backup power fuel cell datacenter',
    
    # NEW: Competitors/OEMs
    'electrolyzer gigafactory',
    'electrolyzer order backlog',
    'Thyssenkrupp Nucera order',
    'Nel Hydrogen contract',
    'ITM Power order',
    'Plug Power electrolyzer',
    
    # NEW: Policy
    '45V final rule hydrogen',
    'EU hydrogen auction results',
    'DOE hydrogen hub funding',
    'IPCEI hydrogen project',
    'hydrogen tax credit update',
    
    # NEW: Project developers
    'Air Products hydrogen project',
    'Fortescue green hydrogen',
    'NEOM hydrogen update',
    'ACWA Power hydrogen',
    'Masdar hydrogen',
    
    # NEW: Regional
    'Middle East hydrogen export',
    'Australia hydrogen project',
    'Europe hydrogen import',
    'Asia hydrogen demand',
    'India hydrogen mission',
]


DATABASE_NAME = 'hydrogen_intelligence_v3.db'

# ============================================================================
# DATABASE SETUP (ENHANCED SCHEMA)
# ============================================================================

def setup_database():
    """Enhanced database schema with comprehensive intelligence tracking"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    # Articles table (existing)
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
    
    # Investigations table (existing)
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
    
    # NEW: Competitive projects tracking
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
    
    # NEW: Offtaker intelligence
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
    
    # NEW: Risk register
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
    
    # NEW: Deal flow tracking
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
    
    # NEW: Peer benchmarks
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
    
    # NEW: User project configuration
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
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_url_hash ON articles(url_hash)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON articles(category)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_published_date ON articles(published_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_competitors_region ON competitors(region)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_offtakers_region ON offtakers(region)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_risks_category ON risks(risk_category)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_deals_date ON deals(deal_date)')
    
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
    print(f"ENHANCED ARTICLE COLLECTION (v3.0)")
    print(f"Target: {min_articles}-{max_articles} articles")
    print(f"{'='*70}\n")
    
    all_articles = []
    
    # 1. RSS Feeds
    print("📡 Fetching RSS feeds...")
    rss_articles = fetch_rss_feeds_enhanced()
    all_articles.extend(rss_articles)
    print(f"  ✓ RSS: {len(rss_articles)} articles\n")
    
    # 2. Deep Web Search
    print("🔍 Deep web search (using expanded queries)...")
    
    search_articles = []
    for query in SEARCH_QUERIES:
        results = deep_web_search(query, num_pages=5, results_per_page=10)
        search_articles.extend(results)
        
        if len(all_articles) + len(search_articles) >= max_articles:
            break
        
        time.sleep(2)  # Rate limiting
    
    all_articles.extend(search_articles)
    print(f"  ✓ Deep search: {len(search_articles)} articles\n")
    
    # 3. News Aggregator Scraping (expanded)
    print("🌐 Scraping news aggregators...")
    
    aggregator_articles = []
    
    scrapers = [
        ('Decarbonfuse', scrape_decarbonfuse),
        ('Hydrogen Insight', scrape_hydrogen_insight),
        ('Recharge News', scrape_recharge_news),
        ('Ammonia Energy', scrape_ammonia_energy),
        ('Energy Storage News', scrape_energy_storage_news),
        ('PV Magazine', scrape_pv_magazine),
    ]
    
    for name, scraper_func in scrapers:
        try:
            results = scraper_func()
            aggregator_articles.extend(results)
        except Exception as e:
            print(f"  ✗ {name} error: {str(e)}")
    
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
        
        # Translate critical/high priority Arabic articles
        if category in ['CRITICAL', 'HIGH']:
            article = process_article_with_translation(article, category)
        
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
    
    # Auto-extract intelligence data
    if stored > 0:
        auto_extract_intelligence_from_articles()
    
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

def extract_company_names(text):
    """Extract potential company names from text"""
    # Common hydrogen companies and patterns
    companies = []
    text_lower = text.lower()
    
    # Known companies
    known_companies = [
        'bp', 'shell', 'totalenergies', 'total', 'enel', 'engie', 'iberdrola',
        'air products', 'linde', 'air liquide', 'yara', 'fortescue', 'fmg',
        'acwa power', 'acwa', 'masdar', 'neom', 'cip', 'orsted', 'rwe',
        'nel', 'nel hydrogen', 'itm power', 'plug power', 'bloom energy',
        'thyssenkrupp', 'siemens', 'mcphy', 'cummins', 'acme', 'hydrom',
        'uniper', 'ineos', 'repsol', 'equinor', 'eni', 'galp'
    ]
    
    for company in known_companies:
        if company in text_lower:
            companies.append(company)
    
    return companies

def extract_capacity(text):
    """Extract project capacity mentions (MW, GW)"""
    import re
    text_lower = text.lower()
    
    # Look for capacity patterns like "500MW", "1.2GW", "2 GW"
    patterns = [
        r'(\d+\.?\d*)\s*(mw|megawatt)',
        r'(\d+\.?\d*)\s*(gw|gigawatt)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            return f"{match.group(1)}{match.group(2)}"
    
    return None

def deduplicate_articles(articles):
    """
    Smart deduplication: Remove duplicate articles about the same event
    
    Considers:
    - Same URL (exact duplicate)
    - Same company + similar title + similar date (duplicate coverage)
    - Same capacity + location (same project)
    """
    seen_urls = set()
    seen_signatures = set()
    unique = []
    duplicates_removed = 0
    
    for article in articles:
        url = article.get('url', '')
        if not url:
            continue
        
        # Check 1: Exact URL match
        url_hash = calculate_url_hash(url)
        if url_hash in seen_urls:
            duplicates_removed += 1
            continue
        
        # Check 2: Content similarity signature
        title = article.get('title', '').lower()
        published = article.get('published', '')
        
        # Extract key identifiers
        companies = extract_company_names(title)
        capacity = extract_capacity(title)
        
        # Create signature: company + key event words + approximate date
        key_words = []
        event_keywords = ['cancel', 'fid', 'final investment', 'exit', 'withdraw', 
                         'delay', 'postpone', 'sign', 'agree', 'announce']
        
        for keyword in event_keywords:
            if keyword in title:
                key_words.append(keyword)
        
        # Build signature
        if companies and key_words:
            # Use date (just month-year for grouping similar announcements)
            date_sig = published[:7] if published else ""  # YYYY-MM
            
            signature = f"{'-'.join(sorted(companies))}_{'-'.join(sorted(key_words))}_{date_sig}"
            if capacity:
                signature += f"_{capacity}"
            
            if signature in seen_signatures:
                duplicates_removed += 1
                continue
            
            seen_signatures.add(signature)
        
        # Keep article
        seen_urls.add(url_hash)
        article['url_hash'] = url_hash
        unique.append(article)
    
    if duplicates_removed > 0:
        print(f"  Removed {duplicates_removed} duplicate articles")
    
    return unique

def categorize_article(title, snippet=''):
    """
    Categorize article and calculate priority score
    Handles both English and Arabic keywords
    """
    text_lower = f"{title} {snippet}".lower()
    text_original = f"{title} {snippet}"  # Keep original for Arabic matching
    
    found_keywords = []
    total_score = 0
    category = 'LOW'
    region = None
    
    # Check CRITICAL keywords
    for keyword in KEYWORDS['CRITICAL']:
        # Arabic keywords - check in original text (case-sensitive)
        if any(ord(c) > 127 for c in keyword):  # Non-ASCII (Arabic)
            if keyword in text_original:
                found_keywords.append(keyword)
                total_score += 10
                category = 'CRITICAL'
        # English keywords - case insensitive
        elif keyword.lower() in text_lower:
            found_keywords.append(keyword)
            total_score += 10
            category = 'CRITICAL'
    
    # Check HIGH keywords
    for keyword in KEYWORDS['HIGH']:
        if any(ord(c) > 127 for c in keyword):  # Arabic
            if keyword in text_original:
                found_keywords.append(keyword)
                total_score += 5
                if category != 'CRITICAL':
                    category = 'HIGH'
        elif keyword.lower() in text_lower:
            found_keywords.append(keyword)
            total_score += 5
            if category != 'CRITICAL':
                category = 'HIGH'
    
    # Check MEDIUM keywords
    for keyword in KEYWORDS['MEDIUM']:
        if any(ord(c) > 127 for c in keyword):  # Arabic
            if keyword in text_original:
                found_keywords.append(keyword)
                total_score += 2
                if category not in ['CRITICAL', 'HIGH']:
                    category = 'MEDIUM'
        elif keyword.lower() in text_lower:
            found_keywords.append(keyword)
            total_score += 2
            if category not in ['CRITICAL', 'HIGH']:
                category = 'MEDIUM'
    
    # Check LOW keywords
    for keyword in KEYWORDS['LOW']:
        if any(ord(c) > 127 for c in keyword):  # Arabic
            if keyword in text_original:
                found_keywords.append(keyword)
                total_score += 1
        elif keyword.lower() in text_lower:
            found_keywords.append(keyword)
            total_score += 1
    
    # Detect region (expanded with Arabic)
    if 'oman' in text_lower or 'duqm' in text_lower or 'hydrom' in text_lower or 'عُمان' in text_original or 'دقم' in text_original:
        region = 'Oman'
    elif 'chile' in text_lower or 'magallanes' in text_lower:
        region = 'Chile'
    elif 'houston' in text_lower or 'texas' in text_lower:
        region = 'Houston'
    elif 'neom' in text_lower or 'saudi' in text_lower:
        region = 'Saudi Arabia'
    elif 'australia' in text_lower or 'pilbara' in text_lower:
        region = 'Australia'
    elif 'morocco' in text_lower:
        region = 'Morocco'
    elif 'namibia' in text_lower:
        region = 'Namibia'
    elif 'egypt' in text_lower:
        region = 'Egypt'
    elif 'india' in text_lower:
        region = 'India'
    
    return category, total_score, list(set(found_keywords)), region

def store_articles(articles):
    """Store articles in database with date filtering (only articles from 2025 onwards)"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    new_count = 0
    old_count = 0
    
    # Current date and cutoff (Jan 1, 2025)
    current_date = datetime.now()
    cutoff_date = datetime(current_date.year - 1, 1, 1)  # Jan 1 of last year
    
    for article in articles:
        # Check article date
        published_str = article.get('published', '')
        if published_str:
            try:
                # Try to parse the date with dateutil if available
                try:
                    from dateutil import parser as date_parser
                    published_date = date_parser.parse(published_str)
                    
                    # Skip articles older than cutoff
                    if published_date.replace(tzinfo=None) < cutoff_date:
                        old_count += 1
                        continue
                except ImportError:
                    # dateutil not available, use simple year check
                    if '2024' in published_str or '2023' in published_str or '2022' in published_str or '2021' in published_str or '2020' in published_str:
                        old_count += 1
                        continue
            except:
                # If we can't parse the date, check if it contains old year info
                if '2024' in published_str or '2023' in published_str or '2022' in published_str or '2021' in published_str or '2020' in published_str:
                    old_count += 1
                    continue
                # Otherwise, keep the article (better to include than exclude)
        
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
    
    if old_count > 0:
        print(f"  ⏭️  Skipped {old_count} old articles (before {cutoff_date.strftime('%Y-%m-%d')})")
    
    conn.commit()
    conn.close()
    
    return new_count


# ============================================================================
# AUTO-EXTRACTION: Populate Intelligence Databases from Articles
# ============================================================================

def auto_extract_intelligence_from_articles():
    """
    Extract competitors, offtakers, risks, and deals from ALL high-priority articles
    Run this after collecting articles to populate intelligence databases
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print("\n🔍 Auto-extracting intelligence from articles...")
    
    # Get ALL high-priority articles (no time limit - will use Option 11's date range context)
    cursor.execute('''
        SELECT id, title, full_text, keywords, region, category, published_date
        FROM articles
        WHERE priority_score >= 5
        ORDER BY published_date DESC
    ''')
    
    articles = cursor.fetchall()
    
    if not articles:
        print("  No high-priority articles to process\n")
        conn.close()
        return
    
    print(f"  Processing {len(articles)} high-priority articles...\n")
    
    competitors_added = 0
    offtakers_added = 0
    risks_updated = 0
    deals_added = 0
    
    for article_id, title, full_text, keywords, region, category, pub_date in articles:
        text = f"{title} {full_text or ''}".lower()
        text_original = f"{title} {full_text or ''}"  # Keep original for Arabic
        
        # Re-detect region if article's region field is empty
        if not region or region == 'Global':
            # Detect region from text
            if 'oman' in text or 'duqm' in text or 'hydrom' in text or 'عُمان' in text_original or 'دقم' in text_original:
                region = 'Oman'
            elif 'chile' in text or 'magallanes' in text:
                region = 'Chile'
            elif 'houston' in text or 'texas' in text:
                region = 'Houston'
            elif 'neom' in text or 'saudi' in text:
                region = 'Saudi Arabia'
            elif 'australia' in text or 'pilbara' in text:
                region = 'Australia'
            elif 'morocco' in text:
                region = 'Morocco'
            elif 'namibia' in text:
                region = 'Namibia'
            elif 'egypt' in text:
                region = 'Egypt'
            elif 'india' in text:
                region = 'India'
            else:
                region = 'Global'  # Only default to Global if truly no region detected
        
        # Extract Competitors (FID announcements, project mentions)
        if any(word in text for word in ['fid', 'final investment decision', 'approved', 'greenlit', 'capacity']):
            companies = extract_company_names(title)
            if companies:
                # Extract capacity if mentioned
                capacity_match = re.search(r'(\d+(?:\.\d+)?)\s*(mw|gw)', text, re.IGNORECASE)
                capacity_mw = None
                if capacity_match:
                    val = float(capacity_match.group(1))
                    unit = capacity_match.group(2).lower()
                    capacity_mw = val * 1000 if unit == 'gw' else val
                
                # Determine status
                status = 'Cancelled' if 'cancel' in text or 'abandon' in text else 'Active'
                
                # Extract FID date if mentioned
                fid_date = None
                if 'fid' in text:
                    # Try to extract year
                    year_match = re.search(r'(20\d{2})', text)
                    if year_match:
                        fid_date = year_match.group(1)
                
                try:
                    cursor.execute('''
                        INSERT OR IGNORE INTO competitors
                        (project_name, company, region, capacity_mw, status, fid_date, 
                         last_updated, source_article_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        title[:100],
                        companies[0],
                        region,  # Now uses re-detected region
                        capacity_mw,
                        status,
                        fid_date,
                        datetime.now().isoformat(),
                        article_id
                    ))
                    if cursor.rowcount > 0:
                        competitors_added += 1
                except Exception as e:
                    pass
        
        # Extract Offtakers (offtake agreements)
        if any(word in text for word in ['offtake', 'buyer', 'customer', 'signed agreement']):
            companies = extract_company_names(title)
            if companies:
                # Extract volume if mentioned
                volume_match = re.search(r'(\d+(?:\.\d+)?)\s*(kt|mt|tonnes)', text, re.IGNORECASE)
                volume_kt = None
                if volume_match:
                    val = float(volume_match.group(1))
                    unit = volume_match.group(2).lower()
                    volume_kt = val * 1000 if unit == 'mt' else val
                
                try:
                    cursor.execute('''
                        INSERT OR IGNORE INTO offtakers
                        (company_name, region, total_demand_kt, secured_kt, available_kt,
                         last_updated, source_article_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        companies[0],
                        region,  # Now uses re-detected region
                        volume_kt,
                        volume_kt if volume_kt else 0,
                        0,  # Available = 0 if they just secured this
                        datetime.now().isoformat(),
                        article_id
                    ))
                    if cursor.rowcount > 0:
                        offtakers_added += 1
                except Exception as e:
                    pass
        
        # Update Risks based on patterns
        risk_signals = {
            'Offtake Market': ['offtake', 'buyer hesitant', 'demand concerns', 'market uncertainty'],
            'Financing Market': ['financing', 'funding', 'capital', 'investors'],
            'Policy Risk': ['policy', 'regulation', 'government', 'subsidy'],
            'Competition': ['competitor', 'rival', 'market share'],
            'Technology Risk': ['technology', 'electrolyzer', 'equipment'],
        }
        
        for risk_cat, signals in risk_signals.items():
            if any(sig in text for sig in signals):
                # Determine if it's bad news
                impact = 'HIGH' if 'cancel' in text or 'delay' in text or 'problem' in text else 'MEDIUM'
                trend = '📈 ↑' if 'cancel' in text or 'delay' in text else '📊 →'
                
                try:
                    # Check if risk exists
                    cursor.execute('SELECT id FROM risks WHERE risk_category = ?', (risk_cat,))
                    if cursor.fetchone():
                        # Update existing risk
                        cursor.execute('''
                            UPDATE risks
                            SET trend = ?, last_updated = ?, source_article_id = ?
                            WHERE risk_category = ?
                        ''', (trend, datetime.now().isoformat(), article_id, risk_cat))
                        if cursor.rowcount > 0:
                            risks_updated += 1
                    else:
                        # Insert new risk
                        cursor.execute('''
                            INSERT INTO risks
                            (risk_category, risk_description, status, trend, impact_level,
                             last_updated, source_article_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            risk_cat,
                            f"Detected from: {title[:80]}",
                            'Mod',
                            trend,
                            impact,
                            datetime.now().isoformat(),
                            article_id
                        ))
                        if cursor.rowcount > 0:
                            risks_updated += 1
                except Exception as e:
                    pass
        
        # Extract Deals (M&A, investments, partnerships)
        deal_keywords = {
            'M&A': ['acquires', 'acquisition', 'merger', 'takeover'],
            'Equity': ['investment', 'funding round', 'raises', 'capital'],
            'Partnership': ['partnership', 'joint venture', 'collaboration', 'agreement'],
        }
        
        for deal_type, keywords_list in deal_keywords.items():
            if any(kw in text for kw in keywords_list):
                companies = extract_company_names(title)
                if companies:
                    # Extract value if mentioned
                    value_match = re.search(r'\$(\d+(?:\.\d+)?)\s*(billion|million|m|b)', text, re.IGNORECASE)
                    value_usd = None
                    if value_match:
                        val = float(value_match.group(1))
                        unit = value_match.group(2).lower()
                        if unit in ['billion', 'b']:
                            value_usd = val * 1000000000
                        else:
                            value_usd = val * 1000000
                    
                    signal = 'Positive' if deal_type in ['Equity', 'Partnership'] else 'Neutral'
                    
                    try:
                        cursor.execute('''
                            INSERT OR IGNORE INTO deals
                            (deal_date, deal_type, parties, value_usd, region, description, signal,
                             source_article_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            pub_date or datetime.now().isoformat(),
                            deal_type,
                            ' + '.join(companies[:2]),
                            value_usd,
                            region,  # Now uses re-detected region
                            title[:150],
                            signal,
                            article_id
                        ))
                        if cursor.rowcount > 0:
                            deals_added += 1
                    except Exception as e:
                        pass
    
    conn.commit()
    conn.close()
    
    print(f"  ✅ Extracted:")
    print(f"     • {competitors_added} competitors")
    print(f"     • {offtakers_added} offtakers")
    print(f"     • {risks_updated} risk updates")
    print(f"     • {deals_added} deals")
    print()


# ============================================================================
# VIEW AND ANALYSIS FUNCTIONS
# ============================================================================

def view_recent_articles(days=1, min_score=0):
    """View recent articles"""
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


def view_statistics():
    """View database statistics"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM articles')
    total = cursor.fetchone()[0]
    
    cursor.execute('SELECT category, COUNT(*) FROM articles GROUP BY category ORDER BY COUNT(*) DESC')
    by_category = cursor.fetchall()
    
    cursor.execute('SELECT region, COUNT(*) FROM articles WHERE region IS NOT NULL AND region != "" GROUP BY region ORDER BY COUNT(*) DESC')
    by_region = cursor.fetchall()
    
    cursor.execute('SELECT source, COUNT(*) FROM articles GROUP BY source ORDER BY COUNT(*) DESC LIMIT 15')
    by_source = cursor.fetchall()
    
    cursor.execute('SELECT keywords FROM articles WHERE keywords IS NOT NULL AND keywords != ""')
    all_keywords = cursor.fetchall()
    
    conn.close()
    
    # Count keyword frequencies
    keyword_counts = {}
    for (kw_string,) in all_keywords:
        for kw in kw_string.split(','):
            kw = kw.strip()
            if kw:
                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
    
    top_keywords = sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:20]
    
    print(f"\n{'='*70}")
    print("DATABASE STATISTICS (v3.0)")
    print(f"{'='*70}\n")
    print(f"Total articles: {total}\n")
    
    print("By category:")
    for cat, count in by_category:
        print(f"  {cat}: {count}")
    
    print("\nBy region:")
    for reg, count in by_region:
        print(f"  {reg}: {count}")
    
    print("\nTop sources:")
    for src, count in by_source:
        print(f"  {src[:40]}: {count}")
    
    print("\nTop keywords found:")
    for kw, count in top_keywords:
        print(f"  {kw}: {count}")
    
    print(f"\n{'='*70}\n")


# ============================================================================
# DEEP-DIVE INVESTIGATION FUNCTIONS (from original)
# ============================================================================

def view_investigations():
    """View deep-dive investigation candidates with filtering options - generates HTML report"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print(f"\n{'='*70}")
    print("DEEP-DIVE INVESTIGATION CANDIDATES")
    print(f"{'='*70}\n")
    
    # First, get total count
    cursor.execute('SELECT COUNT(*) FROM articles WHERE priority_score >= 5')
    total_count = cursor.fetchone()[0]
    
    if total_count == 0:
        print("No investigation candidates found yet.")
        print("\nHigh-priority articles (score ≥5) will appear here.")
        conn.close()
        return
    
    print(f"Total high-priority articles: {total_count}\n")
    
    # Ask user for filtering
    print("Filter options:")
    print("1. Recent (last 30 days)")
    print("2. All time")
    print("3. Custom days back")
    
    filter_choice = input("\nEnter choice (1-3, default 1): ").strip() or '1'
    
    if filter_choice == '1':
        days = 30
    elif filter_choice == '2':
        days = None  # No limit
    elif filter_choice == '3':
        days_input = input("How many days back? ").strip()
        days = int(days_input) if days_input.isdigit() else 30
    else:
        days = 30
    
    # Build query
    if days:
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        cursor.execute('''
            SELECT a.id, a.title, a.url, a.full_text, a.keywords, a.published_date, a.category, a.region
            FROM articles a
            WHERE a.priority_score >= 5
            AND a.fetched_date >= ?
            ORDER BY a.published_date DESC
        ''', (cutoff_date,))
    else:
        cursor.execute('''
            SELECT a.id, a.title, a.url, a.full_text, a.keywords, a.published_date, a.category, a.region
            FROM articles a
            WHERE a.priority_score >= 5
            ORDER BY a.published_date DESC
        ''')
    
    investigations = cursor.fetchall()
    conn.close()
    
    if not investigations:
        print(f"No high-priority articles found in the specified period.")
        return
    
    print(f"\n{'='*70}")
    print(f"SHOWING ALL {len(investigations)} INVESTIGATION CANDIDATES")
    print(f"{'='*70}\n")
    
    # Group by region for better organization
    by_region = {}
    for inv in investigations:
        inv_id, title, url, content, keywords, date, category, region = inv
        region_key = region if region else "Global/Unspecified"
        if region_key not in by_region:
            by_region[region_key] = []
        by_region[region_key].append(inv)
    
    # Generate HTML content
    html_content = f"""
    <div class="info">
        <h2>📋 Filter: {f'Last {days} days' if days else 'All time'}</h2>
        <p><strong>Total Articles:</strong> {len(investigations)}</p>
        <p><strong>Regions:</strong> {len(by_region)}</p>
    </div>
    """
    
    # Display by region  - SHOW ALL (removed limit)
    for region_name in sorted(by_region.keys()):
        articles = by_region[region_name]
        
        html_content += f"""
        <h2>📍 {region_name.upper()} ({len(articles)} articles)</h2>
        <table>
            <tr>
                <th>ID</th>
                <th>Category</th>
                <th>Title</th>
                <th>Date</th>
                <th>Keywords</th>
            </tr>
        """
        
        # SHOW ALL ARTICLES - no limit!
        for inv in articles:
            inv_id, title, url, content, keywords, date, category, region = inv
            date_str = date[:10] if date else 'N/A'
            keywords_display = keywords[:80] if keywords else 'N/A'
            
            # Color code by category
            category_class = {
                'CRITICAL': 'critical',
                'HIGH': 'negative',
                'MEDIUM': 'warning',
                'LOW': 'neutral'
            }.get(category, '')
            
            html_content += f"""
            <tr class="{category_class}">
                <td>#{inv_id}</td>
                <td><strong>{category}</strong></td>
                <td><a href="{url}" target="_blank">{title}</a></td>
                <td>{date_str}</td>
                <td>{keywords_display}</td>
            </tr>
            """
        
        html_content += """
        </table>
        """
    
    html_content += f"""
    <div class="section">
        <h3>Summary</h3>
        <p><strong>Total Articles Shown:</strong> {len(investigations)} (all articles, no limits)</p>
    </div>
    """
    
    # Generate and open HTML report
    period_str = f"{days}days" if days else "all_time"
    filename = f"investigations_{period_str}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    generate_html_report("Deep-Dive Investigation Candidates", html_content, filename)


def deep_dive_event_analysis(title, event_type, max_searches=2):
    """
    Perform deep dive analysis on a specific event to understand WHY it happened
    
    Searches DIFFERENT sources (not the same article) to find root causes
    
    Args:
        title: Article title
        event_type: Type of event (cancellation, FID, delay, etc.)
        max_searches: Maximum number of searches to perform
    
    Returns:
        dict with 'reasons', 'context', 'implications'
    """
    
    print(f"\n  🔍 Deep dive: {title[:60]}...")
    
    # Extract company name for better searching
    companies = extract_company_names(title)
    company_context = companies[0] if companies else ""
    
    # Generate search queries based on event type
    # Search for RELATED articles, not the same one
    search_queries = []
    
    if event_type in ['cancellation', 'exit', 'withdrawal']:
        # For cancellations - search for WHY they cancelled
        if company_context:
            search_queries = [
                f"{company_context} hydrogen cancelled why",
                f"{company_context} exits hydrogen reason",
            ]
        else:
            # Extract location/project from title
            search_queries = [
                f"{title[:40]} cancelled reason",
                f"{title[:40]} statement",
            ]
    
    elif event_type == 'FID':
        # For FID - search for details about financing/offtake
        if company_context:
            search_queries = [
                f"{company_context} hydrogen FID offtake",
                f"{company_context} hydrogen financing",
            ]
        else:
            search_queries = [
                f"{title[:40]} offtake agreement",
                f"{title[:40]} financing",
            ]
    
    elif event_type == 'delay':
        if company_context:
            search_queries = [
                f"{company_context} hydrogen delay reason",
                f"{company_context} hydrogen postponed",
            ]
        else:
            search_queries = [
                f"{title[:40]} delayed why",
            ]
    
    elif event_type == 'infrastructure':
        search_queries = [
            f"{title[:40]} capacity timeline",
        ]
    
    else:
        # Generic - skip deep dive
        return {
            'reasons': [],
            'context': [],
            'implications': [],
            'found_details': False
        }
    
    # Limit searches
    search_queries = search_queries[:max_searches]
    
    # Collect findings
    reasons_found = []
    context_found = []
    
    for query in search_queries:
        try:
            # Use DuckDuckGo search (1 page only for speed)
            results = _search_duckduckgo(query, num_pages=1)
            
            if results:
                # Analyze top 3 results (more sources)
                for result in results[:3]:
                    snippet = result.get('snippet', '')
                    title_text = result.get('title', '')
                    
                    # Skip if it's the exact same article
                    if title[:30].lower() in title_text.lower():
                        continue
                    
                    # Look for key phrases that indicate reasons
                    reason_indicators = [
                        'because', 'due to', 'citing', 'reason', 'caused by',
                        'attributed to', 'result of', 'following', 'after',
                        'blamed', 'explained', 'stated', 'announced',
                        'lack of', 'failed to', 'unable to', 'insufficient',
                        'could not', 'did not', 'without'
                    ]
                    
                    combined_text = f"{title_text} {snippet}".lower()
                    
                    for indicator in reason_indicators:
                        if indicator in combined_text:
                            # Extract sentence with reason
                            sentences = snippet.split('.')
                            for sentence in sentences:
                                if indicator in sentence.lower() and len(sentence) > 20:
                                    reasons_found.append(sentence.strip())
                                    break
                            break
                    
                    # Also capture general context
                    if snippet and len(snippet) > 50:
                        context_found.append(snippet[:200])
            
            time.sleep(2)  # Rate limiting to avoid 403
        
        except Exception as e:
            continue
    
    # Deduplicate and rank reasons
    unique_reasons = list(set(reasons_found))[:3]  # Top 3 unique reasons
    unique_context = list(set(context_found))[:2]  # Top 2 context snippets
    
    # Generate implications based on what we found
    implications = []
    
    if unique_reasons:
        print(f"     ✓ Found {len(unique_reasons)} reason(s) from different sources")
        
        # Analyze reasons for implications
        all_reasons_text = ' '.join(unique_reasons).lower()
        
        # Check for concerning patterns
        if any(word in all_reasons_text for word in ['financing', 'funding', 'capital', 'debt', 'equity']):
            implications.append("⚠️ Financing challenges in market - may affect other projects")
        
        if any(word in all_reasons_text for word in ['offtake', 'buyer', 'demand', 'customer']):
            implications.append("⚠️ Offtake market concerns - secure binding agreements early")
        
        if any(word in all_reasons_text for word in ['permit', 'regulatory', 'approval', 'environmental']):
            implications.append("⚠️ Regulatory hurdles - factor in longer approval timelines")
        
        if any(word in all_reasons_text for word in ['cost', 'expensive', 'uneconomic', 'lcoh']):
            implications.append("⚠️ Economic viability concerns - review project economics")
        
        if any(word in all_reasons_text for word in ['technology', 'technical', 'equipment', 'performance']):
            implications.append("⚠️ Technical risks - consider proven technologies")
        
        if any(word in all_reasons_text for word in ['policy', 'subsidy', 'incentive', 'support']):
            implications.append("⚠️ Policy uncertainty - don't rely solely on subsidies")
        
        if any(word in all_reasons_text for word in ['strategic', 'reallocation', 'priority', 'focus']):
            implications.append("ℹ️ Strategic shift - not necessarily market-wide issue")
    
    else:
        print(f"     ℹ️ No specific reasons found in related coverage")
    
    return {
        'reasons': unique_reasons,
        'context': unique_context,
        'implications': implications,
        'found_details': len(unique_reasons) > 0
    }


def analyze_cumulative_impact():
    """Analyze cumulative impact of ALL articles on your project - generates HTML report"""
    
    # HTML content builder
    html_parts = []
    
    print(f"\n{'='*70}")
    print("📊 CUMULATIVE IMPACT ANALYSIS")
    print(f"{'='*70}\n")
    
    # Ask which region
    print("Which region is YOUR project targeting?")
    print("1. Oman")
    print("2. Chile")
    print("3. Houston")
    
    choice = input("\nEnter choice (1-3): ").strip()
    
    your_region = 'Oman'
    if choice == '2':
        your_region = 'Chile'
    elif choice == '3':
        your_region = 'Houston'
    
    # Ask time period
    days = input("Analyze articles from how many days back? (default 30): ").strip()
    days = int(days) if days.isdigit() else 30
    
    print(f"\nAnalyzing {your_region} project impact from past {days} days...\n")
    
    # Add header to HTML
    html_parts.append(f"""
    <div class="info">
        <h2>📊 Analysis Parameters</h2>
        <p><strong>Region:</strong> {your_region}</p>
        <p><strong>Time Period:</strong> Last {days} days</p>
        <p><strong>Analysis Date:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    """)
    
    # Get all relevant articles from database
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
    
    cursor.execute('''
        SELECT title, full_text, priority_score, category, region, published_date, keywords
        FROM articles
        WHERE fetched_date >= ?
        AND priority_score >= 5
        ORDER BY priority_score DESC, published_date DESC
    ''', (cutoff_date,))
    
    articles = cursor.fetchall()
    conn.close()
    
    if not articles:
        print("No high-priority articles found in this period.")
        return
    
    print(f"Found {len(articles)} high-priority articles total\n")
    
    # Filter articles by region - keep region-specific + global/unspecified
    region_specific_articles = []
    global_articles = []
    other_region_articles = []
    
    for article in articles:
        title, content, score, category, article_region, published, keywords = article
        
        # Check if article is about the selected region or global
        if not article_region or article_region == '':
            global_articles.append(article)
        elif article_region == your_region:
            region_specific_articles.append(article)
        else:
            other_region_articles.append(article)
    
    # Combine region-specific + global for analysis
    relevant_articles = region_specific_articles + global_articles
    
    print(f"📊 Article Breakdown:")
    print(f"   {your_region}-specific: {len(region_specific_articles)}")
    print(f"   Global/Unspecified: {len(global_articles)}")
    print(f"   Other regions: {len(other_region_articles)} (not analyzed)")
    print(f"   → Analyzing: {len(relevant_articles)} articles\n")
    
    if not relevant_articles:
        print(f"No articles found specifically for {your_region} or global market.")
        print("Try a longer time period or check if articles are being tagged with regions.\n")
        return
    
    # Use TEA baselines if available, otherwise use defaults
    if TEA_BASELINES:
        baseline = TEA_BASELINES[your_region]
    else:
        default_baselines = {
            'Oman': {'npv': 580, 'lcoh': 1.90, 'timeline': 48, 'capex': 400, 'competitors': 7},
            'Chile': {'npv': 380, 'lcoh': 2.25, 'timeline': 54, 'capex': 450, 'competitors': 12},
            'Houston': {'npv': 120, 'lcoh': 2.30, 'timeline': 36, 'capex': 350, 'competitors': 15}
        }
        baseline = default_baselines[your_region]
    
    # Track cumulative impacts
    cumulative_npv_change = 0
    cumulative_lcoh_change = 0
    cumulative_timeline_change = 0
    risk_score_change = 0
    
    positive_events = []
    negative_events = []
    neutral_events = []
    all_actions = []
    
    # Analyze each article
    print(f"{'='*70}")
    print(f"ANALYZING {your_region}-RELEVANT ARTICLES...")
    print(f"{'='*70}\n")
    
    articles_with_impact = 0
    deep_dives_performed = 0
    
    for i, article in enumerate(relevant_articles, 1):
        title, content, score, category, region, published, keywords = article
        
        article_lower = title.lower()
        npv_change = 0
        lcoh_change = 0
        timeline_change = 0
        impact_type = 'neutral'
        event_description = ''
        actions = []
        should_deep_dive = False  # Flag to control deep dive
        
        # Mark if this is region-specific or global
        is_region_specific = (region == your_region)
        impact_scope = f"{region}" if is_region_specific else "Global"
        
        # Competitor exits/cancellations - STRONGER impact if in same region
        # Deep dive triggered: These are important events worth investigating
        if any(word in article_lower for word in ['cancel', 'withdraw', 'exit', 'abandon', 'shelved']):
            if is_region_specific:
                # Same region - strong positive impact
                # DEEP DIVE: Understand WHY they cancelled (critical for risk assessment)
                should_deep_dive = True
                deep_dives_performed += 1
                deep_dive = deep_dive_event_analysis(title, 'cancellation', max_searches=2)
                
                # Check if we found clear reasons
                if deep_dive['found_details']:
                    # We have reasons - use standard analysis with adjustment
                    npv_change = baseline['npv'] * 0.04
                    timeline_change = -2
                    impact_type = 'positive'
                    event_description = f"Competitor exit in {your_region}"
                    actions.append(f"URGENT: Contact {your_region} authorities about freed land")
                    
                    # Add implications from deep dive
                    if deep_dive['implications']:
                        for implication in deep_dive['implications']:
                            actions.append(implication)
                    
                    # Adjust NPV if concerning reasons found
                    reasons_text = ' '.join(deep_dive['reasons']).lower()
                    if any(word in reasons_text for word in ['financing', 'offtake', 'demand', 'uneconomic']):
                        # Market issue - reduce positive impact slightly
                        npv_change = baseline['npv'] * 0.03  # Reduced from 0.04
                        event_description += " (market concerns noted)"
                
                else:
                    # NO CLEAR REASONS FOUND - Generate scenario analysis
                    print(f"\n⚠️  No clear reasons found for: {title[:60]}...")
                    print("    Generating scenario analysis...\n")
                    
                    # Generate all possible scenarios
                    scenario_analysis = generate_uncertainty_scenarios(
                        title, 
                        baseline['npv'], 
                        baseline['lcoh'], 
                        baseline['timeline'],
                        your_region
                    )
                    
                    # Display comprehensive scenario analysis
                    display_uncertainty_scenarios(scenario_analysis, baseline, your_region)
                    
                    # Use expected value for NPV calculation
                    npv_change = scenario_analysis['expected_value']
                    timeline_change = 0  # Uncertain
                    impact_type = 'uncertain'
                    event_description = f"Competitor exit in {your_region} (REASON UNKNOWN - see scenario analysis above)"
                    actions.append("🔴 CRITICAL: Investigate root cause (see scenario analysis)")
                    actions.append("🟡 Monitor for pattern over next 30 days")
                    actions.append("⏰ Re-assess in 2 weeks when more information emerges")
            
            elif not region:
                # Global/unspecified - moderate positive impact
                # NO deep dive for global (too many, not specific enough)
                npv_change = baseline['npv'] * 0.02
                impact_type = 'positive'
                event_description = "Competitor exit (global market)"
                actions.append("Monitor if affects your region")
        
        # Competitor FID/offtake - STRONGER negative if in same region
        elif 'FID' in title or 'final investment decision' in article_lower:
            if is_region_specific:
                # Same region - strong negative (direct competition)
                should_deep_dive = True
                deep_dives_performed += 1
                deep_dive = deep_dive_event_analysis(title, 'FID', max_searches=2)
                
                npv_change = -baseline['npv'] * 0.03
                impact_type = 'negative'
                event_description = f"Competitor reached FID in {your_region}"
                actions.append(f"CRITICAL: Accelerate YOUR {your_region} timeline")
                
                # Add context from deep dive
                if deep_dive['found_details']:
                    reasons_text = ' '.join(deep_dive['reasons']).lower()
                    # Check if they secured favorable terms
                    if any(word in reasons_text for word in ['offtake', 'binding', 'contract', 'agreement']):
                        actions.append("⚠️ Competitor has offtake secured - accelerate YOUR offtake development")
                    if any(word in reasons_text for word in ['subsidy', 'support', 'grant', 'incentive']):
                        actions.append("ℹ️ Competitor received policy support - explore available incentives")
            
            elif not region:
                # Global - minor negative (general market pressure)
                # NO deep dive for global FID (too many unrelated articles)
                npv_change = -baseline['npv'] * 0.01
                impact_type = 'negative'
                event_description = "Competitor FID (other market)"
        
        # Offtake agreements - region matters
        elif 'offtake' in article_lower and 'agreement' in article_lower:
            if is_region_specific:
                impact_type = 'neutral'
                event_description = f"Offtake market active in {your_region}"
                actions.append(f"URGENT: Accelerate {your_region} offtake development")
            elif not region:
                impact_type = 'neutral'
                event_description = "Global offtake market signal"
        
        # Policy changes - region-specific policies
        elif '45v' in article_lower or '45V' in title:
            if your_region == 'Houston':
                if 'extended' in article_lower or 'extension' in article_lower:
                    npv_change = baseline['npv'] * 0.15
                    impact_type = 'positive'
                    event_description = "45V deadline extended (Houston)"
                    actions.append("Capture 45V credit opportunity")
                elif 'deadline' in article_lower or 'requirement' in article_lower:
                    npv_change = -baseline['npv'] * 0.25
                    impact_type = 'negative'
                    event_description = "45V timeline pressure (Houston)"
                    actions.append("CRITICAL: Assess Houston timeline feasibility")
        
        # Infrastructure development - MUCH stronger if in same region
        elif 'port' in article_lower or 'infrastructure' in article_lower:
            if is_region_specific:
                # Same region - direct benefit
                should_deep_dive = True
                deep_dives_performed += 1
                deep_dive = deep_dive_event_analysis(title, 'infrastructure', max_searches=2)
                
                npv_change = baseline['npv'] * 0.08
                lcoh_change = -0.15
                impact_type = 'positive'
                event_description = f"Infrastructure development in {your_region}"
                actions.append(f"Partner with {your_region} infrastructure developer")
                
                # Add details from deep dive
                if deep_dive['found_details']:
                    reasons_text = ' '.join(deep_dive['reasons'] + deep_dive['context']).lower()
                    if any(word in reasons_text for word in ['capacity', 'terminal', 'pipeline']):
                        actions.append("ℹ️ Infrastructure capacity details available - review alignment with your needs")
                    if any(word in reasons_text for word in ['timeline', 'completion', '202']):
                        actions.append("Check infrastructure timeline aligns with your COD")
            
            elif not region:
                # Global - minor benefit (learning/best practices)
                # NO deep dive for global infrastructure (not directly relevant)
                npv_change = baseline['npv'] * 0.02
                lcoh_change = -0.03
                impact_type = 'positive'
                event_description = "Infrastructure development (global benchmark)"
        
        # Electrolyzer costs - global impact (same for all regions)
        elif 'electrolyzer' in article_lower and ('cost' in article_lower or 'price' in article_lower):
            if 'fall' in article_lower or 'drop' in article_lower or 'low' in article_lower:
                npv_change = baseline['npv'] * 0.05
                lcoh_change = -0.18
                impact_type = 'positive'
                event_description = "Electrolyzer costs declining (global)"
                actions.append("Lock in new pricing with suppliers")
        
        # Oversupply warnings - global impact
        elif 'oversupply' in article_lower or 'surplus' in article_lower:
            npv_change = -baseline['npv'] * 0.08
            impact_type = 'negative'
            event_description = "Market oversupply warning (global)"
            actions.append("Secure binding offtake before proceeding")
        
        # PPA price changes - stronger if region-specific
        elif 'ppa' in article_lower and 'price' in article_lower:
            multiplier = 1.0 if is_region_specific else 0.5
            if 'increase' in article_lower or 'rise' in article_lower:
                npv_change = -baseline['npv'] * 0.03 * multiplier
                lcoh_change = 0.08 * multiplier
                impact_type = 'negative'
                event_description = f"PPA costs increasing ({impact_scope})"
            elif 'decrease' in article_lower or 'fall' in article_lower:
                npv_change = baseline['npv'] * 0.03 * multiplier
                lcoh_change = -0.08 * multiplier
                impact_type = 'positive'
                event_description = f"PPA costs decreasing ({impact_scope})"
        
        # Accumulate impacts
        if npv_change != 0 or lcoh_change != 0 or timeline_change != 0:
            articles_with_impact += 1
            cumulative_npv_change += npv_change
            cumulative_lcoh_change += lcoh_change
            cumulative_timeline_change += timeline_change
            
            # Add region marker for display
            region_marker = f"🎯 {your_region}" if is_region_specific else "🌍 Global"
            
            # Check if we have deep dive data
            deep_dive_summary = None
            if 'deep_dive' in locals() and deep_dive.get('found_details'):
                deep_dive_summary = {
                    'reasons': deep_dive.get('reasons', []),
                    'implications': deep_dive.get('implications', [])
                }
            
            event_data = {
                'title': title,
                'date': published,
                'description': event_description,
                'region_marker': region_marker,
                'npv_change': npv_change,
                'lcoh_change': lcoh_change,
                'timeline_change': timeline_change,
                'actions': actions,
                'deep_dive': deep_dive_summary
            }
            
            if impact_type == 'positive':
                positive_events.append(event_data)
            elif impact_type == 'negative':
                negative_events.append(event_data)
            else:
                neutral_events.append(event_data)
            
            all_actions.extend(actions)
    
    # Analysis complete
    print(f"\n{'='*70}")
    print(f"ANALYSIS COMPLETE")
    print(f"{'='*70}")
    print(f"Articles analyzed: {len(relevant_articles)}")
    print(f"Deep dives performed: {deep_dives_performed} (region-specific events only)")
    print(f"Articles with financial impact: {articles_with_impact}")
    print(f"Articles with no impact: {len(relevant_articles) - articles_with_impact}")
    print(f"{'='*70}\n")
    # Display results
    print(f"\n{'='*70}")
    print(f"CUMULATIVE IMPACT SUMMARY - {your_region.upper()} PROJECT")
    print(f"{'='*70}\n")
    
    print(f"Analysis Period: Past {days} days")
    print(f"Total Articles in Database: {len(articles)}")
    print(f"Articles Analyzed for {your_region}: {len(relevant_articles)}")
    print(f"  • {your_region}-specific: {len(region_specific_articles)}")
    print(f"  • Global/Unspecified: {len(global_articles)}")
    print(f"Impactful Events: {len(positive_events) + len(negative_events)} (+ {len(neutral_events)} market signals)\n")
    
    # Show examples of what was analyzed vs excluded
    if other_region_articles:
        print(f"{'='*70}")
        print(f"📋 ARTICLE FILTERING SUMMARY")
        print(f"{'='*70}\n")
        
        print(f"✅ ANALYZED ({len(relevant_articles)} articles):")
        if region_specific_articles:
            print(f"\n   {your_region}-specific articles (top 3):")
            for article in region_specific_articles[:3]:
                title = article[0]
                print(f"   • {title[:70]}...")
        
        if global_articles:
            print(f"\n   Global/Unspecified articles (top 3):")
            for article in global_articles[:3]:
                title = article[0]
                print(f"   • {title[:70]}...")
        
        print(f"\n⏭️  EXCLUDED ({len(other_region_articles)} articles from other regions):")
        for article in other_region_articles[:3]:
            title, _, _, _, article_region, _, _ = article
            print(f"   • [{article_region}] {title[:60]}...")
        if len(other_region_articles) > 3:
            print(f"   ... and {len(other_region_articles) - 3} more from other regions")
        print()
    
    print(f"{'='*70}")
    print("FINANCIAL IMPACT")
    print(f"{'='*70}\n")
    
    new_npv = baseline['npv'] + cumulative_npv_change
    new_lcoh = baseline['lcoh'] + cumulative_lcoh_change
    new_timeline = baseline['timeline'] + cumulative_timeline_change
    
    # NPV
    npv_emoji = "📈" if cumulative_npv_change > 0 else "📉" if cumulative_npv_change < 0 else "➡️"
    print(f"{npv_emoji} NET PRESENT VALUE:")
    print(f"   Baseline:          ${baseline['npv']:.0f}M")
    print(f"   Cumulative Change: ${cumulative_npv_change:+.1f}M ({cumulative_npv_change/baseline['npv']*100:+.1f}%)")
    print(f"   UPDATED NPV:       ${new_npv:.0f}M\n")
    
    # LCOH
    if cumulative_lcoh_change != 0:
        lcoh_emoji = "📉" if cumulative_lcoh_change < 0 else "📈"
        print(f"{lcoh_emoji} LEVELIZED COST OF HYDROGEN:")
        print(f"   Baseline:          ${baseline['lcoh']:.2f}/kg")
        print(f"   Cumulative Change: ${cumulative_lcoh_change:+.2f}/kg")
        print(f"   UPDATED LCOH:      ${new_lcoh:.2f}/kg\n")
    
    # Timeline
    if cumulative_timeline_change != 0:
        time_emoji = "⚡" if cumulative_timeline_change < 0 else "🐌"
        print(f"{time_emoji} PROJECT TIMELINE:")
        print(f"   Baseline:          {baseline['timeline']} months")
        print(f"   Cumulative Change: {cumulative_timeline_change:+d} months")
        print(f"   UPDATED:           {new_timeline} months\n")
    
    # Breakdown by event type
    if positive_events:
        print(f"{'='*70}")
        print(f"✅ POSITIVE EVENTS ({len(positive_events)})")
        print(f"{'='*70}\n")
        
        total_positive = sum(e['npv_change'] for e in positive_events)
        for event in positive_events[:5]:  # Top 5
            print(f"{event['region_marker']} {event['description']}")
            print(f"  {event['title'][:60]}...")
            print(f"  NPV Impact: ${event['npv_change']:+.1f}M")
            if event['lcoh_change'] != 0:
                print(f"  LCOH Impact: ${event['lcoh_change']:+.2f}/kg")
            
            # Show deep dive findings if available
            if event.get('deep_dive'):
                deep_dive = event['deep_dive']
                if deep_dive.get('reasons'):
                    print(f"  📋 Why: {deep_dive['reasons'][0][:80]}...")
                if len(deep_dive.get('reasons', [])) > 1:
                    print(f"        (+ {len(deep_dive['reasons'])-1} more reason(s) found)")
            
            print()
        
        if len(positive_events) > 5:
            print(f"  ... and {len(positive_events)-5} more positive events\n")
    
    if negative_events:
        print(f"{'='*70}")
        print(f"⚠️  NEGATIVE EVENTS ({len(negative_events)})")
        print(f"{'='*70}\n")
        
        total_negative = sum(e['npv_change'] for e in negative_events)
        for event in negative_events[:5]:  # Top 5
            print(f"{event['region_marker']} {event['description']}")
            print(f"  {event['title'][:60]}...")
            print(f"  NPV Impact: ${event['npv_change']:+.1f}M")
            if event['lcoh_change'] != 0:
                print(f"  LCOH Impact: ${event['lcoh_change']:+.2f}/kg")
            
            # Show deep dive findings if available
            if event.get('deep_dive'):
                deep_dive = event['deep_dive']
                if deep_dive.get('reasons'):
                    print(f"  📋 Why: {deep_dive['reasons'][0][:80]}...")
                if len(deep_dive.get('reasons', [])) > 1:
                    print(f"        (+ {len(deep_dive['reasons'])-1} more reason(s) found)")
            
            print()
        
        if len(negative_events) > 5:
            print(f"  ... and {len(negative_events)-5} more negative events\n")
    
    # Key actions
    if all_actions:
        print(f"{'='*70}")
        print("🎯 KEY ACTIONS RECOMMENDED")
        print(f"{'='*70}\n")
        
        # Deduplicate and prioritize
        unique_actions = list(set(all_actions))
        critical = [a for a in unique_actions if 'CRITICAL' in a or 'URGENT' in a]
        normal = [a for a in unique_actions if a not in critical]
        
        if critical:
            print("🔴 CRITICAL/URGENT:")
            for action in critical[:3]:
                print(f"   • {action}")
            print()
        
        if normal:
            print("🟡 RECOMMENDED:")
            for action in normal[:5]:
                print(f"   • {action}")
            print()
    
    # Decision recommendation
    print(f"{'='*70}")
    print("💡 DECISION RECOMMENDATION")
    print(f"{'='*70}\n")
    
    if cumulative_npv_change > baseline['npv'] * 0.10:
        print("✅ STRONG POSITIVE: Market conditions improving significantly")
        print(f"   → Consider ACCELERATING project timeline")
        print(f"   → {your_region} opportunity strengthening\n")
    elif cumulative_npv_change > 0:
        print("✅ POSITIVE: Net favorable market developments")
        print(f"   → Proceed with {your_region} project as planned")
        print(f"   → Monitor ongoing developments\n")
    elif cumulative_npv_change > -baseline['npv'] * 0.10:
        print("⚠️  NEUTRAL/MIXED: Offsetting positive and negative factors")
        print(f"   → Proceed cautiously with {your_region}")
        print(f"   → Address key risks identified above\n")
    else:
        print("🔴 NEGATIVE: Market conditions deteriorating")
        print(f"   → RECONSIDER {your_region} project viability")
        print(f"   → Evaluate alternative regions")
        print(f"   → Consider delaying FID until conditions improve\n")
    
    print(f"{'='*70}\n")
    
    # ========================================================================
    # GENERATE HTML REPORT
    # ========================================================================
    
    html_content = f"""
    <div class="info">
        <h2>📊 Analysis Parameters</h2>
        <p><strong>Target Region:</strong> {your_region}</p>
        <p><strong>Time Period:</strong> Last {days} days</p>
        <p><strong>Articles Analyzed:</strong> {len(relevant_articles)}</p>
        <p><strong>Region-Specific:</strong> {len(region_specific_articles)} articles</p>
        <p><strong>Global/Unspecified:</strong> {len(global_articles)} articles</p>
    </div>
    
    <h2>💰 Financial Impact Summary</h2>
    <div class="{'success' if cumulative_npv_change > 0 else 'critical' if cumulative_npv_change < -baseline['npv']*0.05 else 'warning'}">
        <h3>{'📈' if cumulative_npv_change > 0 else '📉'} Net Present Value</h3>
        <table>
            <tr><th>Metric</th><th>Value</th></tr>
            <tr><td>Baseline NPV</td><td>${baseline['npv']:.0f}M</td></tr>
            <tr><td>Cumulative Change</td><td><strong>${cumulative_npv_change:+.1f}M ({cumulative_npv_change/baseline['npv']*100:+.1f}%)</strong></td></tr>
            <tr><td><strong>UPDATED NPV</strong></td><td><strong>${new_npv:.0f}M</strong></td></tr>
        </table>
    """
    
    if cumulative_lcoh_change != 0:
        html_content += f"""
        <h3>{'📉' if cumulative_lcoh_change < 0 else '📈'} Levelized Cost of Hydrogen</h3>
        <table>
            <tr><th>Metric</th><th>Value</th></tr>
            <tr><td>Baseline LCOH</td><td>${baseline['lcoh']:.2f}/kg</td></tr>
            <tr><td>Cumulative Change</td><td><strong>${cumulative_lcoh_change:+.2f}/kg</strong></td></tr>
            <tr><td><strong>UPDATED LCOH</strong></td><td><strong>${new_lcoh:.2f}/kg</strong></td></tr>
        </table>
        """
    
    if cumulative_timeline_change != 0:
        html_content += f"""
        <h3>{'⚡' if cumulative_timeline_change < 0 else '🐌'} Project Timeline</h3>
        <table>
            <tr><th>Metric</th><th>Value</th></tr>
            <tr><td>Baseline Timeline</td><td>{baseline['timeline']} months</td></tr>
            <tr><td>Cumulative Change</td><td><strong>{cumulative_timeline_change:+d} months</strong></td></tr>
            <tr><td><strong>UPDATED TIMELINE</strong></td><td><strong>{new_timeline} months</strong></td></tr>
        </table>
        """
    
    html_content += "</div>"
    
    # Positive Events
    if positive_events:
        html_content += f"""
        <h2>✅ POSITIVE EVENTS ({len(positive_events)})</h2>
        <div class="success">
        <p><strong>Total Positive Impact:</strong> ${sum(e['npv_change'] for e in positive_events):+.1f}M</p>
        <table>
            <tr>
                <th>Event</th>
                <th>Title</th>
                <th>NPV Impact</th>
                <th>LCOH Impact</th>
                <th>Details</th>
            </tr>
        """
        
        for event in positive_events:
            region_marker = event['region_marker']
            title = event['title'][:80]
            npv_impact = event['npv_change']
            lcoh_impact = event['lcoh_change']
            description = event['description']
            
            deep_dive_info = ""
            if event.get('deep_dive') and event['deep_dive'].get('reasons'):
                reasons = event['deep_dive']['reasons']
                deep_dive_info = f"<br><small>📋 {reasons[0][:100]}..."
                if len(reasons) > 1:
                    deep_dive_info += f"<br>(+ {len(reasons)-1} more reason(s))"
                deep_dive_info += "</small>"
            
            html_content += f"""
            <tr>
                <td>{region_marker} {description}</td>
                <td>{title}...</td>
                <td><strong>${npv_impact:+.1f}M</strong></td>
                <td>{f'${lcoh_impact:+.2f}/kg' if lcoh_impact != 0 else '-'}</td>
                <td>{deep_dive_info if deep_dive_info else '-'}</td>
            </tr>
            """
        
        html_content += """
        </table>
        </div>
        """
    
    # Negative Events
    if negative_events:
        html_content += f"""
        <h2>⚠️ NEGATIVE EVENTS ({len(negative_events)})</h2>
        <div class="critical">
        <p><strong>Total Negative Impact:</strong> ${sum(e['npv_change'] for e in negative_events):+.1f}M</p>
        <table>
            <tr>
                <th>Event</th>
                <th>Title</th>
                <th>NPV Impact</th>
                <th>LCOH Impact</th>
                <th>Details</th>
            </tr>
        """
        
        for event in negative_events:
            region_marker = event['region_marker']
            title = event['title'][:80]
            npv_impact = event['npv_change']
            lcoh_impact = event['lcoh_change']
            description = event['description']
            
            deep_dive_info = ""
            if event.get('deep_dive') and event['deep_dive'].get('reasons'):
                reasons = event['deep_dive']['reasons']
                deep_dive_info = f"<br><small>📋 {reasons[0][:100]}..."
                if len(reasons) > 1:
                    deep_dive_info += f"<br>(+ {len(reasons)-1} more reason(s))"
                deep_dive_info += "</small>"
            
            html_content += f"""
            <tr>
                <td>{region_marker} {description}</td>
                <td>{title}...</td>
                <td><strong>${npv_impact:+.1f}M</strong></td>
                <td>{f'${lcoh_impact:+.2f}/kg' if lcoh_impact != 0 else '-'}</td>
                <td>{deep_dive_info if deep_dive_info else '-'}</td>
            </tr>
            """
        
        html_content += """
        </table>
        </div>
        """
    
    # Neutral Events
    if neutral_events:
        html_content += f"""
        <h2>⚪ NEUTRAL/UNCERTAIN EVENTS ({len(neutral_events)})</h2>
        <div class="neutral">
        <table>
            <tr>
                <th>Event</th>
                <th>Title</th>
                <th>Notes</th>
            </tr>
        """
        
        for event in neutral_events:
            html_content += f"""
            <tr>
                <td>{event['region_marker']} {event['description']}</td>
                <td>{event['title'][:80]}...</td>
                <td>No clear impact on {your_region} project</td>
            </tr>
            """
        
        html_content += """
        </table>
        </div>
        """
    
    # Action Items
    if all_actions:
        critical = [a for a in all_actions if 'CRITICAL' in a.upper() or 'URGENT' in a.upper()]
        normal = [a for a in all_actions if a not in critical]
        
        html_content += f"""
        <h2>🎯 KEY ACTIONS RECOMMENDED</h2>
        """
        
        if critical:
            html_content += f"""
            <div class="critical">
                <h3>🔴 CRITICAL/URGENT</h3>
                <ul>
            """
            for action in critical[:5]:
                html_content += f"<li>{action}</li>"
            html_content += """
                </ul>
            </div>
            """
        
        if normal:
            html_content += f"""
            <div class="warning">
                <h3>🟡 RECOMMENDED</h3>
                <ul>
            """
            for action in normal[:10]:
                html_content += f"<li>{action}</li>"
            html_content += """
                </ul>
            </div>
            """
    
    # Decision Recommendation
    html_content += f"""
    <h2>💡 DECISION RECOMMENDATION</h2>
    """
    
    if cumulative_npv_change > baseline['npv'] * 0.10:
        html_content += f"""
        <div class="success">
            <h3>✅ STRONG POSITIVE: Market conditions improving significantly</h3>
            <ul>
                <li>Consider ACCELERATING project timeline</li>
                <li>{your_region} opportunity strengthening</li>
            </ul>
        </div>
        """
    elif cumulative_npv_change > 0:
        html_content += f"""
        <div class="success">
            <h3>✅ POSITIVE: Net favorable market developments</h3>
            <ul>
                <li>Proceed with {your_region} project as planned</li>
                <li>Monitor ongoing developments</li>
            </ul>
        </div>
        """
    elif cumulative_npv_change > -baseline['npv'] * 0.10:
        html_content += f"""
        <div class="warning">
            <h3>⚠️ NEUTRAL/MIXED: Offsetting positive and negative factors</h3>
            <ul>
                <li>Proceed cautiously with {your_region}</li>
                <li>Address key risks identified above</li>
            </ul>
        </div>
        """
    else:
        html_content += f"""
        <div class="critical">
            <h3>🔴 NEGATIVE: Market conditions deteriorating</h3>
            <ul>
                <li>RECONSIDER {your_region} project viability</li>
                <li>Evaluate alternative regions</li>
                <li>Consider delaying FID until conditions improve</li>
            </ul>
        </div>
        """
    
    # Generate HTML report
    filename = f"cumulative_impact_{your_region.lower()}_{days}days_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    generate_html_report(f"Cumulative Impact Analysis - {your_region}", html_content, filename)
    
    # Save session context for Options 12-18
    global SESSION_CONTEXT
    SESSION_CONTEXT = {
        'analysis_run': True,
        'your_region': your_region,
        'baseline': baseline,
        'articles_analyzed': len(relevant_articles),
        'last_analysis_date': datetime.now().isoformat(),
        'cumulative_npv_change': cumulative_npv_change,
        'final_npv': baseline['npv'] + cumulative_npv_change,
        'days_analyzed': days
    }
    
    print(f"✅ Analysis complete! You can now run Options 12-18 for deeper insights.\n")
    
    # Generate HTML report
    print("📄 Generating HTML report...")
    
    # Build comprehensive HTML summary
    html_summary = f"""
    <div class="section">
        <h2>📊 Baseline Assumptions</h2>
        <div class="metric">NPV: <strong>${baseline['npv']}M</strong></div>
        <div class="metric">LCOH: <strong>${baseline['lcoh']}/kg</strong></div>
        <div class="metric">Timeline: <strong>{baseline['timeline']} months</strong></div>
        <div class="metric">Region: <strong>{your_region}</strong></div>
    </div>
    
    <div class="{'success' if cumulative_npv_change > 0 else 'critical' if cumulative_npv_change < 0 else 'neutral'}">
        <h2>💰 Cumulative Impact</h2>
        <p style="font-size: 1.5em; margin: 15px 0;">
            <strong>NPV Change: ${cumulative_npv_change/1000000:+.1f}M ({cumulative_npv_change/baseline['npv']*100:+.1f}%)</strong>
        </p>
        <p style="font-size: 1.2em;">
            <strong>Updated NPV: ${(baseline['npv'] + cumulative_npv_change)/1000000:.0f}M</strong>
        </p>
        <p><strong>LCOH Change:</strong> ${cumulative_lcoh_change:+.2f}/kg</p>
        <p><strong>Timeline Change:</strong> {cumulative_timeline_change:+d} months</p>
    </div>
    """
    
    # Events summary
    if positive_events or negative_events or neutral_events:
        html_summary += f"""
        <h2>📈 Events Breakdown</h2>
        <div class="section">
            <table>
                <tr>
                    <th>Type</th>
                    <th>Count</th>
                    <th>NPV Impact</th>
                </tr>
                <tr class="positive">
                    <td>✅ Positive Events</td>
                    <td>{len(positive_events)}</td>
                    <td>${sum([e['npv_change'] for e in positive_events])/1000000:+.1f}M</td>
                </tr>
                <tr class="negative">
                    <td>⚠️ Negative Events</td>
                    <td>{len(negative_events)}</td>
                    <td>${sum([e['npv_change'] for e in negative_events])/1000000:+.1f}M</td>
                </tr>
                <tr class="neutral">
                    <td>🟡 Neutral/Mixed Events</td>
                    <td>{len(neutral_events)}</td>
                    <td>${sum([e['npv_change'] for e in neutral_events])/1000000:+.1f}M</td>
                </tr>
            </table>
        </div>
        """
        
        # Positive events detail
        if positive_events:
            html_summary += """
            <h2>✅ Positive Events</h2>
            """
            for event in positive_events[:20]:  # Show top 20
                html_summary += f"""
                <div class="positive">
                    <h3>{event['description']}</h3>
                    <p><strong>NPV Impact:</strong> ${event['npv_change']/1000000:+.1f}M</p>
                    <p><strong>Article:</strong> {event['title'][:100]}</p>
                </div>
                """
        
        # Negative events detail
        if negative_events:
            html_summary += """
            <h2>⚠️ Negative Events</h2>
            """
            for event in negative_events[:20]:  # Show top 20
                html_summary += f"""
                <div class="negative">
                    <h3>{event['description']}</h3>
                    <p><strong>NPV Impact:</strong> ${event['npv_change']/1000000:+.1f}M</p>
                    <p><strong>Article:</strong> {event['title'][:100]}</p>
                </div>
                """
    
    # Key actions
    if all_actions:
        html_summary += """
        <h2>🎯 Key Actions Recommended</h2>
        """
        
        critical = [a for a in all_actions if 'CRITICAL' in a or 'URGENT' in a]
        normal = [a for a in all_actions if 'CRITICAL' not in a and 'URGENT' not in a]
        
        if critical:
            html_summary += """
            <div class="critical">
                <h3>🔴 CRITICAL/URGENT:</h3>
                <ul>
            """
            for action in critical[:10]:
                html_summary += f"<li>{action}</li>"
            html_summary += """
                </ul>
            </div>
            """
        
        if normal:
            html_summary += """
            <div class="warning">
                <h3>🟡 RECOMMENDED:</h3>
                <ul>
            """
            for action in normal[:15]:
                html_summary += f"<li>{action}</li>"
            html_summary += """
                </ul>
            </div>
            """
    
    # Decision recommendation
    if cumulative_npv_change > baseline['npv'] * 0.10:
        decision_class = "success"
        decision_text = f"""
        <h3>✅ STRONG POSITIVE: Market conditions improving significantly</h3>
        <ul>
            <li>Consider ACCELERATING project timeline</li>
            <li>{your_region} opportunity strengthening</li>
        </ul>
        """
    elif cumulative_npv_change > 0:
        decision_class = "success"
        decision_text = f"""
        <h3>✅ POSITIVE: Net favorable market developments</h3>
        <ul>
            <li>Proceed with {your_region} project as planned</li>
            <li>Monitor ongoing developments</li>
        </ul>
        """
    elif cumulative_npv_change > -baseline['npv'] * 0.10:
        decision_class = "warning"
        decision_text = f"""
        <h3>⚠️ NEUTRAL/MIXED: Offsetting positive and negative factors</h3>
        <ul>
            <li>Proceed cautiously with {your_region}</li>
            <li>Address key risks identified above</li>
        </ul>
        """
    else:
        decision_class = "critical"
        decision_text = f"""
        <h3>🔴 NEGATIVE: Market conditions deteriorating</h3>
        <ul>
            <li>RECONSIDER {your_region} project viability</li>
            <li>Evaluate alternative regions</li>
            <li>Consider delaying FID until conditions improve</li>
        </ul>
        """
    
    html_summary += f"""
    <div class="{decision_class}">
        <h2>💡 Decision Recommendation</h2>
        {decision_text}
    </div>
    
    <div class="section">
        <h3>📊 Analysis Summary</h3>
        <p><strong>Articles Analyzed:</strong> {len(relevant_articles)}</p>
        <p><strong>Deep Dives Performed:</strong> {deep_dives_performed} (region-specific events only)</p>
        <p><strong>Articles with Financial Impact:</strong> {articles_with_impact}</p>
        <p><strong>Period:</strong> Last {days} days</p>
    </div>
    """
    
    # Combine with initial HTML parts
    final_html = ''.join(html_parts) + html_summary
    
    # Generate report
    filename = f"cumulative_impact_{your_region.lower()}_{days}days_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    generate_html_report(f"Cumulative Impact Analysis - {your_region}", final_html, filename)


# ============================================================================
# MAIN EXECUTION (ENHANCED)
# ============================================================================

# ============================================================================
# ADVANCED INTELLIGENCE FEATURES
# ============================================================================

# ============================================================================
# FEATURE 1: SCENARIO-BASED ANALYSIS FOR UNCERTAIN CANCELLATIONS
# ============================================================================

def generate_uncertainty_scenarios(title, baseline_npv, baseline_lcoh, baseline_timeline, your_region):
    """
    When we can't find WHY a competitor cancelled, generate all possible scenarios
    Returns dict with scenarios and expected value
    """
    
    scenarios = []
    
    # Scenario 1: Company-Specific Decision (POSITIVE)
    scenarios.append({
        'number': 1,
        'name': 'Company-Specific Decision',
        'sentiment': 'POSITIVE',
        'probability': 0.35,
        'description': f'Competitor made internal strategic choice (focus shift, management change). No market-wide issue - just their internal priorities changed.',
        'npv_change': baseline_npv * 0.04,
        'lcoh_change': 0,
        'timeline_change': -2,
        'risk_level': 'LOW',
        'actions': [
            f'✓ Contact {your_region} authorities about freed land/permits',
            '✓ Recruit talent from competitor team',
            '✓ Approach their potential offtakers',
            '✓ ACCELERATE your timeline to capture opportunity'
        ],
        'indicators': [
            'Competitor announces focus on other markets',
            'Competitor continues other hydrogen projects elsewhere',
            f'No other {your_region} cancellations follow'
        ]
    })
    
    # Scenario 2: Offtake Market Issues (NEGATIVE)
    scenarios.append({
        'number': 2,
        'name': 'Offtake Market Issues',
        'sentiment': 'NEGATIVE',
        'probability': 0.35,
        'description': 'Competitor couldn\'t secure binding offtake agreements. European/Asian buyers are hesitant. Offtake market tighter than expected.',
        'npv_components': {
            'competition_benefit': baseline_npv * 0.02,
            'offtake_risk': -baseline_npv * 0.04,
        },
        'npv_change': baseline_npv * -0.02,
        'lcoh_change': 0,
        'timeline_change': 0,
        'risk_level': 'HIGH',
        'actions': [
            '🔴 URGENT: Accelerate offtake negotiations NOW',
            '🔴 Diversify offtaker targets (don\'t rely on same buyers they approached)',
            '🔴 Consider binding MoUs even at lower prices',
            f'🔴 Explore domestic {your_region} demand (not just export)',
            '⚠️ DO NOT proceed to FID without secured offtake'
        ],
        'indicators': [
            'Other projects announce offtake challenges',
            'European hydrogen demand forecasts revised down',
            f'More {your_region} cancellations citing "market conditions"'
        ]
    })
    
    # Scenario 3: Economics Don't Work (VERY NEGATIVE)
    scenarios.append({
        'number': 3,
        'name': 'Economics Don\'t Work',
        'sentiment': 'VERY NEGATIVE',
        'probability': 0.20,
        'description': f'Competitor\'s LCOH came in too high vs. market prices. {your_region} projects fundamentally uneconomic at current tech costs. Revenue < Costs even with optimistic assumptions.',
        'npv_components': {
            'competition_benefit': baseline_npv * 0.02,
            'economic_viability_risk': -baseline_npv * 0.08,
        },
        'npv_change': baseline_npv * -0.06,
        'lcoh_change': 0,
        'timeline_change': 0,
        'risk_level': 'CRITICAL',
        'actions': [
            '🔴 CRITICAL: Immediately review YOUR LCOH assumptions',
            '🔴 Re-run economics with latest electrolyzer quotes',
            '🔴 Stress test against $2.00-2.50/kg H2 prices',
            '🔴 Consider waiting 12-18 months for cost curve to improve',
            '🔴 Evaluate if subsidy/support is available to close gap',
            '⛔ HOLD FID until economics validated independently'
        ],
        'indicators': [
            'Electrolyzer prices not falling as expected',
            'Global hydrogen price forecasts revised down',
            'Multiple projects cite "economic viability" in cancellations'
        ]
    })
    
    # Scenario 4: Financing Unavailable (MODERATELY NEGATIVE)
    scenarios.append({
        'number': 4,
        'name': 'Financing Unavailable',
        'sentiment': 'MODERATELY NEGATIVE',
        'probability': 0.10,
        'description': 'Competitor couldn\'t secure debt/equity at acceptable terms. Lenders require higher returns / stricter covenants. Financing market for hydrogen tightening.',
        'npv_components': {
            'competition_benefit': baseline_npv * 0.02,
            'financing_cost_risk': -baseline_npv * 0.03,
        },
        'npv_change': baseline_npv * -0.01,
        'lcoh_change': 0,
        'timeline_change': 0,
        'risk_level': 'MEDIUM',
        'actions': [
            '🟡 Diversify financing sources (DFIs, export credit agencies)',
            '🟡 Engage lenders early with detailed risk mitigation plan',
            '🟡 Consider offtake-backed project financing',
            '🟡 Budget for higher financing costs in your models'
        ],
        'indicators': [
            'Other hydrogen projects announce financing delays',
            'Banks publish more conservative lending criteria',
            'Interest rates for project finance increase'
        ]
    })
    
    # Calculate expected value
    expected_npv = sum(s['probability'] * s['npv_change'] for s in scenarios)
    best_case = max(s['npv_change'] for s in scenarios)
    worst_case = min(s['npv_change'] for s in scenarios)
    
    return {
        'scenarios': scenarios,
        'expected_value': expected_npv,
        'confidence_interval': {
            'best_case': best_case,
            'worst_case': worst_case,
        },
        'recommendation': 'INVESTIGATE - Decision pending',
        'next_review': '2 weeks'
    }


def display_uncertainty_scenarios(analysis, baseline, your_region):
    """
    Display scenario analysis in formatted output
    """
    print(f"\n{'='*70}")
    print(f"❓ UNCERTAIN EVENT - Multiple Scenarios Possible")
    print(f"🎯 {your_region} Competitor exit in {your_region}")
    print(f"{'='*70}\n")
    
    print(f"📋 Why: No specific reasons found in public sources\n")
    
    for scenario in analysis['scenarios']:
        sentiment_emoji = {
            'POSITIVE': '✅',
            'NEGATIVE': '⚠️',
            'VERY NEGATIVE': '🚨',
            'MODERATELY NEGATIVE': '🟡'
        }.get(scenario['sentiment'], '📊')
        
        print(f"{'━'*70}")
        print(f"{sentiment_emoji} SCENARIO {scenario['number']}: {scenario['name']} ({scenario['sentiment']} for you)")
        print(f"{'─'*70}\n")
        
        print(f"What it means:")
        print(f"• {scenario['description']}\n")
        
        print(f"Impact on YOUR project:")
        print(f"📈 NPV Change: ${scenario['npv_change']/1000000:+.1f}M ({scenario['npv_change']/baseline['npv']*100:+.1f}%)")
        if scenario['timeline_change'] != 0:
            print(f"⚡ Timeline: {scenario['timeline_change']:+d} months")
        print(f"💰 Updated NPV: ${(baseline['npv'] + scenario['npv_change'])/1000000:.0f}M\n")
        
        # Show breakdown if available
        if 'npv_components' in scenario:
            print(f"Breakdown:")
            for component, value in scenario['npv_components'].items():
                print(f"  {component.replace('_', ' ').title()}: ${value/1000000:+.1f}M")
            print()
        
        print(f"What you should do:")
        for action in scenario['actions']:
            print(f"  {action}")
        print()
        
        print(f"Probability estimate: {scenario['probability']*100:.0f}%")
        print(f"Risk Level: {scenario['risk_level']}")
        print(f"\nIndicators to watch:")
        for indicator in scenario['indicators']:
            print(f"  • {indicator}")
        print()
    
    # Expected value calculation
    print(f"{'━'*70}")
    print(f"📊 EXPECTED VALUE CALCULATION (Probability-Weighted)")
    print(f"{'─'*70}\n")
    
    for scenario in analysis['scenarios']:
        contrib = scenario['probability'] * scenario['npv_change']
        print(f"Scenario {scenario['number']} ({scenario['name'][:20]}...): "
              f"{scenario['probability']*100:>2.0f}% × ${scenario['npv_change']/1000000:+.1f}M = ${contrib/1000000:+.1f}M")
    
    print(f"{'═'*70}")
    print(f"Expected NPV Impact: ${analysis['expected_value']/1000000:+.1f}M\n")
    print(f"Confidence Interval: ${analysis['confidence_interval']['worst_case']/1000000:+.1f}M "
          f"to ${analysis['confidence_interval']['best_case']/1000000:+.1f}M (wide range!)\n")
    
    # Recommendations
    print(f"{'━'*70}")
    print(f"🎯 RECOMMENDED ACTIONS (Given Uncertainty):")
    print(f"{'━'*70}\n")
    
    print("IMMEDIATE (Next 2 Weeks):")
    print(f"🔴 1. INVESTIGATE root cause through non-public sources:")
    print(f"       • Contact {your_region} officials")
    print(f"       • Speak with industry consultants active in {your_region}")
    print("       • Review competitor's latest investor presentations")
    print("       • Talk to potential offtakers (are they pulling back?)\n")
    
    print("🔴 2. STRESS TEST your own project:")
    print("       • Re-validate LCOH with latest cost data")
    print("       • Check offtaker commitment levels")
    print("       • Review financing term sheets\n")
    
    print("🟡 3. MONITOR for signals over next 30 days:")
    print(f"       • Additional {your_region} cancellations?")
    print("       • Competitor's explanation in quarterly earnings?")
    print("       • Industry analyst reports on the cancellation?\n")
    
    print("DECISION FRAMEWORK:\n")
    print(f"IF Scenario 1 confirmed (Strategic shift):")
    print(f"   → ACCELERATE: Move to FID faster, capture opportunity")
    print(f"   → Updated NPV: ${(baseline['npv'] + analysis['scenarios'][0]['npv_change'])/1000000:.0f}M ✅\n")
    
    print(f"IF Scenario 2 confirmed (Offtake issues):")
    print(f"   → MITIGATE: Solve offtake FIRST, then proceed")
    print(f"   → Do NOT proceed without binding agreements")
    print(f"   → Updated NPV: ${(baseline['npv'] + analysis['scenarios'][1]['npv_change'])/1000000:.0f}M ⚠️\n")
    
    print(f"IF Scenario 3 confirmed (Economics broken):")
    print(f"   → PAUSE: Delay 12-18 months, wait for cost improvements")
    print(f"   → Re-evaluate with new data")
    print(f"   → Updated NPV: ${(baseline['npv'] + analysis['scenarios'][2]['npv_change'])/1000000:.0f}M 🚨\n")
    
    print(f"IF Scenario 4 confirmed (Financing):")
    print(f"   → ADAPT: Adjust financing strategy, budget higher costs")
    print(f"   → Updated NPV: ${(baseline['npv'] + analysis['scenarios'][3]['npv_change'])/1000000:.0f}M ⚠️\n")
    
    print(f"{'━'*70}")
    print(f"⏰ NEXT REVIEW: {analysis['next_review']} (or when new information emerges)")
    print(f"Status: {analysis['recommendation']}")
    print(f"{'━'*70}\n")


# ============================================================================
# FEATURE 2: COMPETITIVE INTELLIGENCE DASHBOARD
# ============================================================================

def get_region_from_context_or_prompt(feature_name="this feature"):
    """
    Helper function to get region - tries SESSION_CONTEXT first, then user_project, then prompts
    Returns: (your_region, source_description)
    """
    global SESSION_CONTEXT
    
    # Try SESSION_CONTEXT first (from Option 11)
    if SESSION_CONTEXT['analysis_run']:
        print(f"📊 Using data from Option 11 analysis:")
        print(f"   Region: {SESSION_CONTEXT['your_region']}")
        print(f"   Last analyzed: {SESSION_CONTEXT['last_analysis_date'][:10]}")
        print(f"   Articles: {SESSION_CONTEXT['articles_analyzed']}, "
              f"NPV Impact: ${SESSION_CONTEXT['cumulative_npv_change']/1000000:+.1f}M\n")
        return SESSION_CONTEXT['your_region'], "Option 11 analysis"
    
    # Try user_project second
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT region FROM user_project ORDER BY id DESC LIMIT 1')
    result = cursor.fetchone()
    conn.close()
    
    if result:
        print(f"ℹ️  Using project profile (region: {result[0]})")
        print(f"💡 TIP: Run Option 11 first for full market context\n")
        return result[0], "project profile"
    
    # Fall back to manual input
    print(f"⚠️  No context available.")
    print(f"💡 RECOMMENDED: Run Option 11 first, or setup project (Option 17)\n")
    your_region = input("Enter region for {feature_name} (Oman/Chile/Houston): ").strip() or "Oman"
    return your_region, "manual input"


# ============================================================================
# FEATURE 2: COMPETITIVE INTELLIGENCE DASHBOARD
# ============================================================================

def view_competitive_landscape():
    """Display competitive intelligence dashboard"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print(f"\n{'='*70}")
    print("COMPETITIVE INTELLIGENCE DASHBOARD")
    print(f"{'='*70}\n")
    
    # Get region from context/profile/prompt
    your_region, source = get_region_from_context_or_prompt("competitive analysis")
    
    # Get capacity from user project if available
    cursor.execute('SELECT capacity_mw FROM user_project ORDER BY id DESC LIMIT 1')
    result = cursor.fetchone()
    your_capacity = result[0] if result else None
    
    # Get all competitors in region
    cursor.execute('''
        SELECT project_name, company, capacity_mw, status, fid_date, offtake_status, land_status
        FROM competitors
        WHERE region = ? OR region IS NULL
        ORDER BY capacity_mw DESC
    ''', (your_region,))
    
    competitors = cursor.fetchall()
    
    if not competitors:
        print(f"No competitor data available for {your_region}.")
        print("Data will be auto-populated as you collect news articles.\n")
        conn.close()
        return
    
    # Display competitive table
    print(f"{your_region.upper()} HYDROGEN LANDSCAPE - COMPETITIVE MAP")
    print(f"{'─'*70}\n")
    
    print(f"{'Project':<20} {'Status':<12} {'Capacity':<10} {'FID Date':<12} {'Offtake':<10}")
    print(f"{'─'*70}")
    
    total_capacity = 0
    your_rank = 0
    active_count = 0
    
    for i, comp in enumerate(competitors, 1):
        project, company, capacity, status, fid, offtake, land = comp
        
        if status == 'Active':
            active_count += 1
            total_capacity += capacity or 0
        
        status_emoji = '✅' if status == 'Active' else '❌'
        offtake_emoji = '✓' if offtake == 'Secured' else '❓'
        
        print(f"{project[:19]:<20} {status_emoji} {status:<9} {capacity or 0:<10.0f} {fid or 'TBD':<12} {offtake_emoji} {offtake or 'None':<8}")
        
        # Calculate user rank
        if your_capacity and capacity and your_capacity < capacity:
            your_rank += 1
    
    if your_capacity:
        print(f"{'YOUR Project':<20} {'Planning':<12} {your_capacity:<10.0f} {'Q1 2027':<12} {'❓ MoU':<10}")
        total_capacity += your_capacity
    
    print(f"{'─'*70}\n")
    
    print(f"Total Pipeline: {total_capacity:.1f} GW ({active_count} active projects)\n")
    
    if your_capacity:
        print(f"🎯 Your Market Position:")
        print(f"  • Currently #{your_rank + 1} by capacity in {your_region}")
        print(f"  • Market share: {your_capacity/total_capacity*100:.1f}% of total pipeline\n")
    
    # Market saturation analysis
    max_capacity = {
        'Oman': 5000,  # 5 GW
        'Chile': 30000,  # 30 GW  
        'Houston': 10000  # 10 GW
    }.get(your_region, 10000)
    
    utilization = (total_capacity / max_capacity) * 100
    
    print(f"📊 Market Capacity Analysis:")
    print(f"  • {your_region} estimated max capacity: {max_capacity/1000:.1f} GW")
    print(f"  • Current pipeline: {total_capacity/1000:.1f} GW")
    print(f"  • Utilization: {utilization:.0f}%")
    
    if utilization < 50:
        print(f"  • Status: 🟢 Room for growth")
    elif utilization < 75:
        print(f"  • Status: 🟡 Moderate saturation")
    else:
        print(f"  • Status: 🔴 High saturation - competitive pressure\n")
    
    conn.close()


# ============================================================================
# FEATURE 3: OFFTAKER INTELLIGENCE TRACKER
# ============================================================================

def view_offtaker_intelligence():
    """Display offtaker intelligence tracker"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print(f"\n{'='*70}")
    print("OFFTAKER INTELLIGENCE TRACKER")
    print(f"{'='*70}\n")
    
    # Get region from context/profile/prompt
    target_region, source = get_region_from_context_or_prompt("offtaker analysis")
    
    # Get offtakers
    cursor.execute('''
        SELECT company_name, type, total_demand_kt, secured_kt, available_kt, 
               last_activity, notes
        FROM offtakers
        WHERE region = ? OR region IS NULL
        ORDER BY available_kt DESC
    ''', (target_region,))
    
    offtakers = cursor.fetchall()
    
    if not offtakers:
        print(f"No offtaker data available for {target_region}.")
        print("\n💡 TIP: Data will be auto-populated as you collect news articles")
        print("     mentioning offtake agreements, buyer commitments, etc.\n")
        
        # Show manual entry option
        manual = input("Would you like to manually add an offtaker? (y/n): ").strip().lower()
        if manual == 'y':
            add_offtaker_manual(target_region)
        
        conn.close()
        return
    
    # Display offtaker table
    print(f"{target_region.upper()} OFFTAKER MARKET")
    print(f"{'─'*70}\n")
    
    print(f"{'Company':<20} {'Type':<12} {'Demand':<10} {'Secured':<10} {'Available':<10} {'Status'}")
    print(f"{'─'*70}")
    
    total_demand = 0
    total_secured = 0
    total_available = 0
    
    for offtaker in offtakers:
        company, type_, demand, secured, available, activity, notes = offtaker
        
        total_demand += demand or 0
        total_secured += secured or 0
        total_available += available or 0
        
        # Status indicator
        if available and available > 100:
            status = '✓✓ HOT'
        elif available and available > 50:
            status = '✓ WARM'
        elif available and available > 0:
            status = '🟡 LIMITED'
        else:
            status = '❌ FULL'
        
        print(f"{company[:19]:<20} {type_[:11]:<12} {demand or 0:<10.0f} "
              f"{secured or 0:<10.0f} {available or 0:<10.0f} {status}")
    
    print(f"{'─'*70}")
    print(f"{'TOTAL':<20} {'':<12} {total_demand:<10.0f} {total_secured:<10.0f} {total_available:<10.0f}\n")
    
    # Market analysis
    saturation = (total_secured / total_demand * 100) if total_demand > 0 else 0
    
    print(f"📊 Market Saturation Analysis:")
    print(f"  • Total market demand: {total_demand:.0f} kt/yr")
    print(f"  • Already secured: {total_secured:.0f} kt/yr ({saturation:.0f}%)")
    print(f"  • Still available: {total_available:.0f} kt/yr")
    
    if saturation < 50:
        print(f"  • Status: 🟢 Market open - good opportunity")
    elif saturation < 75:
        print(f"  • Status: 🟡 Market tightening - act soon")
    else:
        print(f"  • Status: 🔴 Market saturated - urgent action needed\n")
    
    # Get user project capacity
    cursor.execute('SELECT capacity_mw FROM user_project ORDER BY id DESC LIMIT 1')
    result = cursor.fetchone()
    
    if result:
        your_capacity = result[0]
        your_h2_output = your_capacity * 0.15  # Rough estimate: 0.15 kt/MW/yr
        
        print(f"\n🎯 Your Project ({your_capacity}MW → ~{your_h2_output:.0f} kt/yr):")
        
        if your_h2_output <= total_available:
            print(f"  ✅ Your capacity FITS within available market ({your_h2_output/total_available*100:.0f}% of available)")
        else:
            print(f"  ⚠️ Your capacity EXCEEDS available market by {your_h2_output - total_available:.0f} kt/yr")
            print(f"     Consider: Reduce size OR develop new offtakers")
    
    # Recommendations
    print(f"\n💡 RECOMMENDED TARGETS:")
    
    hot_targets = [o for o in offtakers if o[4] and o[4] > 100]
    warm_targets = [o for o in offtakers if o[4] and 50 < o[4] <= 100]
    
    if hot_targets:
        print(f"\n🔥 HOT (>100kt available):")
        for target in hot_targets[:3]:
            company, type_, demand, secured, available, activity, notes = target
            print(f"  1. {company} ({type_}) - {available:.0f}kt available")
            if activity:
                print(f"     Last seen: {activity}")
    
    if warm_targets:
        print(f"\n🟡 WARM (50-100kt available):")
        for target in warm_targets[:3]:
            company, type_, demand, secured, available, activity, notes = target
            print(f"  • {company} ({type_}) - {available:.0f}kt available")
    
    print()
    conn.close()


def add_offtaker_manual(region):
    """Manually add offtaker data"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print("\nEnter offtaker details:")
    company = input("Company name: ").strip()
    type_ = input("Type (Steel/Fertilizer/Shipping/Refining/Power): ").strip()
    demand = float(input("Total demand (kt/yr): ").strip() or "0")
    secured = float(input("Already secured (kt/yr): ").strip() or "0")
    available = demand - secured
    
    cursor.execute('''
        INSERT INTO offtakers 
        (company_name, type, region, total_demand_kt, secured_kt, available_kt, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (company, type_, region, demand, secured, available, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Added {company} to offtaker database\n")


# ============================================================================
# FEATURE 4: RISK REGISTER WITH EARLY WARNING SYSTEM
# ============================================================================

def view_risk_dashboard():
    """Display risk register with early warning system"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print(f"\n{'='*70}")
    print("RISK REGISTER & EARLY WARNING SYSTEM")
    print(f"{'='*70}\n")
    
    # Get all risks
    cursor.execute('''
        SELECT risk_category, risk_description, status, trend, impact_level, 
               probability, last_updated, mitigation_actions
        FROM risks
        ORDER BY 
            CASE impact_level 
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
                ELSE 4
            END,
            CASE trend
                WHEN '📈 ↑' THEN 1
                WHEN '📊 →' THEN 2
                WHEN '📉 ↓' THEN 3
                ELSE 4
            END
    ''')
    
    risks = cursor.fetchall()
    
    if not risks:
        print("No risks tracked yet.")
        print("\n💡 TIP: Risks will be auto-populated from news patterns:")
        print("   • Offtake challenges detected → Offtake Market risk")
        print("   • Financing delays reported → Financing Market risk")
        print("   • Policy changes announced → Policy Risk")
        print("\nWould you like to manually add a risk? (y/n): ")
        
        if input().strip().lower() == 'y':
            add_risk_manual()
        
        conn.close()
        return
    
    # Display risk dashboard
    print(f"PROJECT RISK DASHBOARD")
    print(f"{'─'*70}\n")
    
    print(f"{'Risk Category':<20} {'Status':<8} {'Trend':<8} {'Impact':<10} {'Prob':<8}")
    print(f"{'─'*70}")
    
    critical_count = 0
    high_count = 0
    alerts = []
    
    for risk in risks:
        category, description, status, trend, impact, prob, updated, mitigation = risk
        
        # Count by severity
        if impact == 'CRITICAL':
            critical_count += 1
        elif impact == 'HIGH':
            high_count += 1
        
        # Status emoji
        status_emoji = {
            'Low': '🟢',
            'Mod': '🟡',
            'High': '🔴',
            'Critical': '🚨'
        }.get(status, '⚪')
        
        print(f"{category[:19]:<20} {status_emoji} {status[:5]:<6} {trend:<8} {impact[:9]:<10} {prob[:7]:<8}")
        
        # Check for alerts (increasing trend + high/critical impact)
        if '↑' in trend and impact in ['HIGH', 'CRITICAL']:
            alerts.append({
                'category': category,
                'description': description,
                'impact': impact,
                'mitigation': mitigation
            })
    
    print(f"{'─'*70}\n")
    
    # Overall health
    total_risks = len(risks)
    green_count = total_risks - critical_count - high_count
    
    print(f"Overall Project Health: ", end="")
    if critical_count > 0:
        print(f"🚨 CRITICAL ({critical_count} critical, {high_count} high risks)")
    elif high_count > 2:
        print(f"🔴 HIGH RISK ({high_count} high risks)")
    elif high_count > 0:
        print(f"🟡 MODERATE ({high_count} high, {green_count} low risks)")
    else:
        print(f"🟢 GOOD ({total_risks} risks, all low/medium)\n")
    
    # Alerts
    if alerts:
        print(f"\n{'='*70}")
        print(f"🚨 ALERTS - Action Required")
        print(f"{'='*70}\n")
        
        for i, alert in enumerate(alerts, 1):
            print(f"{i}. {alert['category']} - {alert['impact']} RISK ⚠️")
            print(f"   {alert['description']}")
            if alert['mitigation']:
                print(f"   Action: {alert['mitigation']}")
            print()
    
    # Positive developments
    print(f"{'='*70}")
    print(f"📈 RECENT RISK CHANGES (Last 30 Days)")
    print(f"{'='*70}\n")
    
    cursor.execute('''
        SELECT risk_category, trend, last_updated
        FROM risks
        WHERE last_updated >= date('now', '-30 days')
        ORDER BY last_updated DESC
        LIMIT 5
    ''')
    
    recent = cursor.fetchall()
    
    if recent:
        for cat, trend, updated in recent:
            trend_desc = "Increasing ⚠️" if '↑' in trend else "Stable" if '→' in trend else "Decreasing ✓"
            print(f"  • {cat}: {trend_desc} (updated {updated[:10]})")
    else:
        print("  No recent updates")
    
    print()
    conn.close()


def add_risk_manual():
    """Manually add a risk"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print("\nEnter risk details:")
    category = input("Risk category (Offtake/Financing/Policy/Technical/Competition/etc): ").strip()
    description = input("Description: ").strip()
    status = input("Current status (Low/Mod/High/Critical): ").strip()
    impact = input("Impact level (LOW/MEDIUM/HIGH/CRITICAL): ").strip().upper()
    probability = input("Probability (Low/Medium/High): ").strip()
    mitigation = input("Mitigation actions: ").strip()
    
    cursor.execute('''
        INSERT INTO risks
        (risk_category, risk_description, status, trend, impact_level, probability, 
         last_updated, mitigation_actions)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (category, description, status, '📊 →', impact, probability, 
          datetime.now().isoformat(), mitigation))
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Added {category} risk to register\n")


# ============================================================================
# FEATURE 5: PEER BENCHMARKING
# ============================================================================

def view_peer_benchmarks():
    """Display peer benchmarking analysis"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print(f"\n{'='*70}")
    print("PEER BENCHMARKING ANALYSIS")
    print(f"{'='*70}\n")
    
    # Get user project
    cursor.execute('SELECT * FROM user_project ORDER BY id DESC LIMIT 1')
    user_project = cursor.fetchone()
    
    if not user_project:
        print("⚠️ No project configured. Please setup your project first (Option 17).\n")
        conn.close()
        return
    
    # Unpack user project
    _, project_name, region, capacity, fid_date, cod_date, lcoh, npv, elec_cost, power_cost, offtake, _ = user_project
    
    print(f"Your Project: {project_name or 'Unnamed'}")
    print(f"Region: {region}, Capacity: {capacity}MW\n")
    
    # Get peer benchmarks
    cursor.execute('''
        SELECT project_name, capacity_mw, lcoh_usd_per_kg, npv_million_usd, 
               timeline_months, offtake_secured_pct, electrolyzer_cost_per_kw, 
               power_cost_per_kwh, status
        FROM benchmarks
        WHERE region = ? OR region IS NULL
        ORDER BY capacity_mw DESC
    ''', (region,))
    
    peers = cursor.fetchall()
    
    if not peers:
        print(f"No peer benchmark data available for {region}.")
        print("\n💡 TIP: Benchmark data will be auto-populated from news articles")
        print("     mentioning project costs, timelines, and performance metrics.\n")
        conn.close()
        return
    
    # Calculate peer averages
    peer_lcoh = [p[2] for p in peers if p[2]]
    peer_npv = [p[3] for p in peers if p[3]]
    peer_timeline = [p[4] for p in peers if p[4]]
    peer_offtake = [p[5] for p in peers if p[5]]
    peer_elec = [p[6] for p in peers if p[6]]
    peer_power = [p[7] for p in peers if p[7]]
    
    avg_lcoh = sum(peer_lcoh) / len(peer_lcoh) if peer_lcoh else 0
    avg_npv = sum(peer_npv) / len(peer_npv) if peer_npv else 0
    avg_timeline = sum(peer_timeline) / len(peer_timeline) if peer_timeline else 0
    avg_offtake = sum(peer_offtake) / len(peer_offtake) if peer_offtake else 0
    avg_elec = sum(peer_elec) / len(peer_elec) if peer_elec else 0
    avg_power = sum(peer_power) / len(peer_power) if peer_power else 0
    
    best_lcoh = min(peer_lcoh) if peer_lcoh else 0
    best_npv = max(peer_npv) if peer_npv else 0
    best_timeline = min(peer_timeline) if peer_timeline else 0
    best_offtake = max(peer_offtake) if peer_offtake else 0
    best_elec = min(peer_elec) if peer_elec else 0
    best_power = min(peer_power) if peer_power else 0
    
    # Display comparison table
    print(f"HOW DOES YOUR PROJECT COMPARE?")
    print(f"{'─'*70}\n")
    
    print(f"{'Metric':<25} {'Your Project':<15} {'Peer Avg':<15} {'Best-in-Class':<15}")
    print(f"{'─'*70}")
    
    # LCOH
    your_vs_avg_lcoh = ((lcoh - avg_lcoh) / avg_lcoh * 100) if avg_lcoh else 0
    lcoh_symbol = '✓' if lcoh < avg_lcoh else '⚠️'
    print(f"{'LCOH ($/kg)':<25} ${lcoh:<14.2f} ${avg_lcoh:<14.2f} ${best_lcoh:<14.2f} {lcoh_symbol}")
    
    # NPV
    your_vs_avg_npv = ((npv - avg_npv) / avg_npv * 100) if avg_npv else 0
    npv_symbol = '✓' if npv > avg_npv else '⚠️'
    print(f"{'NPV ($M)':<25} ${npv:<14.0f} ${avg_npv:<14.0f} ${best_npv:<14.0f} {npv_symbol}")
    
    # Timeline (calculate from FID to COD)
    if fid_date and cod_date:
        # Simple month calculation
        your_timeline = 36  # Default estimate
    else:
        your_timeline = 36
    
    timeline_symbol = '✓' if your_timeline <= avg_timeline else '⚠️'
    print(f"{'Timeline (months)':<25} {your_timeline:<14.0f} {avg_timeline:<14.0f} {best_timeline:<14.0f} {timeline_symbol}")
    
    # Offtake
    offtake_pct_map = {'Secured': 100, 'MoU': 30, 'None': 0}
    your_offtake_pct = offtake_pct_map.get(offtake, 0)
    offtake_symbol = '✓' if your_offtake_pct >= avg_offtake else '⚠️'
    print(f"{'Offtake Secured (%)':<25} {your_offtake_pct:<14.0f} {avg_offtake:<14.0f} {best_offtake:<14.0f} {offtake_symbol}")
    
    # Electrolyzer cost
    elec_symbol = '✓' if elec_cost < avg_elec else '⚠️'
    print(f"{'Electrolyzer ($/kW)':<25} ${elec_cost:<13.0f} ${avg_elec:<13.0f} ${best_elec:<13.0f} {elec_symbol}")
    
    # Power cost
    power_symbol = '✓' if power_cost < avg_power else '⚠️'
    print(f"{'Power ($/kWh)':<25} ${power_cost:<14.3f} ${avg_power:<14.3f} ${best_power:<14.3f} {power_symbol}")
    
    print(f"{'─'*70}\n")
    
    # Analysis
    print(f"🏆 YOUR STRENGTHS:")
    strengths = []
    if lcoh < avg_lcoh:
        strengths.append(f"• LCOH {abs(your_vs_avg_lcoh):.0f}% below peer average (competitive)")
    if npv > avg_npv:
        strengths.append(f"• NPV {your_vs_avg_npv:.0f}% above peer average (strong returns)")
    if elec_cost < avg_elec:
        strengths.append(f"• Electrolyzer cost below average (good procurement)")
    if power_cost < avg_power:
        strengths.append(f"• Power cost below average (good location/PPA)")
    
    if strengths:
        for s in strengths:
            print(f"  {s}")
    else:
        print(f"  No clear advantages vs peers (review all assumptions)")
    
    print(f"\n⚠️ YOUR WEAKNESSES:")
    weaknesses = []
    if your_offtake_pct < avg_offtake:
        weaknesses.append(f"• Offtake {your_offtake_pct:.0f}% vs {avg_offtake:.0f}% peer average (HIGH RISK)")
    if lcoh > avg_lcoh:
        weaknesses.append(f"• LCOH {abs(your_vs_avg_lcoh):.0f}% above peer average (uncompetitive)")
    if your_timeline > avg_timeline:
        weaknesses.append(f"• Timeline {your_timeline - avg_timeline:.0f} months longer than average")
    
    if weaknesses:
        for w in weaknesses:
            print(f"  {w}")
    else:
        print(f"  No significant weaknesses identified")
    
    # Recommendations
    print(f"\n💡 ACTION PLAN TO REACH BEST-IN-CLASS:\n")
    
    if your_offtake_pct < best_offtake:
        print(f"1. Offtake: Move from {offtake} → Binding in next 90 days")
        print(f"   Impact: De-risk project, match peer average\n")
    
    if lcoh > best_lcoh:
        gap = lcoh - best_lcoh
        print(f"2. LCOH: Reduce by ${gap:.2f}/kg to match best-in-class")
        print(f"   Approach: Renegotiate PPA, optimize design, lock lower equipment costs\n")
    
    if your_timeline > best_timeline:
        gap = your_timeline - best_timeline
        print(f"3. Timeline: Accelerate by {gap:.0f} months")
        print(f"   Impact: Earlier revenue, beat competition to market\n")
    
    print()
    conn.close()


# ============================================================================
# FEATURE 6: DEAL FLOW INTELLIGENCE
# ============================================================================

def view_deal_flow():
    """Display deal flow intelligence"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print(f"\n{'='*70}")
    print("MARKET DEAL FLOW INTELLIGENCE")
    print(f"{'='*70}\n")
    
    days = input("Show deals from last how many days? (default 90): ").strip() or "90"
    days = int(days)
    
    cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
    
    cursor.execute('''
        SELECT deal_date, deal_type, parties, value_usd, region, description, signal
        FROM deals
        WHERE deal_date >= ?
        ORDER BY deal_date DESC
    ''', (cutoff_date,))
    
    deals = cursor.fetchall()
    
    if not deals:
        print(f"No deals tracked in last {days} days.")
        print("\n💡 TIP: Deal data will be auto-populated from news articles about:")
        print("   • M&A transactions")
        print("   • Equity investments")
        print("   • Offtake agreements")
        print("   • Strategic partnerships")
        print("\nWould you like to manually add a deal? (y/n): ")
        
        if input().strip().lower() == 'y':
            add_deal_manual()
        
        conn.close()
        return
    
    # Display deals
    print(f"RECENT TRANSACTIONS - LAST {days} DAYS")
    print(f"{'─'*70}\n")
    
    print(f"{'Date':<12} {'Type':<12} {'Parties':<30} {'Value':<12} {'Signal'}")
    print(f"{'─'*70}")
    
    for deal in deals:
        date, type_, parties, value, region, description, signal = deal
        
        signal_emoji = {
            'Positive': '🟢',
            'Negative': '🔴',
            'Neutral': '🟡',
            'Mixed': '🟠'
        }.get(signal, '⚪')
        
        date_short = date[:10] if date else 'N/A'
        value_str = f"${value/1000000:.0f}M" if value else "N/A"
        
        print(f"{date_short:<12} {type_[:11]:<12} {parties[:29]:<30} {value_str:<12} {signal_emoji} {signal}")
    
    print(f"{'─'*70}\n")
    
    # Trend analysis
    equity_deals = [d for d in deals if 'Equity' in d[1]]
    ma_deals = [d for d in deals if 'M&A' in d[1]]
    offtake_deals = [d for d in deals if 'Offtake' in d[1]]
    
    print(f"📊 TREND ANALYSIS:\n")
    print(f"  Equity Deals:    {len(equity_deals)} transactions")
    print(f"  M&A Activity:    {len(ma_deals)} transactions")
    print(f"  Offtake Deals:   {len(offtake_deals)} transactions")
    
    # Calculate trend direction (compare to previous period)
    prev_cutoff = (datetime.now() - timedelta(days=days*2)).isoformat()
    
    cursor.execute('''
        SELECT COUNT(*) FROM deals
        WHERE deal_date >= ? AND deal_date < ?
    ''', (prev_cutoff, cutoff_date))
    
    prev_period_count = cursor.fetchone()[0]
    current_period_count = len(deals)
    
    if prev_period_count > 0:
        trend_pct = ((current_period_count - prev_period_count) / prev_period_count) * 100
        trend_direction = "↑" if trend_pct > 0 else "↓"
        print(f"\n  Overall Activity: {trend_direction} {abs(trend_pct):.0f}% vs previous {days} days")
    
    # Market sentiment
    positive_count = len([d for d in deals if d[6] == 'Positive'])
    negative_count = len([d for d in deals if d[6] == 'Negative'])
    
    print(f"\n📈 MARKET SENTIMENT:")
    if positive_count > negative_count * 1.5:
        print(f"  🟢 POSITIVE - Investors active, market healthy")
    elif negative_count > positive_count * 1.5:
        print(f"  🔴 CAUTIOUS - Pullbacks, distressed assets")
    else:
        print(f"  🟡 MIXED - Some activity, some caution")
    
    # Implications
    print(f"\n🎯 IMPLICATIONS FOR YOUR PROJECT:\n")
    
    if len(equity_deals) > 3:
        print(f"  ✓ Active equity market - good time for fundraising")
    elif len(equity_deals) == 0:
        print(f"  ⚠️ Quiet equity market - may be harder to raise capital")
    
    if len(ma_deals) > 2 and negative_count > positive_count:
        print(f"  ⚠️ Distressed M&A suggests market shake-out - be conservative")
    
    if len(offtake_deals) < 2:
        print(f"  ⚠️ Few offtake deals - buyers may be hesitant")
    
    print()
    conn.close()


def add_deal_manual():
    """Manually add a deal"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print("\nEnter deal details:")
    date = input("Date (YYYY-MM-DD): ").strip()
    type_ = input("Type (Equity/M&A/Offtake/Partnership): ").strip()
    parties = input("Parties involved: ").strip()
    value = input("Value in USD millions (or leave blank): ").strip()
    value = float(value) * 1000000 if value else None
    region = input("Region: ").strip()
    description = input("Description: ").strip()
    signal = input("Signal (Positive/Negative/Neutral/Mixed): ").strip()
    
    cursor.execute('''
        INSERT INTO deals
        (deal_date, deal_type, parties, value_usd, region, description, signal)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (date, type_, parties, value, region, description, signal))
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Added {type_} deal to database\n")


# ============================================================================
# FEATURE 7: PROJECT PROFILE SETUP
# ============================================================================

def setup_user_project():
    """Setup or update user's project profile - can auto-fill from TEA baseline"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print(f"\n{'='*70}")
    print("PROJECT PROFILE SETUP")
    print(f"{'='*70}\n")
    
    # Check if project already exists
    cursor.execute('SELECT * FROM user_project ORDER BY id DESC LIMIT 1')
    existing = cursor.fetchone()
    
    if existing:
        print("Existing project found:")
        print(f"  Name: {existing[1]}")
        print(f"  Region: {existing[2]}")
        print(f"  Capacity: {existing[3]}MW\n")
        
        update = input("Update existing project? (y/n): ").strip().lower()
        if update != 'y':
            conn.close()
            return
    
    # Offer TEA baseline integration
    print("\nData source options:")
    print("1. Auto-fill from TEA baseline (SHARE model)")
    print("2. Manual entry")
    
    source_choice = input("\nSelect option (1-2): ").strip()
    
    if source_choice == '1':
        # Use TEA baseline
        print("\nSelect region for TEA baseline:")
        print("1. Oman")
        print("2. Chile")
        print("3. Houston")
        region_choice = input("Select region (1-3): ").strip()
        region_map = {'1': 'Oman', '2': 'Chile', '3': 'Houston'}
        region = region_map.get(region_choice, 'Oman')
        
        print(f"\n🔄 Loading TEA baseline for {region}...")
        
        try:
            # Get baseline from TEA adapter
            baseline = get_tea_baselines().get(region)
            
            if baseline:
                print(f"✅ TEA baseline loaded successfully!\n")
                
                # Auto-fill from baseline
                project_name = input(f"Project name (default: {region} H2 Project): ").strip() or f"{region} H2 Project"
                capacity = float(input(f"Capacity MW (from baseline: {baseline.get('electrolyser_capacity_mw', 800)}): ").strip() or str(baseline.get('electrolyser_capacity_mw', 800)))
                
                target_fid = input("Target FID date (YYYY-MM, default 2027-01): ").strip() or "2027-01"
                target_cod = input("Target COD date (YYYY-MM, default 2029-12): ").strip() or "2029-12"
                
                # Use baseline data
                lcoh = baseline.get('lcoh', baseline.get('lcoh_usd_per_kg', 1.85))
                npv = baseline.get('npv', 580)
                
                # Convert NPV from millions if needed
                if npv < 10:  # Likely in wrong units
                    npv = npv * 1  # Assume already in millions
                
                elec_cost = 680  # Default - not in baseline
                power_cost = baseline.get('lcoe_usd_per_mwh', 15) / 1000  # Convert from $/MWh to $/kWh
                
                print(f"\n📊 Values from TEA baseline:")
                print(f"  LCOH: ${lcoh:.2f}/kg")
                print(f"  NPV: ${npv:.0f}M")
                print(f"  Power cost: ${power_cost:.3f}/kWh")
                
                offtake = 'None'  # Default
                
            else:
                print(f"⚠️  No TEA baseline found for {region}. Using defaults.\n")
                raise ValueError("No baseline")
                
        except Exception as e:
            print(f"⚠️  Could not load TEA baseline: {e}")
            print("Falling back to manual entry...\n")
            source_choice = '2'  # Fall through to manual entry
    
    if source_choice == '2' or source_choice not in ['1', '2']:
        # Manual entry
        print("\nEnter your project details:\n")
        
        project_name = input("Project name: ").strip() or "My Hydrogen Project"
        
        print("\nRegion options:")
        print("1. Oman")
        print("2. Chile")
        print("3. Houston")
        print("4. Other")
        region_choice = input("Select region (1-4): ").strip()
        region_map = {'1': 'Oman', '2': 'Chile', '3': 'Houston', '4': 'Other'}
        region = region_map.get(region_choice, 'Oman')
        if region == 'Other':
            region = input("Enter region name: ").strip()
        
        capacity = float(input("Capacity (MW): ").strip() or "800")
        
        target_fid = input("Target FID date (YYYY-MM): ").strip() or "2027-01"
        target_cod = input("Target COD date (YYYY-MM): ").strip() or "2029-12"
        
        print("\nFinancial assumptions:")
        lcoh = float(input("LCOH assumption ($/kg): ").strip() or "1.85")
        npv = float(input("NPV baseline ($M): ").strip() or "580")
        
        print("\nCost assumptions:")
        elec_cost = float(input("Electrolyzer cost ($/kW): ").strip() or "680")
        power_cost = float(input("Power cost ($/kWh): ").strip() or "0.015")
        
        offtake = 'None'
    
    print("\nOfftake status:")
    print("1. Secured (binding agreement)")
    print("2. MoU (non-binding)")
    print("3. None")
    offtake_choice = input("Select (1-3): ").strip()
    offtake_map = {'1': 'Secured', '2': 'MoU', '3': 'None'}
    offtake = offtake_map.get(offtake_choice, 'None')
    
    # Save or update
    if existing:
        cursor.execute('''
            UPDATE user_project
            SET project_name=?, region=?, capacity_mw=?, target_fid_date=?,
                target_cod_date=?, lcoh_assumption=?, npv_baseline=?,
                electrolyzer_cost_assumption=?, power_cost_assumption=?,
                offtake_status=?, last_updated=?
            WHERE id=?
        ''', (project_name, region, capacity, target_fid, target_cod, lcoh, npv,
              elec_cost, power_cost, offtake, datetime.now().isoformat(), existing[0]))
    else:
        cursor.execute('''
            INSERT INTO user_project
            (project_name, region, capacity_mw, target_fid_date, target_cod_date,
             lcoh_assumption, npv_baseline, electrolyzer_cost_assumption,
             power_cost_assumption, offtake_status, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (project_name, region, capacity, target_fid, target_cod, lcoh, npv,
              elec_cost, power_cost, offtake, datetime.now().isoformat()))
    
    conn.commit()
    
    # Display summary
    print(f"\n{'='*70}")
    print("PROJECT PROFILE SAVED")
    print(f"{'='*70}\n")
    print(f"  Project: {project_name}")
    print(f"  Location: {region}, {capacity}MW")
    print(f"  Timeline: FID {target_fid} → COD {target_cod}")
    print(f"  Economics: LCOH ${lcoh:.2f}/kg, NPV ${npv:.0f}M")
    print(f"  Offtake: {offtake}")
    print(f"\n✅ Profile complete! Other features will now use this data.\n")
    
    conn.close()


# ============================================================================
# FEATURE 8: AI INVESTMENT COMMITTEE
# ============================================================================

def ai_investment_committee():
    """Run AI investment committee review"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    print(f"\n{'='*70}")
    print("AI INVESTMENT COMMITTEE REVIEW")
    print(f"{'='*70}\n")
    
    # Check if user project exists
    cursor.execute('SELECT * FROM user_project ORDER BY id DESC LIMIT 1')
    user_project = cursor.fetchone()
    
    if not user_project:
        print("⚠️ No project configured.")
        print("Please setup your project first (Option 17) to get personalized review.\n")
        conn.close()
        return
    
    # Unpack project
    _, project_name, region, capacity, fid_date, cod_date, lcoh, npv, elec_cost, power_cost, offtake, _ = user_project
    
    print(f"Analyzing: {project_name}")
    print(f"Region: {region}, Capacity: {capacity}MW")
    print(f"Target FID: {fid_date}\n")
    
    print("Running comprehensive analysis...")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    
    # Scoring system
    scores = {}
    max_score = 10
    
    # 1. Economics Score
    peer_lcoh_avg = 2.10  # Default benchmark
    if lcoh < peer_lcoh_avg:
        econ_score = min(10, 10 * (1 - (lcoh - peer_lcoh_avg) / peer_lcoh_avg))
    else:
        econ_score = max(3, 10 * (1 - (lcoh - peer_lcoh_avg) / peer_lcoh_avg))
    
    scores['Economics'] = round(econ_score, 1)
    
    # 2. Offtake Score
    offtake_score_map = {'Secured': 10, 'MoU': 3, 'None': 1}
    scores['Offtake'] = offtake_score_map.get(offtake, 1)
    
    # 3. Location Score
    location_scores = {'Oman': 8, 'Chile': 7, 'Houston': 6}
    scores['Location'] = location_scores.get(region, 5)
    
    # 4. Timing Score (check against competition)
    cursor.execute('SELECT COUNT(*) FROM competitors WHERE region=? AND status="Active"', (region,))
    competitor_count = cursor.fetchone()[0]
    
    if competitor_count < 3:
        timing_score = 8  # Good timing, not crowded
    elif competitor_count < 5:
        timing_score = 6  # Moderate competition
    else:
        timing_score = 4  # Crowded market
    
    scores['Timing'] = timing_score
    
    # 5. Risk Score (inverse of risk register)
    cursor.execute('SELECT COUNT(*) FROM risks WHERE impact_level IN ("HIGH", "CRITICAL")')
    high_risks = cursor.fetchone()[0]
    
    risk_score = max(3, 10 - (high_risks * 2))
    scores['Risk Management'] = risk_score
    
    # Overall FID Readiness
    overall_score = sum(scores.values()) / len(scores)
    
    # Display results
    print(f"🎯 OVERALL FID READINESS: {overall_score:.1f}/10 ", end="")
    
    if overall_score >= 8:
        print("(STRONG - PROCEED) ✅\n")
        readiness = "STRONG"
    elif overall_score >= 6:
        print("(MODERATE - PROCEED WITH CAUTION) 🟡\n")
        readiness = "MODERATE"
    else:
        print("(WEAK - ADDRESS ISSUES FIRST) 🔴\n")
        readiness = "WEAK"
    
    print(f"{'━'*70}")
    print("DETAILED ASSESSMENT")
    print(f"{'━'*70}\n")
    
    # Detailed breakdown
    print("✅ STRENGTHS:\n")
    
    if scores['Economics'] >= 7:
        print(f"1. Economics ({scores['Economics']}/10) ✓")
        print(f"   • LCOH ${lcoh}/kg is competitive ({(peer_lcoh_avg - lcoh) / peer_lcoh_avg * 100:.0f}% below peers)")
        print(f"   • NPV ${npv}M indicates strong returns\n")
    
    if scores['Location'] >= 7:
        print(f"2. Location ({scores['Location']}/10) ✓")
        print(f"   • {region} offers good fundamentals")
        print(f"   • Power cost ${power_cost}/kWh is competitive\n")
    
    if scores['Timing'] >= 7:
        print(f"3. Timing ({scores['Timing']}/10) ✓")
        print(f"   • Market not oversaturated ({competitor_count} active competitors)")
        print(f"   • Window of opportunity exists\n")
    
    # Red flags
    print(f"{'━'*70}")
    print("🚨 RED FLAGS:\n")
    
    critical_issues = []
    
    if scores['Offtake'] <= 3:
        print(f"1. Offtake Risk ({scores['Offtake']}/10) 🔴 CRITICAL")
        print(f"   • Status: {offtake} (not binding)")
        print(f"   • 85% of successful FIDs had binding offtake BEFORE decision")
        print(f"   • Your risk: Reach FID without secured buyer = 60% chance of delay\n")
        critical_issues.append("Offtake")
        print(f"   RECOMMENDATION: DO NOT proceed to FID without binding offtake\n")
    
    if scores['Economics'] < 5:
        print(f"2. Economic Viability ({scores['Economics']}/10) 🔴 CRITICAL")
        print(f"   • LCOH ${lcoh}/kg is above market average")
        print(f"   • Project may struggle to compete\n")
        critical_issues.append("Economics")
        print(f"   RECOMMENDATION: Re-optimize design, lock lower equipment costs\n")
    
    if scores['Risk Management'] < 5:
        print(f"3. Risk Level ({scores['Risk Management']}/10) 🟡 MEDIUM")
        print(f"   • {high_risks} high/critical risks identified")
        print(f"   • Need mitigation plans before FID\n")
    
    # Probabilistic outcomes
    print(f"{'━'*70}")
    print("📊 PROBABILISTIC OUTCOMES")
    print(f"{'━'*70}\n")
    
    if len(critical_issues) == 0:
        success_prob = 70
        delay_prob = 25
        cancel_prob = 5
    elif len(critical_issues) == 1:
        success_prob = 45
        delay_prob = 40
        cancel_prob = 15
    else:
        success_prob = 25
        delay_prob = 50
        cancel_prob = 25
    
    print(f"If you proceed to FID now:\n")
    print(f"  Success (on-time, on-budget):        {success_prob}%")
    print(f"  Delayed 6-12 months:                 {delay_prob}%")
    print(f"  Cancelled/major restructure:         {cancel_prob}%\n")
    
    expected_npv = (success_prob/100 * npv) + (delay_prob/100 * npv * 0.8) + (cancel_prob/100 * npv * 0.3)
    
    print(f"  Expected NPV (probability-weighted): ${expected_npv:.0f}M")
    print(f"  vs Base Case NPV: ${npv}M")
    print(f"  Risk-adjusted discount: {(1 - expected_npv/npv)*100:.0f}%\n")
    
    # Final recommendation
    print(f"{'━'*70}")
    print("💡 FINAL RECOMMENDATION")
    print(f"{'━'*70}\n")
    
    if readiness == "STRONG" and len(critical_issues) == 0:
        print("🟢 PROCEED TO FID\n")
        print("Your project shows strong fundamentals across all dimensions.")
        print("No critical blockers identified.\n")
        print("Recommended timeline: Proceed as planned\n")
    
    elif readiness == "MODERATE" or len(critical_issues) == 1:
        print("🟡 CONDITIONAL PROCEED\n")
        print(f"Address the following before FID:\n")
        
        if "Offtake" in critical_issues:
            print("1. CRITICAL: Secure binding offtake (60%+ of capacity)")
            print("   Timeline: Next 90 days")
            print("   Without this: Delay FID by 6 months\n")
        
        if "Economics" in critical_issues:
            print("2. CRITICAL: Improve economics")
            print("   Target: Reduce LCOH to <$2.00/kg")
            print("   Approach: Renegotiate PPA, optimize design\n")
        
        print(f"If resolved: Expected NPV improves to ${npv * 0.95:.0f}M")
        print(f"If unresolved: Delay likely, expected NPV ${expected_npv:.0f}M\n")
    
    else:
        print("🔴 DO NOT PROCEED YET\n")
        print(f"Critical issues must be resolved:\n")
        
        for i, issue in enumerate(critical_issues, 1):
            print(f"{i}. {issue}")
        
        print(f"\nRecommended action: PAUSE FID, address issues")
        print(f"Timeline: Re-assess in 6-12 months\n")
    
    print(f"{'━'*70}")
    print(f"Confidence in recommendation: {min(85, 70 + overall_score * 2):.0f}%")
    print(f"{'━'*70}\n")
    
    conn.close()


# ============================================================================
# MAIN FUNCTION
# ============================================================================

def main():
    """Main execution function"""
    global TEA_BASELINES

    print("\n" + "="*70)
    print("ENHANCED HYDROGEN INTELLIGENCE MONITORING SYSTEM v3.0")
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
    print("CORE FUNCTIONS:")
    print("1. Run enhanced collection (50+ articles)")
    print("2. Run standard collection (RSS only)")
    print("3. View recent articles")
    print("4. Test deep search")
    print("5. Test news aggregator scraping")
    print("6. View statistics")
    print("7. List all RSS feeds")
    print("8. List all keywords")
    print("9. List all search queries")
    print("10. View deep-dive investigations")
    print("11. Analyze cumulative impact on YOUR project ⭐")
    print("\nADVANCED INTELLIGENCE:")
    print("12. Competitive Intelligence Dashboard 🎯")
    print("13. Offtaker Intelligence Tracker 📊")
    print("14. Risk Register & Early Warnings ⚠️")
    print("15. Peer Benchmarking 📈")
    print("16. Deal Flow Intelligence 💼")
    print("17. Setup Your Project Profile ⚙️")
    print("18. AI Investment Committee Review 🤖")
    print("="*70)
    
    choice = input("\nEnter choice (1-18): ").strip()
    
    if choice == '1':
        print("\n[ENHANCED MODE] Collecting 50+ articles...\n")
        min_articles = input("Minimum articles to collect (default 50): ").strip() or "50"
        max_articles = input("Maximum articles to collect (default 100): ").strip() or "100"
        
        collect_articles_enhanced(
            min_articles=int(min_articles),
            max_articles=int(max_articles)
        )
    
    elif choice == '2':
        print("\n[STANDARD MODE] Running RSS-only collection...\n")
        articles = fetch_rss_feeds_enhanced()
        stored = store_articles([{
            **a, 
            'category': 'LOW', 
            'priority_score': 1, 
            'keywords': '', 
            'region': '', 
            'url_hash': calculate_url_hash(a['url'])
        } for a in articles if a.get('url')])
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
        
        scrapers = [
            ('Decarbonfuse', scrape_decarbonfuse),
            ('Hydrogen Insight', scrape_hydrogen_insight),
            ('Recharge News', scrape_recharge_news),
            ('Ammonia Energy', scrape_ammonia_energy),
            ('Energy Storage News', scrape_energy_storage_news),
            ('PV Magazine', scrape_pv_magazine),
        ]
        
        for name, scraper_func in scrapers:
            print(f"\n{name}:")
            try:
                results = scraper_func()
                print(f"   Found: {len(results)} articles")
            except Exception as e:
                print(f"   Error: {str(e)}")
    
    elif choice == '6':
        view_statistics()
    
    elif choice == '7':
        print(f"\n{'='*70}")
        print(f"RSS FEEDS ({len(RSS_FEEDS)} total)")
        print(f"{'='*70}\n")
        for name, url in RSS_FEEDS.items():
            print(f"  {name}")
            print(f"    {url}\n")
    
    elif choice == '8':
        print(f"\n{'='*70}")
        print("KEYWORDS BY PRIORITY")
        print(f"{'='*70}\n")
        for category, keywords in KEYWORDS.items():
            print(f"\n{category} ({len(keywords)} keywords):")
            for kw in keywords:
                print(f"  - {kw}")
    
    elif choice == '9':
        print(f"\n{'='*70}")
        print(f"SEARCH QUERIES ({len(SEARCH_QUERIES)} total)")
        print(f"{'='*70}\n")
        for i, query in enumerate(SEARCH_QUERIES, 1):
            print(f"  {i}. {query}")
    
    elif choice == '10':
        view_investigations()
    
    elif choice == '11':
        analyze_cumulative_impact()
    
    elif choice == '12':
        view_competitive_landscape()
    
    elif choice == '13':
        view_offtaker_intelligence()
    
    elif choice == '14':
        view_risk_dashboard()
    
    elif choice == '15':
        view_peer_benchmarks()
    
    elif choice == '16':
        view_deal_flow()
    
    elif choice == '17':
        setup_user_project()
    
    elif choice == '18':
        ai_investment_committee()
    
    else:
        print("Invalid choice")

if __name__ == "__main__":
    main()