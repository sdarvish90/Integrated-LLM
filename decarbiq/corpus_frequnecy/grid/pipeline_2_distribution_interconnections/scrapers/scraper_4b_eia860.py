"""
Scraper 4B: EIA Form 860/860M — Planned Generator Tracking
=============================================================
Downloads the monthly EIA-860M Excel file and the annual EIA-860
to identify planned generators ≥1 MW in Texas that may be co-located
with or serving blue H2, ammonia, or CCS facilities.

Key signals:
- Gas turbines in Gulf Coast counties (potential H2/ammonia power supply)
- Solar + battery near known H2/ammonia sites (green/hybrid power)
- Any generator owned by known H2/ammonia developers
- Planned generators at existing petrochemical/refinery sites
"""

import re
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scrapers.utils import (
    fetch_page_soup, fetch_url, download_file,
    content_hash, is_new_content, mark_seen,
)
from config.settings import (
    EIA_860M_URL, EIA_860_URL, STORAGE_DIR, OUTPUTS_DIR, ScrapeResult,
    GULF_COAST_COUNTIES, KNOWN_DEVELOPERS, EIA_STATE_FILTER,
)

EIA_DIR = STORAGE_DIR / "eia"


def scrape_eia_860m() -> list[ScrapeResult]:
    """
    Download the latest EIA-860M monthly generator inventory and extract
    planned generators in Texas, with relevance filtering.
    """
    print("[4B.1] Downloading EIA Form 860M...")
    results = []

    # Get the 860M download page to find the latest file
    soup = fetch_page_soup(EIA_860M_URL)
    if soup is None:
        print("  [ERROR] Could not fetch EIA-860M page")
        return results

    # Find the Excel download link
    xlsx_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.endswith(".xlsx") and "generator" in href.lower():
            full = href if href.startswith("http") else f"https://www.eia.gov{href}"
            xlsx_links.append({"url": full, "text": a.get_text(strip=True)})

    if not xlsx_links:
        print("  [WARN] No 860M Excel file found on page")
        return results

    # Download the most recent file
    latest = xlsx_links[0]
    url = latest["url"]
    url_hash = content_hash(url)

    local = download_file(url, EIA_DIR, filename="eia860m_latest.xlsx")
    if local is None:
        print("  [ERROR] Failed to download 860M file")
        return results

    print(f"  Downloaded: {latest['text']}")

    try:
        # Parse the Planned tab
        df_planned = pd.read_excel(local, sheet_name="Planned", engine="openpyxl",
                                    header=2)
        print(f"  Planned generators (national): {len(df_planned)}")

        # Standardize column names
        df_planned.columns = [str(c).strip() for c in df_planned.columns]

        # Find state column
        state_col = None
        for col in df_planned.columns:
            if col.lower() in ["state", "plant state"]:
                state_col = col
                break

        if state_col is None:
            print(f"  [WARN] Could not find State column. Columns: {list(df_planned.columns)[:10]}")
            return results

        # Filter to Texas
        tx_planned = df_planned[df_planned[state_col].isin(EIA_STATE_FILTER)].copy()
        print(f"  Texas planned generators: {len(tx_planned)}")

        if tx_planned.empty:
            return results

        # Find county column
        county_col = None
        for col in tx_planned.columns:
            if "county" in col.lower():
                county_col = col
                break

        # Find capacity column
        cap_col = None
        for col in tx_planned.columns:
            if "nameplate" in col.lower() and "capacity" in col.lower():
                cap_col = col
                break
            if "net summer" in col.lower():
                cap_col = col

        # Find entity/owner column
        entity_col = None
        for col in tx_planned.columns:
            if "entity name" in col.lower() or "owner" in col.lower():
                entity_col = col
                break

        # Find technology/fuel columns
        tech_col = None
        fuel_col = None
        for col in tx_planned.columns:
            if "technology" in col.lower():
                tech_col = col
            if "energy source" in col.lower() or "fuel" in col.lower():
                fuel_col = col

        # ── Filter for Gulf Coast ─────────────────────────────────────────
        gulf_coast_planned = pd.DataFrame()
        if county_col:
            gulf_coast_planned = tx_planned[
                tx_planned[county_col].astype(str).str.strip().isin(GULF_COAST_COUNTIES)
            ]
            print(f"  Gulf Coast planned: {len(gulf_coast_planned)}")

        # ── Filter for known developers ───────────────────────────────────
        developer_planned = pd.DataFrame()
        if entity_col:
            for dev in KNOWN_DEVELOPERS:
                matches = tx_planned[
                    tx_planned[entity_col].astype(str).str.contains(dev, case=False, na=False)
                ]
                developer_planned = pd.concat([developer_planned, matches])
            developer_planned = developer_planned.drop_duplicates()
            if not developer_planned.empty:
                print(f"  Known developer matches: {len(developer_planned)}")

        # ── Filter for gas generators (potential H2/ammonia power) ────────
        gas_planned = pd.DataFrame()
        if tech_col:
            gas_planned = tx_planned[
                tx_planned[tech_col].astype(str).str.contains(
                    "natural gas|combustion turbine|combined cycle|gas",
                    case=False, na=False
                )
            ]
        elif fuel_col:
            gas_planned = tx_planned[
                tx_planned[fuel_col].astype(str).str.contains("NG|gas", case=False, na=False)
            ]
        if not gas_planned.empty:
            print(f"  Gas generators in Texas: {len(gas_planned)}")

        # ── Build summary ─────────────────────────────────────────────────
        summary = {
            "total_texas_planned": len(tx_planned),
            "gulf_coast_planned": len(gulf_coast_planned),
            "developer_matches": len(developer_planned),
            "gas_generators": len(gas_planned),
            "columns": list(tx_planned.columns),
        }

        # Total planned MW
        if cap_col:
            summary["total_texas_mw"] = float(tx_planned[cap_col].sum())
            if not gulf_coast_planned.empty:
                summary["gulf_coast_mw"] = float(gulf_coast_planned[cap_col].sum())
            if not gas_planned.empty:
                summary["gas_mw"] = float(gas_planned[cap_col].sum())

        # Save filtered data
        tx_planned.to_csv(OUTPUTS_DIR / "eia860m_texas_planned.csv", index=False)
        if not gulf_coast_planned.empty:
            gulf_coast_planned.to_csv(OUTPUTS_DIR / "eia860m_gulf_coast_planned.csv", index=False)
        if not developer_planned.empty:
            developer_planned.to_csv(OUTPUTS_DIR / "eia860m_developer_matches.csv", index=False)

        file_hash = content_hash(local.read_bytes())
        result = ScrapeResult(
            source="eia_860m",
            doc_type="monthly_generator_inventory",
            title=f"EIA-860M: {len(tx_planned)} TX planned, {len(gulf_coast_planned)} Gulf Coast",
            url=url,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            content_hash=file_hash,
            text_excerpt=json.dumps(summary, indent=2),
            relevance_score=0.6,
            matched_keywords=["planned generators", "Texas", "distribution"],
            metadata=summary,
            local_path=str(local),
            is_new=is_new_content(file_hash),
        )
        results.append(result)
        mark_seen(file_hash)

    except Exception as e:
        print(f"  [ERROR] Failed to parse 860M: {e}")
        import traceback
        traceback.print_exc()

    return results


def scrape_eia_860_annual() -> list[ScrapeResult]:
    """
    Download the annual EIA-860 for deeper generator data including
    ownership details, environmental equipment, and construction costs.
    """
    print("[4B.2] Checking EIA Form 860 (annual)...")
    results = []

    soup = fetch_page_soup(EIA_860_URL)
    if soup is None:
        return results

    # Find the latest annual data download
    xlsx_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if href.endswith(".zip") and ("eia860" in href.lower() or "860" in text.lower()):
            full = href if href.startswith("http") else f"https://www.eia.gov{href}"
            xlsx_links.append({"url": full, "text": text})

    if xlsx_links:
        latest = xlsx_links[0]
        url_hash = content_hash(latest["url"])
        if is_new_content(url_hash):
            result = ScrapeResult(
                source="eia_860_annual",
                doc_type="annual_generator_report",
                title=f"EIA-860 Annual: {latest['text'][:80]}",
                url=latest["url"],
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                content_hash=url_hash,
                text_excerpt=f"Annual EIA-860 data available: {latest['text']}",
                relevance_score=0.5,
                matched_keywords=["planned generators", "annual"],
                metadata={"download_url": latest["url"], "note": "Download ZIP for full data"},
                is_new=True,
            )
            results.append(result)
            mark_seen(url_hash)

    print(f"  [OK] {len(results)} annual datasets found")
    return results


def run_all_4b() -> list[ScrapeResult]:
    results = []
    results.extend(scrape_eia_860m())
    results.extend(scrape_eia_860_annual())
    return results


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    all_results = run_all_4b()
    print(f"\n[4B TOTAL] {len(all_results)} items")
    for r in all_results:
        print(f"  [{r.source}] {r.title[:70]}")
        if r.metadata.get("total_texas_planned"):
            print(f"    TX planned: {r.metadata['total_texas_planned']} generators")
            print(f"    Gulf Coast: {r.metadata.get('gulf_coast_planned', 'N/A')}")
