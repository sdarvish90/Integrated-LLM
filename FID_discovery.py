#!/usr/bin/env python3
"""
FID Signal Discovery (US-first, then Global) — Tier 1/2/3

Goal
-----
Discover PUBLIC "FID signal" artifacts you can later ingest & score:
  Tier 1 (weak): interconnection request / queue entries, early filings
  Tier 2 (medium): studies completed, IA executed, major permits issued
  Tier 3 (strong): EPC awards, offtake agreements, grant/award finalized, explicit FID PR

This script does DISCOVERY + SNAPSHOT (downloads files/pages), NOT “LLM training”.

US sources implemented (public):
- CAISO public queue report (xlsx)
- MISO GI queue (downloads spreadsheet link from page)
- ERCOT GIS report (xlsx public data product)
- FERC eCollection RSS feed (accepted filings; monthly backfill)
- EPA ECHO Web Services (permits/compliance; API discovery hook)
- USAspending API (award search; API discovery hook)

Global: stubs you can extend by adding adapters.

Outputs
--------
state/fid_discovery_state.json
state/fid_discovery_plan.json
state/fid_discovery_graph.jsonl
data/raw/fid_signals/... (html/xlsx/xml/json)
data/curated/fid_signals/catalog.jsonl   (normalized discovered items)

Notes
-----
- This is designed for incremental daily/weekly runs.
- Tomorrow’s run will skip unchanged items based on hashes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Iterable, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

TOOL_VERSION = "0.1.0"

# ----------------------------
# Paths
# ----------------------------
STATE_DIR = Path("state")
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw" / "fid_signals"
CURATED_DIR = DATA_DIR / "curated" / "fid_signals"

STATE_PATH = STATE_DIR / "fid_discovery_state.json"
PLAN_PATH = STATE_DIR / "fid_discovery_plan.json"
GRAPH_PATH = STATE_DIR / "fid_discovery_graph.jsonl"
CATALOG_PATH = CURATED_DIR / "catalog.jsonl"
LOG_DIR = Path("logs")
LOG_PATH = LOG_DIR / "fid_signal_discovery.log"

# ----------------------------
# Defaults
# ----------------------------
DEFAULT_KEYWORDS = [
    "hydrogen", "electrolyzer", "renewable", "wind", "solar",
    "ppa", "offtake", "interconnection", "queue", "study",
    "agreement", "permit", "air permit", "water permit",
    "EPC", "notice to proceed", "NTP", "final investment decision", "FID",
    "DOE", "grant", "award"
]

REQUEST_SLEEP = 0.20
HTTP_TIMEOUT = 60
REFRESH_STALE_DAYS = 3
MAX_DOWNLOADS_PER_RUN = 350

# ----------------------------
# Utilities
# ----------------------------
def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logging.getLogger("").addHandler(console)

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def pace():
    time.sleep(REQUEST_SLEEP)

def safe_slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:180] if len(s) > 180 else s

def in_date_window(dt: Optional[datetime], start: datetime, end: datetime) -> bool:
    if dt is None:
        return True  # unknown date: keep, score later
    return start <= dt <= end

def parse_date_guess(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    fmts = [
        "%Y-%m-%d",
        "%b %d, %Y",
        "%B %d, %Y",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None

def keyword_score(text: str, keywords: List[str]) -> float:
    t = (text or "").lower()
    score = 0.0
    for kw in keywords:
        kw = kw.lower().strip()
        if not kw:
            continue
        if kw in t:
            score += 4.5
        toks = [x for x in re.split(r"\s+", kw) if x]
        for tok in toks:
            score += len(re.findall(rf"\b{re.escape(tok)}\b", t)) * 1.8
    return score

def http_get(url: str, headers: Optional[dict] = None, max_retries: int = 6) -> requests.Response:
    backoff = 1.7
    last_exc = None
    for i in range(max_retries):
        try:
            r = requests.get(url, headers=headers or {}, timeout=HTTP_TIMEOUT)
            if r.status_code in (429, 502, 503):
                time.sleep(backoff)
                backoff *= 2
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            if i == max_retries - 1:
                raise
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"HTTP failed: {last_exc}")

def write_raw(kind_slug: str, filename: str, content: bytes) -> Path:
    dt = datetime.now(timezone.utc)
    out_dir = RAW_DIR / kind_slug / f"{dt:%Y}" / f"{dt:%m}" / f"{dt:%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / filename
    p.write_bytes(content)
    return p

def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

# ----------------------------
# State
# ----------------------------
def load_state() -> Dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        return {
            "tool_version": TOOL_VERSION,
            "last_run_utc": None,
            "seen": {}  # url -> {sha256, last_checked_utc, first_seen_utc, meta...}
        }
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))

def save_state(st: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_PATH)

def is_stale(rec: Dict[str, Any]) -> bool:
    ts = rec.get("last_checked_utc")
    if not ts:
        return True
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return True
    return (datetime.now(timezone.utc) - dt) > timedelta(days=REFRESH_STALE_DAYS)

# ----------------------------
# Discovery item model
# ----------------------------
@dataclass
class DiscoveredItem:
    url: str
    title: str
    source: str
    tier: int                 # 1/2/3
    signal_type: str          # e.g., interconnection_queue, permit, award, filing, press_release
    published_utc: Optional[str]
    score: float
    content_type: Optional[str] = None
    raw_path: Optional[str] = None
    sha256: Optional[str] = None

# ----------------------------
# Source adapter interface
# ----------------------------
class SourceAdapter:
    name: str
    scope: str  # "us" or "global"

    def discover(self, start: datetime, end: datetime, keywords: List[str]) -> List[DiscoveredItem]:
        raise NotImplementedError

# ----------------------------
# US: Interconnection queue sources
# ----------------------------
class CAISOQueueAdapter(SourceAdapter):
    """
    CAISO publishes a public interconnection queue report as an XLSX.
    Public link: https://www.caiso.com/documents/publicqueuereport.xlsx
    """
    name = "caiso_public_queue_xlsx"
    scope = "us"

    def discover(self, start: datetime, end: datetime, keywords: List[str]) -> List[DiscoveredItem]:
        url = "https://www.caiso.com/documents/publicqueuereport.xlsx"
        title = "CAISO Public Queue Report (XLSX)"
        # Tier mapping: queue entries are Tier 1; if IA milestone columns exist, you can score as Tier 2 later.
        blob = f"{title} interconnection queue generator interconnection agreement study"
        sc = keyword_score(blob, keywords)
        return [DiscoveredItem(
            url=url, title=title, source=self.name, tier=1,
            signal_type="interconnection_queue", published_utc=None, score=sc
        )]

class MISOQueueAdapter(SourceAdapter):
    """
    MISO GI Queue page provides interactive queue and says you can download the complete spreadsheet.
    We discover the download link by parsing the GI Queue page.
    """
    name = "miso_gi_queue"
    scope = "us"

    def discover(self, start: datetime, end: datetime, keywords: List[str]) -> List[DiscoveredItem]:
        page = "https://www.misoenergy.org/planning/resource-utilization/generator-interconnection/"
        r = http_get(page)
        pace()
        soup = BeautifulSoup(r.text, "html.parser")
        found = []

        # find likely spreadsheet links
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href:
                continue
            url = urljoin(page, href)
            txt = (a.get_text(" ", strip=True) or "")
            if any(url.lower().endswith(ext) for ext in (".xlsx", ".xls", ".csv")):
                sc = keyword_score(f"{txt} {url} interconnection queue", keywords)
                found.append(DiscoveredItem(
                    url=url,
                    title=txt or "MISO GI Queue Spreadsheet",
                    source=self.name,
                    tier=1,
                    signal_type="interconnection_queue",
                    published_utc=None,
                    score=sc,
                ))

        # If we didn't detect, keep the page itself (still useful for embedded links)
        if not found:
            sc = keyword_score("MISO generator interconnection queue download spreadsheet", keywords)
            found.append(DiscoveredItem(
                url=page,
                title="MISO Generator Interconnection (GI Queue Page)",
                source=self.name,
                tier=1,
                signal_type="interconnection_queue_page",
                published_utc=None,
                score=sc,
            ))

        return found

class ERCOTGISAdapter(SourceAdapter):
    """
    ERCOT GIS Report is a public data product (xlsx) with interconnection milestones.
    We store the product page; it typically links the latest xlsx.
    """
    name = "ercot_gis_report"
    scope = "us"

    def discover(self, start: datetime, end: datetime, keywords: List[str]) -> List[DiscoveredItem]:
        product_page = "https://www.ercot.com/mp/data-products/data-product-details?id=pg7-200-er"
        title = "ERCOT GIS Report (Interconnection milestones & trends)"
        sc = keyword_score(f"{title} signed interconnect agreement studies milestones", keywords)
        # Tier 2 potential: “signed interconnect agreement / study complete” are Tier 2 signals inside the file
        return [DiscoveredItem(
            url=product_page,
            title=title,
            source=self.name,
            tier=2,
            signal_type="interconnection_milestones",
            published_utc=None,
            score=sc
        )]

# ----------------------------
# US: Permits + awards + filings (Tier 2/3 signals)
# ----------------------------
class EPAECHOAdapter(SourceAdapter):
    """
    EPA ECHO provides Web Services endpoints for facility searches across environmental programs.
    This adapter discovers the endpoint documentation page (and optionally can call APIs later).
    """
    name = "epa_echo_web_services"
    scope = "us"

    def discover(self, start: datetime, end: datetime, keywords: List[str]) -> List[DiscoveredItem]:
        url = "https://echo.epa.gov/tools/web-services"
        title = "EPA ECHO Web Services (permits/compliance REST services)"
        sc = keyword_score("permit air water npdes enforcement compliance facility search api", keywords)
        return [DiscoveredItem(
            url=url, title=title, source=self.name, tier=2,
            signal_type="permits_api", published_utc=None, score=sc
        )]

class USASpendingAdapter(SourceAdapter):
    """
    USAspending provides public API endpoints for federal awards/grants/contracts.
    This adapter discovers the API docs page (and can later run keyword-based award searches).
    """
    name = "usaspending_api"
    scope = "us"

    def discover(self, start: datetime, end: datetime, keywords: List[str]) -> List[DiscoveredItem]:
        url = "https://api.usaspending.gov/docs/endpoints"
        title = "USAspending API Endpoints (federal awards)"
        sc = keyword_score("DOE grant award electrolyzer hydrogen renewable", keywords)
        return [DiscoveredItem(
            url=url, title=title, source=self.name, tier=3,
            signal_type="awards_api", published_utc=None, score=sc
        )]

class FERCRSSAdapter(SourceAdapter):
    """
    FERC eCollection RSS feed (accepted filings); supports month/year params for backfill.
    Useful Tier 1/2 signals for regulated proceedings.
    """
    name = "ferc_ecollection_rss"
    scope = "us"

    def __init__(self, months_back: int = 60):
        self.months_back = months_back

    def discover(self, start: datetime, end: datetime, keywords: List[str]) -> List[DiscoveredItem]:
        # We will add candidate RSS URLs (monthly) within the window for later fetch.
        # Base: https://ecollection.ferc.gov/api/rssfeed?month=5&year=2022
        items: List[DiscoveredItem] = []
        cursor = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
        start_month = datetime(start.year, start.month, 1, tzinfo=timezone.utc)

        while cursor >= start_month:
            url = f"https://ecollection.ferc.gov/api/rssfeed?month={cursor.month}&year={cursor.year}"
            title = f"FERC Accepted Filings RSS {cursor.year}-{cursor.month:02d}"
            sc = keyword_score("filing docket interconnection certificate pipeline hydrogen", keywords)
            items.append(DiscoveredItem(
                url=url, title=title, source=self.name, tier=1,
                signal_type="regulatory_filings_rss", published_utc=cursor.isoformat(), score=sc
            ))
            # step back one month
            if cursor.month == 1:
                cursor = datetime(cursor.year - 1, 12, 1, tzinfo=timezone.utc)
            else:
                cursor = datetime(cursor.year, cursor.month - 1, 1, tzinfo=timezone.utc)

        return items[:MAX_DOWNLOADS_PER_RUN]  # cap

# ----------------------------
# Global stubs (extend later)
# ----------------------------
class GlobalConnectionsStub(SourceAdapter):
    """
    Placeholder for country/TSO interconnection or connection-queue disclosures.
    Example: UK NESO provides queue management information (not always a structured queue file).
    You will extend this with actual country-specific adapters.
    """
    name = "global_connections_stub"
    scope = "global"

    def discover(self, start: datetime, end: datetime, keywords: List[str]) -> List[DiscoveredItem]:
        # Example info page
        url = "https://www.neso.energy/industry-information/connections/queue-management"
        title = "UK NESO Connections Queue Management (info page)"
        sc = keyword_score("connections queue management grid connection milestone", keywords)
        return [DiscoveredItem(
            url=url, title=title, source=self.name, tier=1,
            signal_type="global_queue_info", published_utc=None, score=sc
        )]

# ----------------------------
# Downloader / snapshotter
# ----------------------------
def snapshot_item(state: Dict[str, Any], item: DiscoveredItem, download_budget: List[int]) -> DiscoveredItem:
    seen = state.setdefault("seen", {}).get(item.url)
    if seen and not is_stale(seen):
        return item  # skip fetching

    if download_budget[0] >= MAX_DOWNLOADS_PER_RUN:
        return item

    r = http_get(item.url)
    pace()

    content_type = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    body = r.content
    h = sha256_bytes(body)

    # if unchanged, mark checked and return
    if seen and seen.get("sha256") == h:
        seen["last_checked_utc"] = utcnow()
        state["seen"][item.url] = seen
        return item

    # store raw
    urlp = urlparse(item.url)
    path_slug = safe_slug(urlp.netloc + "_" + urlp.path)
    ext = ".bin"
    if item.url.lower().endswith(".xlsx"):
        ext = ".xlsx"
    elif item.url.lower().endswith(".xml") or "xml" in content_type:
        ext = ".xml"
    elif item.url.lower().endswith(".pdf") or "pdf" in content_type:
        ext = ".pdf"
    elif "html" in content_type:
        ext = ".html"
    elif "json" in content_type:
        ext = ".json"

    fname = f"{safe_slug(item.source)}_{safe_slug(item.signal_type)}{ext}"
    p = write_raw(path_slug, fname, body)
    download_budget[0] += 1

    # update state
    rec = seen or {}
    rec.setdefault("first_seen_utc", utcnow())
    rec["last_checked_utc"] = utcnow()
    rec["sha256"] = h
    rec["content_type"] = content_type
    rec["raw_path"] = str(p)
    state["seen"][item.url] = rec

    # update item
    item.content_type = content_type
    item.raw_path = str(p)
    item.sha256 = h
    return item

# ----------------------------
# Main orchestration
# ----------------------------
def get_adapters(scope: str) -> List[SourceAdapter]:
    us_adapters: List[SourceAdapter] = [
        CAISOQueueAdapter(),
        MISOQueueAdapter(),
        ERCOTGISAdapter(),
        EPAECHOAdapter(),
        USASpendingAdapter(),
        FERCRSSAdapter(),
    ]
    global_adapters: List[SourceAdapter] = [
        GlobalConnectionsStub(),
    ]

    if scope == "us":
        return us_adapters
    if scope == "global":
        return global_adapters
    if scope == "all":
        return us_adapters + global_adapters
    raise ValueError("scope must be us|global|all")

def main():
    setup_logging()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CURATED_DIR.mkdir(parents=True, exist_ok=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["us", "global", "all"], default="us")
    ap.add_argument("--tiers", nargs="+", type=int, default=[1,2,3])
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS)
    args = ap.parse_args()

    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    state = load_state()
    state["tool_version"] = TOOL_VERSION
    state["last_run_utc"] = utcnow()

    adapters = get_adapters(args.scope)

    logging.info("========================================")
    logging.info(f"FID Signal Discovery v{TOOL_VERSION} | scope={args.scope} | tiers={args.tiers} | window={args.start}..{args.end}")
    logging.info(f"keywords={args.keywords}")

    all_items: List[DiscoveredItem] = []
    for ad in adapters:
        try:
            items = ad.discover(start_dt, end_dt, args.keywords)
            # filter by tier + rough date window
            items = [it for it in items if it.tier in args.tiers]
            items = [it for it in items if in_date_window(parse_date_guess(it.published_utc or ""), start_dt, end_dt)]
            all_items.extend(items)
            logging.info(f"[DISCOVER] {ad.name}: {len(items)} items")
        except Exception as e:
            logging.exception(f"[DISCOVER] {ad.name} failed: {e}")

    # de-dupe by url keep best score
    best: Dict[str, DiscoveredItem] = {}
    for it in all_items:
        if it.url not in best or it.score > best[it.url].score:
            best[it.url] = it
    items = sorted(best.values(), key=lambda x: x.score, reverse=True)

    # plan
    plan = {
        "tool_version": TOOL_VERSION,
        "run_utc": state["last_run_utc"],
        "scope": args.scope,
        "tiers": args.tiers,
        "window": {"start": args.start, "end": args.end},
        "keywords": args.keywords,
        "count": len(items),
        "items_preview": [asdict(x) for x in items[:40]],
    }
    PLAN_PATH.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    logging.info(f"[PLAN] saved -> {PLAN_PATH}")

    # snapshot + graph + catalog
    download_budget = [0]
    for it in items:
        # graph log (every discovered item)
        append_jsonl(GRAPH_PATH, {
            "run_utc": state["last_run_utc"],
            "url": it.url,
            "source": it.source,
            "tier": it.tier,
            "signal_type": it.signal_type,
            "score": it.score,
            "title": it.title,
            "published_utc": it.published_utc,
        })

        # snapshot
        it2 = snapshot_item(state, it, download_budget)

        # catalog record (what downstream ingestion/embedding should read)
        append_jsonl(CATALOG_PATH, {
            "run_utc": state["last_run_utc"],
            "url": it2.url,
            "title": it2.title,
            "source": it2.source,
            "tier": it2.tier,
            "signal_type": it2.signal_type,
            "published_utc": it2.published_utc,
            "score": it2.score,
            "content_type": it2.content_type,
            "raw_path": it2.raw_path,
            "sha256": it2.sha256,
        })

        if download_budget[0] >= MAX_DOWNLOADS_PER_RUN:
            logging.warning("[STOP] Hit MAX_DOWNLOADS_PER_RUN.")
            break

    save_state(state)
    logging.info(f"[DONE] items={len(items)} downloads={download_budget[0]}")
    logging.info("========================================")

if __name__ == "__main__":
    main()
