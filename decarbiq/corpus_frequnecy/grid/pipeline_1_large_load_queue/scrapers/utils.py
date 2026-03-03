"""
Shared utilities for Pipeline 1.
- Rate-limited HTTP fetcher
- PDF text extraction
- Relevance scoring against H2/ammonia/CCS keywords
- Content hashing for deduplication
- Snapshot store (JSON-based, tracks what we've already seen)
"""

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

from config.settings import (
    REQUEST_HEADERS, REQUEST_TIMEOUT, RATE_LIMIT_SECONDS,
    PRIMARY_KEYWORDS, SECONDARY_KEYWORDS, STORAGE_DIR, ScrapeResult,
)

# ── Rate-Limited Fetcher ─────────────────────────────────────────────────────

_last_request_time: dict[str, float] = {}


def fetch_url(url: str, as_bytes: bool = False, timeout: int = REQUEST_TIMEOUT) -> requests.Response | None:
    """Fetch URL with rate limiting per domain and error handling."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc

    now = time.time()
    if domain in _last_request_time:
        elapsed = now - _last_request_time[domain]
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)

    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout,
                            allow_redirects=True)
        _last_request_time[domain] = time.time()
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        print(f"  [WARN] Failed to fetch {url}: {e}")
        return None


def fetch_page_soup(url: str) -> BeautifulSoup | None:
    """Fetch HTML page and return parsed BeautifulSoup."""
    resp = fetch_url(url)
    if resp is None:
        return None
    return BeautifulSoup(resp.text, "lxml")


def download_file(url: str, dest_dir: Path, filename: str = None) -> Path | None:
    """Download file to dest_dir, return local path."""
    resp = fetch_url(url, as_bytes=True)
    if resp is None:
        return None
    if filename is None:
        filename = url.split("/")[-1].split("?")[0]
    # Sanitize filename
    filename = re.sub(r'[^\w\-.]', '_', filename)[:200]
    dest = dest_dir / filename
    dest.write_bytes(resp.content)
    return dest


# ── PDF Text Extraction ──────────────────────────────────────────────────────

def extract_pdf_text(pdf_path: Path, max_pages: int = 50) -> str:
    """Extract text from PDF using pdfplumber."""
    if not HAS_PDFPLUMBER:
        print("  [WARN] pdfplumber not available, skipping PDF extraction")
        return ""
    try:
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages[:max_pages]):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)
    except Exception as e:
        print(f"  [WARN] PDF extraction failed for {pdf_path}: {e}")
        return ""


# ── Content Hashing ──────────────────────────────────────────────────────────

def content_hash(content: str | bytes) -> str:
    """SHA-256 hash of content for deduplication."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:16]


# ── Relevance Scoring ────────────────────────────────────────────────────────

def score_relevance(text: str) -> tuple[float, list[str]]:
    """
    Score text relevance to blue H2/ammonia/CCS topics.
    Returns (score 0-1, list of matched keywords).
    
    Scoring:
    - Primary keyword match: 0.15 each (capped contribution 0.75)
    - Secondary keyword match: 0.05 each (capped contribution 0.25)
    - Final score capped at 1.0
    """
    text_lower = text.lower()
    matched = []

    primary_score = 0.0
    for kw in PRIMARY_KEYWORDS:
        if kw.lower() in text_lower:
            matched.append(kw)
            primary_score += 0.15
    primary_score = min(primary_score, 0.75)

    secondary_score = 0.0
    for kw in SECONDARY_KEYWORDS:
        if kw.lower() in text_lower:
            matched.append(kw)
            secondary_score += 0.05
    secondary_score = min(secondary_score, 0.25)

    return min(primary_score + secondary_score, 1.0), matched


# ── Snapshot Store (deduplication across runs) ───────────────────────────────

SNAPSHOT_FILE = STORAGE_DIR / "snapshots" / "seen_hashes.json"


def load_seen_hashes() -> dict[str, str]:
    """Load previously seen content hashes. Returns {hash: first_seen_iso}."""
    if SNAPSHOT_FILE.exists():
        return json.loads(SNAPSHOT_FILE.read_text())
    return {}


def save_seen_hashes(hashes: dict[str, str]):
    """Persist seen hashes."""
    SNAPSHOT_FILE.write_text(json.dumps(hashes, indent=2))


def is_new_content(h: str) -> bool:
    """Check if we've seen this content hash before."""
    seen = load_seen_hashes()
    return h not in seen


def mark_seen(h: str):
    """Mark a content hash as seen."""
    seen = load_seen_hashes()
    if h not in seen:
        seen[h] = datetime.now(timezone.utc).isoformat()
        save_seen_hashes(seen)


# ── Link Extraction Helpers ──────────────────────────────────────────────────

def extract_pdf_links(soup: BeautifulSoup, base_url: str = "https://www.ercot.com") -> list[dict]:
    """Extract all PDF links from a page, returning list of {url, text}."""
    links = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if href.lower().endswith(".pdf"):
            full_url = href if href.startswith("http") else base_url + href
            links.append({
                "url": full_url,
                "text": a_tag.get_text(strip=True),
            })
    return links


def extract_all_links(soup: BeautifulSoup, base_url: str = "https://www.ercot.com",
                       extensions: list[str] = None) -> list[dict]:
    """Extract links, optionally filtering by file extension."""
    links = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        full_url = href if href.startswith("http") else base_url + href
        if extensions:
            if not any(full_url.lower().endswith(ext) for ext in extensions):
                continue
        links.append({
            "url": full_url,
            "text": a_tag.get_text(strip=True),
        })
    return links


# ── Output Formatting ────────────────────────────────────────────────────────

def results_to_json(results: list[ScrapeResult], filepath: Path):
    """Write scrape results to JSON."""
    from dataclasses import asdict
    data = [asdict(r) for r in results]
    filepath.write_text(json.dumps(data, indent=2, default=str))
    print(f"  [OK] Wrote {len(data)} results to {filepath}")


def results_to_dataframe(results: list[ScrapeResult]):
    """Convert results to pandas DataFrame."""
    import pandas as pd
    from dataclasses import asdict
    return pd.DataFrame([asdict(r) for r in results])
