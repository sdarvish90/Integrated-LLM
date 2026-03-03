"""
DecarbIQ Learning Layer — Step 4: Frequency Review
Prints the emergent scheme landscape. No database writes.
"""

import sqlite3

from config import LEARNING_DB


def report_a_frequency(db: sqlite3.Connection):
    """Report A — Frequency distribution by document type."""
    print("\n" + "=" * 70)
    print("REPORT A — FREQUENCY DISTRIBUTION BY DOCUMENT TYPE")
    print("=" * 70)

    doc_types = db.execute("""
        SELECT document_type, COUNT(DISTINCT source_id) as doc_count
        FROM claims
        GROUP BY document_type
        ORDER BY doc_count DESC
    """).fetchall()

    for doc_type, doc_count in doc_types:
        print(f"\n=== {doc_type} ({doc_count} documents processed) ===\n")

        # Get claim frequencies for this doc type
        freq = db.execute("""
            SELECT
                claim_type,
                COUNT(*) as total_occurrences,
                COUNT(DISTINCT source_id) as doc_occurrences,
                ROUND(AVG(confidence), 2) as avg_conf
            FROM claims
            WHERE document_type = ?
            GROUP BY claim_type
            ORDER BY doc_occurrences DESC, total_occurrences DESC
        """, (doc_type,)).fetchall()

        primary = []
        secondary = []
        single = []

        for claim_type, total_occ, doc_occ, avg_conf in freq:
            ratio = doc_occ / doc_count if doc_count > 0 else 0
            entry = (claim_type, doc_occ, doc_count, avg_conf, total_occ)
            if ratio >= 0.50:
                primary.append(entry)
            elif ratio >= 0.20:
                secondary.append(entry)
            elif doc_occ == 1:
                single.append(entry)
            else:
                secondary.append(entry)

        if primary:
            print("PRIMARY SCHEMES (appeared in 50%+ of documents):")
            for ct, doc_occ, dc, avg_conf, total in primary:
                print(f"  {doc_occ:3d}/{dc}  {ct:45s}  avg_confidence={avg_conf}")
            print()

        if secondary:
            print("SECONDARY SCHEMES (appeared in 20–49% or 2+ documents):")
            for ct, doc_occ, dc, avg_conf, total in secondary:
                print(f"  {doc_occ:3d}/{dc}  {ct:45s}  avg_confidence={avg_conf}")
            print()

        if single:
            print("SINGLE OCCURRENCE (appeared once — may be most important):")
            for ct, doc_occ, dc, avg_conf, total in single:
                # Get the specific company and date for this single claim
                detail = db.execute("""
                    SELECT company_name, document_date
                    FROM claims
                    WHERE document_type = ? AND claim_type = ?
                    LIMIT 1
                """, (doc_type, ct)).fetchone()
                company = detail[0] if detail else "?"
                date = detail[1] if detail else "?"
                print(f"  {doc_occ:3d}/{dc}  {ct:45s}  avg_confidence={avg_conf}  -> {company} {date}")
            print()


def report_b_company(db: sqlite3.Connection):
    """Report B — Per-company claim summary."""
    print("\n" + "=" * 70)
    print("REPORT B — PER-COMPANY CLAIM SUMMARY")
    print("=" * 70)

    companies = db.execute("""
        SELECT DISTINCT company_name
        FROM claims
        ORDER BY company_name
    """).fetchall()

    for (company,) in companies:
        claims = db.execute("""
            SELECT document_date, document_type, claim_type, claim_text,
                   confidence, commitment_level, flagged_for_review
            FROM claims
            WHERE company_name = ?
            ORDER BY document_date DESC, claim_type
        """, (company,)).fetchall()

        if not claims:
            continue

        print(f"\n=== {company} ({len(claims)} claims) ===")
        for date, doc_type, ct, text, conf, commit, flagged in claims:
            flag_str = "  <- FLAGGED" if flagged else ""
            # Truncate claim text for display
            short_text = text[:60] + "..." if text and len(text) > 60 else (text or "")
            print(f"  {date or 'no-date':12s}  {doc_type:12s}  {ct:40s}  conf={conf:.2f}  {commit:12s}{flag_str}")


def report_c_stats(db: sqlite3.Connection):
    """Report C — Read pass statistics."""
    print("\n" + "=" * 70)
    print("REPORT C — READ PASS STATISTICS")
    print("=" * 70)

    total_docs = db.execute("SELECT COUNT(*) FROM read_pass_log WHERE status='SUCCESS'").fetchone()[0]
    total_claims = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    flagged = db.execute("SELECT COUNT(*) FROM claims WHERE flagged_for_review=1").fetchone()[0]

    cost_row = db.execute("SELECT SUM(cost_usd), SUM(prompt_tokens), SUM(completion_tokens) FROM read_pass_log").fetchone()
    total_cost = cost_row[0] or 0
    total_prompt = cost_row[1] or 0
    total_completion = cost_row[2] or 0

    failed_docs = db.execute("SELECT source_id, company_name, document_type, error_message FROM read_pass_log WHERE status='FAILED'").fetchall()
    skipped_docs = db.execute("SELECT COUNT(*) FROM read_pass_log WHERE status='SKIPPED'").fetchone()[0]

    print(f"  Total documents processed:  {total_docs}")
    print(f"  Total documents skipped:    {skipped_docs}")
    print(f"  Total claims extracted:     {total_claims}")
    print(f"  Claims flagged for review:  {flagged}")
    print(f"  Total prompt tokens:        {total_prompt:,}")
    print(f"  Total completion tokens:    {total_completion:,}")
    print(f"  Total Groq cost:            ${total_cost:.4f}")

    if failed_docs:
        print(f"\n  Failed documents ({len(failed_docs)}):")
        for sid, company, doc_type, err in failed_docs:
            print(f"    id={sid}  {company}  {doc_type}  — {err[:80] if err else 'no error message'}")

    # Claim type diversity
    unique_types = db.execute("SELECT COUNT(DISTINCT claim_type) FROM claims").fetchone()[0]
    print(f"\n  Unique claim types emerged: {unique_types}")

    # Top 10 most common claim types across all document types
    print("\n  Top 10 claim types (all document types):")
    for ct, count in db.execute("""
        SELECT claim_type, COUNT(*) as cnt
        FROM claims GROUP BY claim_type ORDER BY cnt DESC LIMIT 10
    """):
        print(f"    {count:4d}  {ct}")


def run():
    db = sqlite3.connect(str(LEARNING_DB))
    report_a_frequency(db)
    report_b_company(db)
    report_c_stats(db)
    db.close()


if __name__ == "__main__":
    run()
