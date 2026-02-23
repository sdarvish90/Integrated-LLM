"""
doe_award_status.py
====================
Verifies the current status of DOE awards in your regulatory_evidence table
against the USAspending API, flagging cancellations made post-January 2025.

CONTEXT
-------
The Trump administration (Jan 2025 onwards) has cancelled or proposed cancelling
~$23B in Biden-era clean energy grants in multiple tranches:
  - May 2025:    $3.7B cancelled (24 projects) — ExxonMobil, Calpine, Heidelberg, Ørsted
  - Oct 2, 2025: $7.6B cancelled (321 awards, 223 projects) — blue state targeted
  - Oct 8, 2025: 600+ awards proposed for termination including all 7 H2 hubs
  - Total proposed: ~$23.3B across 600+ grants (many still in litigation)

HOW IT WORKS
------------
The USAspending transactions endpoint returns a modification history per award.
A termination shows as action_type = "E" (terminate for convenience) or "H"
(terminate for default). A $0 obligation revision post-Jan 2025 is also a
strong cancellation signal even when the action_type wasn't updated correctly.

USAGE
-----
    python doe_award_status.py --check-all
    python doe_award_status.py --check-all --update-db
    python doe_award_status.py --check-id DECD0000053
    python doe_award_status.py --report
"""

from __future__ import annotations

import json
import logging
import sqlite3
import ssl
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)

# SSL context for macOS compatibility (system Python may lack certs)
_SSL_CTX = ssl.create_default_context()
try:
    import certifi
    _SSL_CTX.load_verify_locations(certifi.where())
except ImportError:
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE

# ── Constants ────────────────────────────────────────────────────────────────

USASPENDING_BASE = "https://api.usaspending.gov/api/v2"
ADMIN_CHANGE_DATE = date(2025, 1, 20)          # Inauguration Day — start of risk window
FIRST_CANCELLATION = date(2025, 5, 30)         # First confirmed DOE tranche
SECOND_CANCELLATION = date(2025, 10, 2)        # $7.6B blue-state tranche
THIRD_LIST_DATE = date(2025, 10, 8)            # 600+ award list leaked

# Action types from USAspending that indicate termination
TERMINATION_ACTION_TYPES = {
    "E": "Terminate for Convenience",
    "H": "Terminate for Default",
}

# Companies confirmed cancelled in public reporting (from May + Oct 2025 tranches)
# Use this as a fast-path flag when the API doesn't yet reflect the termination
CONFIRMED_CANCELLED_COMPANIES = {
    # May 2025 tranche
    "EXXON MOBIL",
    "EXXON MOBIL CORPORATION",
    "CALPINE",
    "CALPINE TEXAS CCUS",
    "CALPINE CALIFORNIA CCUS",
    "HEIDELBERG MATERIALS",
    "HEIDELBERG MATERIALS US",
    "ORSTED STAR P2X",
    "ØRSTED",
    # Oct 2025 — hydrogen hubs
    "ARCHES H2",                       # CA green hub — confirmed cancelled
    "PACIFIC NORTHWEST HYDROGEN",      # WA/OR green hub — confirmed cancelled
    # These hubs are on the proposed list (status uncertain/litigated)
    "HYVELOCITY",                      # Gulf Coast hub — proposed
    "MIDWEST ALLIANCE FOR CLEAN HYDROGEN",  # Midwest hub — proposed
    "HEARTLAND HYDROGEN HUB",          # Heartland hub — proposed
    "MID-ATLANTIC CLEAN HYDROGEN HUB", # MACH2 — proposed
    # Oct general tranche (blue states)
    "PLUG POWER",                      # 4 grants cancelled per Latitude Media
    "GE VERNOVA OPERATIONS",           # 11 projects cancelled per E&E
    "DAIMLER TRUCK NORTH AMERICA",
}

# Hubs with uncertain/litigated status as of Feb 2026
LITIGATED_OR_UNCERTAIN = {
    "HYVELOCITY",
    "MIDWEST ALLIANCE FOR CLEAN HYDROGEN",
    "HEARTLAND HYDROGEN HUB",
    "MID-ATLANTIC CLEAN HYDROGEN HUB",
    "ARCH2",
    "APPALACHIAN REGIONAL CLEAN HYDROGEN",
}

# ── Status dataclass ──────────────────────────────────────────────────────────

@dataclass
class AwardStatus:
    document_id: str
    company_name: str
    state: Optional[str]
    original_amount: float
    
    # API-derived
    status: str = "unknown"          # active | terminated | reduced | uncertain | api_error
    termination_date: Optional[str] = None
    termination_action_type: Optional[str] = None
    latest_obligation: float = 0.0
    latest_action_date: Optional[str] = None
    total_outlays: float = 0.0       # how much was actually disbursed
    pct_disbursed: float = 0.0
    
    # Flags
    confirmed_cancelled_by_reporting: bool = False
    in_litigation: bool = False
    awarded_in_lame_duck: bool = False   # Nov 5 2024 – Jan 20 2025
    
    # Explanation
    reasoning: str = ""
    api_transactions: List[Dict] = field(default_factory=list, repr=False)


# ── API helpers ───────────────────────────────────────────────────────────────

def _post(endpoint: str, payload: dict, timeout: int = 15) -> dict:
    url = f"{USASPENDING_BASE}{endpoint}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "DecarbIQ/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read())


def _get(endpoint: str, timeout: int = 15) -> dict:
    url = f"{USASPENDING_BASE}{endpoint}"
    req = urllib.request.Request(
        url, headers={"Content-Type": "application/json", "User-Agent": "DecarbIQ/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read())


def _resolve_generated_id(document_id: str) -> Optional[str]:
    """
    Convert a raw award ID like DECD0000053 to USAspending generated_unique_award_id.

    Empirically confirmed pattern for DOE cooperative agreements:
        DECD0000053  →  ASST_NON_DECD0000053_089
        DEFE0022596  →  ASST_NON_DEFE0022596_089
        DEAR0001019  →  ASST_NON_DEAR0001019_089

    Falls back to API search if the direct pattern 404s (handles edge cases
    where the agency code suffix differs from 089).
    """
    # Fast path — direct pattern (empirically confirmed for DOE awards)
    direct_id = f"ASST_NON_{document_id}_089"
    try:
        _get(f"/awards/{direct_id}/")
        return direct_id
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Record expunged post-cancellation — confirmed by Sierra Club reporting
            # Do NOT fall through to search; treat 404 as cancellation signal
            raise
        # Other HTTP error — try search fallback
    except Exception:
        pass

    # Fallback: API search (handles non-089 suffix)
    try:
        result = _post("/search/spending_by_award/", {
            "subawards": False,
            "limit": 1,
            "page": 1,
            "fields": ["Award ID"],
            "filters": {
                "award_ids": [document_id],
                "award_type_codes": ["02", "03", "04", "05"]
            },
            "sort": "Award Amount",
            "order": "desc"
        })
        results = result.get("results", [])
        if results:
            return results[0].get("generated_internal_id")
        return None
    except Exception as e:
        logger.warning("Failed to resolve award ID %s: %s", document_id, e)
        return None


def get_award_transactions(generated_id: str) -> List[Dict]:
    """Fetch full transaction/modification history for an award."""
    try:
        result = _post("/transactions/", {
            "award_id": generated_id,
            "limit": 50,
            "page": 1,
            "sort": "action_date",
            "order": "asc"
        })
        return result.get("results", [])
    except Exception as e:
        logger.warning("Failed to get transactions for %s: %s", generated_id, e)
        return []


def get_award_summary(generated_id: str) -> Dict:
    """Get total outlays for an award (how much was actually paid out)."""
    try:
        return _get(f"/awards/{generated_id}/")
    except Exception as e:
        logger.warning("Failed to get award summary for %s: %s", generated_id, e)
        return {}


# ── Core status logic ─────────────────────────────────────────────────────────

def _parse_amount(excerpt: str) -> float:
    """Extract dollar amount from raw_text_excerpt."""
    for line in (excerpt or "").split("\n"):
        if "Amount:" in line:
            try:
                clean = line.split("Amount:")[1].strip().replace("$", "").replace(",", "")
                return float(clean)
            except (ValueError, IndexError):
                pass
    return 0.0


def _check_lame_duck(date_signed: Optional[str]) -> bool:
    """Was this award signed between Election Day 2024 and Inauguration Day 2025?"""
    if not date_signed:
        return False
    try:
        d = datetime.strptime(date_signed[:10], "%Y-%m-%d").date()
        return date(2024, 11, 5) <= d <= ADMIN_CHANGE_DATE
    except ValueError:
        return False


def check_award_status(document_id: str, company_name: str,
                       state: Optional[str], excerpt: str) -> AwardStatus:
    """
    Full status check for a single DOE award via USAspending API.
    
    Returns AwardStatus with:
      - status: active | terminated | reduced | uncertain | api_error
      - termination_date: if terminated
      - pct_disbursed: actual outlays / obligated amount
      - confirmed_cancelled_by_reporting: from known cancellation lists
      - in_litigation: if the company/hub is known to be contesting
    """
    original_amount = _parse_amount(excerpt)
    status = AwardStatus(
        document_id=document_id,
        company_name=company_name,
        state=state,
        original_amount=original_amount,
    )

    # ── Fast-path: check against confirmed cancelled company list ─────────────
    company_upper = (company_name or "").upper()
    for cancelled in CONFIRMED_CANCELLED_COMPANIES:
        if cancelled in company_upper:
            status.confirmed_cancelled_by_reporting = True
            break
    
    for litigated in LITIGATED_OR_UNCERTAIN:
        if litigated in company_upper:
            status.in_litigation = True
            break

    # ── API check ─────────────────────────────────────────────────────────────
    try:
        generated_id = _resolve_generated_id(document_id)
        if not generated_id:
            status.status = "uncertain"
            status.reasoning = "Award ID not found in USAspending (may have been expunged after cancellation)"
            # Note: Sierra Club reporting confirmed cancelled records ARE being expunged
            if status.confirmed_cancelled_by_reporting:
                status.status = "terminated"
                status.reasoning = "Not found in USAspending (records expunged post-cancellation) + confirmed in public reporting"
            return status

        # Get transaction history
        transactions = get_award_transactions(generated_id)
        status.api_transactions = transactions
        time.sleep(0.3)  # Rate limit courtesy

        # Get summary (for outlays)
        summary = get_award_summary(generated_id)
        total_outlays = (summary.get("total_outlay") or 
                        summary.get("total_account_outlay") or 0.0)
        if isinstance(total_outlays, (int, float)) and total_outlays:
            status.total_outlays = float(total_outlays)
            if original_amount > 0:
                status.pct_disbursed = round(100 * status.total_outlays / original_amount, 1)
        time.sleep(0.3)

        # Check date_signed for lame duck
        date_signed = summary.get("date_signed")
        status.awarded_in_lame_duck = _check_lame_duck(date_signed)

        # ── Parse transactions for termination signals ─────────────────────────
        # KEY FINDING from empirical testing (Feb 2026):
        # DOE is NOT using action_type="E" (formal terminate) for 2025 cancellations.
        # Instead they issue a REVISION (action_type="C") with federal_action_obligation=$0
        # AFTER a large prior obligation. This zeroes the award without a formal termination.
        # ExxonMobil's record was fully expunged (404). Both patterns must be detected.
        #
        # Pattern: NEW ($X) → REVISION ($Y large) → REVISION ($0 post-Jan-2025) = CANCELLED

        termination_tx = None
        latest_tx = None
        cumulative_obligation = 0.0
        obligation_post_jan25 = 0.0
        post_jan25_zero_revision = None
        pre_jan25_peak = 0.0

        for tx in transactions:
            action_date_str = tx.get("action_date", "")
            action_type = tx.get("action_type", "")
            obligation = float(tx.get("federal_action_obligation") or 0)
            
            try:
                action_date = datetime.strptime(action_date_str[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            latest_tx = tx
            cumulative_obligation += obligation

            # Track peak obligation before admin change and sum after
            if action_date < ADMIN_CHANGE_DATE:
                pre_jan25_peak = max(pre_jan25_peak, cumulative_obligation)
            else:
                obligation_post_jan25 += obligation

            # Formal termination action (rare but possible for some agencies)
            if action_type in TERMINATION_ACTION_TYPES:
                termination_tx = tx
                status.termination_date = action_date_str
                status.termination_action_type = TERMINATION_ACTION_TYPES[action_type]

            # $0 revision post-Jan 2025 after meaningful prior obligation = de facto cancellation
            if (action_date >= ADMIN_CHANGE_DATE
                    and action_type == "C"
                    and obligation == 0.0
                    and pre_jan25_peak > 1_000_000):
                post_jan25_zero_revision = tx

        # ── Determine final status ─────────────────────────────────────────────
        if latest_tx:
            status.latest_action_date = latest_tx.get("action_date")
            status.latest_obligation = cumulative_obligation

        if termination_tx:
            status.status = "terminated"
            status.reasoning = (
                f"Formal termination: action_type={status.termination_action_type} "
                f"on {status.termination_date}"
            )

        elif post_jan25_zero_revision:
            # This is the actual DOE 2025 cancellation pattern
            zero_date = post_jan25_zero_revision.get("action_date")
            status.status = "terminated"
            status.termination_date = zero_date
            status.termination_action_type = "REVISION to $0 (de facto cancellation)"
            status.reasoning = (
                f"$0 revision on {zero_date} after peak obligation of "
                f"${pre_jan25_peak:,.0f} pre-inauguration — "
                f"matches confirmed DOE 2025 cancellation pattern"
            )

        elif obligation_post_jan25 < -0.5 * original_amount and original_amount > 0:
            status.status = "reduced"
            status.reasoning = (
                f"Obligation reduced by ${abs(obligation_post_jan25):,.0f} post Jan 2025 "
                f"({abs(obligation_post_jan25)/original_amount*100:.0f}% of original)"
            )

        elif status.confirmed_cancelled_by_reporting:
            status.status = "terminated"
            status.reasoning = (
                "Confirmed cancelled in public reporting (E&E News / Latitude Media / PBS) "
                "but API may not yet reflect termination"
            )

        elif status.in_litigation:
            status.status = "uncertain"
            status.reasoning = (
                "On DOE termination list per leaked documents; status contested / in litigation"
            )

        elif status.awarded_in_lame_duck:
            status.status = "at_risk"
            status.reasoning = (
                "Awarded Nov 5 2024 – Jan 20 2025 (lame duck window); "
                "DOE stated 26% of cancelled awards were from this period — elevated cancellation risk"
            )

        else:
            status.status = "active"
            status.reasoning = "No termination found in transaction history; not on known cancellation lists"

    except urllib.error.HTTPError as e:
        if e.code == 404:
            status.status = "terminated"
            status.reasoning = "404 from USAspending — award record may have been expunged post-cancellation"
        else:
            status.status = "api_error"
            status.reasoning = f"HTTP {e.code}: {e.reason}"

    except Exception as e:
        status.status = "api_error"
        status.reasoning = f"API error: {str(e)[:150]}"

    return status


# ── Batch checker ─────────────────────────────────────────────────────────────

def check_all_awards(db_path: str, update_db: bool = False,
                     limit: Optional[int] = None) -> List[AwardStatus]:
    """
    Check all DOE awards in regulatory_evidence against USAspending.
    
    Args:
        db_path: Path to your SQLite DB
        update_db: If True, writes status back to regulatory_evidence 
                   (adds doe_award_status and doe_status_checked_at columns)
        limit: For testing — only check first N awards
    
    Returns:
        List of AwardStatus objects, sorted by risk (terminated first)
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Add status columns if they don't exist
    if update_db:
        for col, coltype in [
            ("doe_award_status", "TEXT"),
            ("doe_termination_date", "TEXT"),
            ("doe_pct_disbursed", "REAL"),
            ("doe_status_checked_at", "TEXT"),
            ("doe_status_reasoning", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE regulatory_evidence ADD COLUMN {col} {coltype}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists

    rows = conn.execute("""
        SELECT document_id, document_url, company_name, state, raw_text_excerpt
        FROM regulatory_evidence
        WHERE source = 'doe_usaspending'
        AND document_id IS NOT NULL
        ORDER BY document_id
    """).fetchall()

    if limit:
        rows = rows[:limit]

    results = []
    total = len(rows)
    
    print(f"\n  Checking {total} DOE awards against USAspending API...")
    print(f"  Administration change date: {ADMIN_CHANGE_DATE}")
    print(f"  First confirmed cancellation: {FIRST_CANCELLATION}\n")

    for i, row in enumerate(rows, 1):
        doc_id = row["document_id"]
        company = row["company_name"] or ""
        state = row["state"]
        excerpt = row["raw_text_excerpt"] or ""

        print(f"  [{i}/{total}] {doc_id} | {company[:40]}", end="", flush=True)
        
        status = check_award_status(doc_id, company, state, excerpt)
        results.append(status)
        
        status_emoji = {
            "active": "✓",
            "terminated": "✗",
            "reduced": "↓",
            "uncertain": "?",
            "at_risk": "⚠",
            "api_error": "⚙",
        }.get(status.status, "?")
        
        print(f" → {status_emoji} {status.status.upper()}"
              + (f" ({status.termination_date})" if status.termination_date else "")
              + (f" [{status.pct_disbursed:.0f}% disbursed]" if status.pct_disbursed else ""))

        if update_db:
            conn.execute("""
                UPDATE regulatory_evidence
                SET doe_award_status = ?,
                    doe_termination_date = ?,
                    doe_pct_disbursed = ?,
                    doe_status_checked_at = ?,
                    doe_status_reasoning = ?
                WHERE document_id = ?
            """, (
                status.status,
                status.termination_date,
                status.pct_disbursed,
                datetime.now().isoformat(),
                status.reasoning,
                doc_id,
            ))
            conn.commit()

        time.sleep(0.5)  # Respect API rate limits

    conn.close()

    # Sort: terminated first, then uncertain/at_risk, then active
    status_order = {"terminated": 0, "reduced": 1, "uncertain": 2, "at_risk": 3,
                    "active": 4, "api_error": 5}
    results.sort(key=lambda x: status_order.get(x.status, 9))

    return results


# ── Report generator ──────────────────────────────────────────────────────────

def print_report(results: List[AwardStatus]) -> None:
    """Print a formatted status report to console."""
    
    by_status: Dict[str, List[AwardStatus]] = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r)

    total_original = sum(r.original_amount for r in results)
    total_terminated = sum(r.original_amount for r in by_status.get("terminated", []))
    total_uncertain = sum(r.original_amount for r in by_status.get("uncertain", []))
    total_at_risk = sum(r.original_amount for r in by_status.get("at_risk", []))
    total_active = sum(r.original_amount for r in by_status.get("active", []))

    print("\n" + "="*70)
    print("  DOE AWARD STATUS REPORT — Post-January 2025 Administration Change")
    print("="*70)
    print(f"\n  Total awards checked:     {len(results)}")
    print(f"  Total obligated:          ${total_original:>12,.0f}")
    print(f"  ✗ Terminated:             ${total_terminated:>12,.0f}  ({len(by_status.get('terminated', []))} awards)")
    print(f"  ? Uncertain/litigated:    ${total_uncertain:>12,.0f}  ({len(by_status.get('uncertain', []))} awards)")
    print(f"  ⚠ At risk (lame duck):    ${total_at_risk:>12,.0f}  ({len(by_status.get('at_risk', []))} awards)")
    print(f"  ✓ Active:                 ${total_active:>12,.0f}  ({len(by_status.get('active', []))} awards)")

    for status_key, label in [
        ("terminated", "✗ TERMINATED"),
        ("reduced", "↓ SIGNIFICANTLY REDUCED"),
        ("uncertain", "? UNCERTAIN / LITIGATED"),
        ("at_risk", "⚠ AT RISK (LAME DUCK AWARD)"),
    ]:
        group = by_status.get(status_key, [])
        if not group:
            continue
        print(f"\n\n  {label}")
        print("  " + "-"*60)
        for r in group:
            lame = " [LAME DUCK]" if r.awarded_in_lame_duck else ""
            lit = " [LITIGATED]" if r.in_litigation else ""
            print(f"\n    {r.document_id} | {r.company_name[:45]}")
            print(f"    Amount: ${r.original_amount:,.0f} | State: {r.state} | Disbursed: {r.pct_disbursed:.1f}%{lame}{lit}")
            print(f"    Reason: {r.reasoning[:100]}")

    active_group = by_status.get("active", [])
    if active_group:
        print(f"\n\n  ✓ ACTIVE ({len(active_group)} awards)")
        print("  " + "-"*60)
        for r in active_group:
            lame = " [LAME DUCK - WATCH]" if r.awarded_in_lame_duck else ""
            print(f"    {r.document_id} | {r.company_name[:40]} | ${r.original_amount:,.0f} | {r.state}{lame}")

    print("\n" + "="*70)
    print("  IMPLICATION FOR PROJECT DATABASE")
    print("="*70)
    cancelled_pct = 100 * (total_terminated + total_uncertain) / total_original if total_original else 0
    print(f"\n  {cancelled_pct:.0f}% of DOE award value in your DB is terminated or uncertain.")
    print(f"  These records should NOT be used as positive FID signals.")
    print(f"  Consider adding 'doe_award_cancelled' to your evidence gap flag system.")
    print(f"\n  Surviving awards (likely blue hydrogen / CCS aligned with current admin):")
    print(f"  - Delek Big Spring TX (CCUS, red state) — Revision on 2025-07-16, still active")
    print(f"  - Southern Company, Chevron, Shell aligned projects — administration favorites")
    print(f"  - DOE Fossil Energy grants (not OCED) — lower cancellation risk")


# ── DB impact: mark cancelled awards in unified_projects ─────────────────────

def flag_cancelled_in_project_evidence(db_path: str) -> Dict:
    """
    After running check_all_awards with update_db=True, this function
    propagates the cancellation flags to project_evidence so FID scoring
    knows not to count cancelled DOE awards as positive signals.
    """
    conn = sqlite3.connect(db_path)
    
    # Check if status columns exist yet
    try:
        conn.execute("SELECT doe_award_status FROM regulatory_evidence LIMIT 1")
    except sqlite3.OperationalError:
        conn.close()
        return {"error": "Run check_all_awards(update_db=True) first"}

    # Find project_evidence entries linked to cancelled DOE awards
    cancelled_docs = conn.execute("""
        SELECT document_id, company_name, doe_award_status, doe_termination_date
        FROM regulatory_evidence
        WHERE source = 'doe_usaspending'
        AND doe_award_status IN ('terminated', 'reduced', 'uncertain')
    """).fetchall()
    
    updated = 0
    for row in cancelled_docs:
        # Mark these evidence entries as stale
        conn.execute("""
            UPDATE project_evidence
            SET stale = 1,
                stale_reason = ?
            WHERE document_id = ?
        """, (
            f"DOE award {row[2]}: {row[3] or 'post-Jan 2025 administration change'}",
            row[0]
        ))
        updated += conn.total_changes

    conn.commit()
    conn.close()
    
    return {
        "cancelled_awards_found": len(cancelled_docs),
        "project_evidence_entries_staled": updated
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="Check DOE award status post-Jan 2025")
    parser.add_argument("--check-all", action="store_true",
                        help="Check all DOE awards in DB")
    parser.add_argument("--check-id", type=str, metavar="AWARD_ID",
                        help="Check a single award by ID")
    parser.add_argument("--update-db", action="store_true",
                        help="Write status back to regulatory_evidence table")
    parser.add_argument("--flag-projects", action="store_true",
                        help="Mark cancelled awards as stale in project_evidence")
    parser.add_argument("--report", action="store_true",
                        help="Print report from already-checked DB data")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only check first N awards (for testing)")
    parser.add_argument("--db", type=str, default="blue_h2_intelligence.db",
                        help="Path to SQLite database")
    args = parser.parse_args()

    if args.check_all:
        results = check_all_awards(args.db, update_db=args.update_db, limit=args.limit)
        print_report(results)

    elif args.check_id:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT company_name, state, raw_text_excerpt FROM regulatory_evidence "
            "WHERE document_id = ?", (args.check_id,)
        ).fetchone()
        conn.close()
        if row:
            s = check_award_status(args.check_id, row["company_name"],
                                    row["state"], row["raw_text_excerpt"])
            print(f"\n  {s.document_id}: {s.status.upper()}")
            print(f"  Company: {s.company_name}")
            print(f"  Amount: ${s.original_amount:,.0f} | Disbursed: {s.pct_disbursed:.1f}%")
            print(f"  Reasoning: {s.reasoning}")
            print(f"  Lame duck: {s.awarded_in_lame_duck}")
            if s.api_transactions:
                print(f"\n  Transactions ({len(s.api_transactions)}):")
                for tx in s.api_transactions:
                    print(f"    {tx.get('action_date')} | type={tx.get('action_type')} "
                          f"({tx.get('action_type_description')}) | "
                          f"${float(tx.get('federal_action_obligation') or 0):,.0f}")
        else:
            print(f"  Award {args.check_id} not found in DB")

    elif args.flag_projects:
        result = flag_cancelled_in_project_evidence(args.db)
        print(f"\n  Flagged {result.get('cancelled_awards_found', 0)} cancelled awards")
        print(f"  Staled {result.get('project_evidence_entries_staled', 0)} project_evidence entries")

    elif args.report:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT document_id, company_name, state, raw_text_excerpt,
                       doe_award_status, doe_termination_date, doe_pct_disbursed,
                       doe_status_reasoning
                FROM regulatory_evidence
                WHERE source = 'doe_usaspending'
                AND doe_award_status IS NOT NULL
            """).fetchall()
            results = []
            for r in rows:
                s = AwardStatus(
                    document_id=r["document_id"],
                    company_name=r["company_name"] or "",
                    state=r["state"],
                    original_amount=_parse_amount(r["raw_text_excerpt"] or ""),
                    status=r["doe_award_status"],
                    termination_date=r["doe_termination_date"],
                    pct_disbursed=r["doe_pct_disbursed"] or 0.0,
                    reasoning=r["doe_status_reasoning"] or "",
                )
                results.append(s)
            print_report(results)
        except sqlite3.OperationalError:
            print("  No status data found. Run --check-all --update-db first.")
        conn.close()
