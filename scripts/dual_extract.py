"""
Dual-extraction orchestrator.

Fetches trial JSON once, runs both extraction agents independently,
reconciles the results, and handles auto-accept vs human review.

Usage:
    python scripts/dual_extract.py NCT06151288
    python scripts/dual_extract.py NCT06151288 NCT03197376
    python scripts/dual_extract.py --dry-run NCT06151288
    python scripts/dual_extract.py --force NCT06151288
    python scripts/dual_extract.py --search "pneumococcal conjugate vaccine"
    python scripts/dual_extract.py --csv data/output.csv NCT06151288
"""

import argparse
import csv
import os
import sys
import time

# Add scripts dir to path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from extractor_base import (
    CSV_FIELDNAMES, DEFAULT_CSV, EXTRACTIONS_DIR,
    load_vaccine_lookup, load_country_lookup,
    fetch_and_cache, search_studies,
    save_extraction, now_iso,
)
from extractor_a import ExtractorA
from extractor_b import ExtractorB
from reconcile import (
    reconcile, save_reconciliation, generate_review_csv,
    get_final_rows, print_summary,
)


def check_duplicates(nct_id, csv_path):
    """Check if an NCT ID already exists in the CSV."""
    if not os.path.exists(csv_path):
        return False
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("clinical_trial_study_id") == nct_id:
                return True
    return False


def append_to_csv(rows, csv_path):
    """Append rows to the CSV file."""
    if not os.path.exists(csv_path):
        print(f"  Creating new CSV: {csv_path}")
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
    else:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing = list(reader)
        all_rows = existing + rows
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(all_rows)
    print(f"  Appended {len(rows)} rows to {csv_path}")


def process_trial(nct_id, agent_a, agent_b, dry_run=False, csv_path=DEFAULT_CSV):
    """Run dual extraction + reconciliation for a single trial.

    Returns (final_rows, pending_count, reconciliation).
    """
    print(f"\n{'='*60}")
    print(f"DUAL EXTRACTION: {nct_id}")
    print(f"{'='*60}")

    # Step 1: Fetch and cache JSON
    print(f"\n  Fetching {nct_id}...")
    data = fetch_and_cache(nct_id)
    if not data:
        print(f"  SKIP: Could not fetch {nct_id}")
        return [], 0, None

    if not data.get("resultsSection"):
        print(f"  SKIP: {nct_id} has no results posted")
        return [], 0, None

    # Step 2: Run Agent A
    print(f"\n  Running Agent A (keyword-first)...")
    rows_a = agent_a.extract(data, nct_id)
    save_extraction(rows_a, nct_id, "a")
    print(f"    Agent A produced {len(rows_a)} rows")

    # Step 3: Run Agent B
    print(f"\n  Running Agent B (schema-aware)...")
    rows_b = agent_b.extract(data, nct_id)
    save_extraction(rows_b, nct_id, "b")
    print(f"    Agent B produced {len(rows_b)} rows")

    # Step 4: Reconcile
    print(f"\n  Reconciling...")
    result = reconcile(rows_a, rows_b, nct_id)
    save_reconciliation(result, nct_id)
    print_summary(result)

    # Step 5: Handle disagreements
    summary = result["summary"]
    final_rows, pending = get_final_rows(result)

    if summary["status"] == "FULLY_AGREED":
        print(f"\n  All {summary['agreements']} rows agreed. Auto-accepted.")
    elif pending > 0:
        # Generate review CSV
        review_path = os.path.join(EXTRACTIONS_DIR, nct_id, "review.csv")
        n_review = generate_review_csv(result, review_path)
        print(f"\n  {n_review} disagreements written to: {review_path}")
        print(f"  Edit the 'chosen_value' column and run:")
        print(f"    python scripts/adjudicate.py {nct_id}")

    return final_rows, pending, result


def main():
    parser = argparse.ArgumentParser(
        description="Dual-extraction pipeline for immunogenicity data"
    )
    parser.add_argument(
        "nct_ids",
        nargs="*",
        help="One or more NCT IDs to extract",
    )
    parser.add_argument(
        "--search",
        help="Search ClinicalTrials.gov for trials matching this query",
    )
    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV,
        help=f"Path to output CSV (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview extraction without writing to CSV",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract trials already in the dataset",
    )
    args = parser.parse_args()

    # Collect NCT IDs
    nct_ids = list(args.nct_ids or [])
    if args.search:
        print(f"Searching ClinicalTrials.gov: '{args.search}'...")
        search_ids = search_studies(args.search)
        nct_ids.extend(search_ids)

    if not nct_ids:
        parser.error("No NCT IDs provided. Specify IDs directly or use --search.")

    # De-duplicate
    nct_ids = list(dict.fromkeys(nct_ids))
    print(f"\nProcessing {len(nct_ids)} trial(s): {', '.join(nct_ids)}")

    # Load lookups
    vaccine_lookup = load_vaccine_lookup()
    country_lookup = load_country_lookup()
    print(f"Loaded {len(vaccine_lookup)} vaccine lookup entries")
    print(f"Loaded {len(country_lookup)} country lookup entries")

    # Create agents
    agent_a = ExtractorA(vaccine_lookup, country_lookup)
    agent_b = ExtractorB(vaccine_lookup, country_lookup)

    # Ensure extractions dir exists
    os.makedirs(EXTRACTIONS_DIR, exist_ok=True)

    # Process trials
    all_accepted = []
    pending_trials = []
    skipped = []

    for nct_id in nct_ids:
        nct_id = nct_id.strip().upper()
        if not nct_id.startswith("NCT"):
            print(f"\n  SKIP: '{nct_id}' is not a valid NCT ID")
            skipped.append(nct_id)
            continue

        if not args.force and check_duplicates(nct_id, args.csv):
            print(f"\n  SKIP: {nct_id} already exists in {args.csv} (use --force to overwrite)")
            skipped.append(nct_id)
            continue

        final_rows, pending, result = process_trial(
            nct_id, agent_a, agent_b, dry_run=args.dry_run, csv_path=args.csv
        )

        if not result:
            skipped.append(nct_id)
        elif pending > 0:
            pending_trials.append((nct_id, pending))
            # Still add agreed rows
            all_accepted.extend(final_rows)
        else:
            all_accepted.extend(final_rows)

        # Rate limiting between trials
        if len(nct_ids) > 1:
            time.sleep(1)

    # Summary
    print(f"\n{'='*60}")
    print(f"DUAL EXTRACTION SUMMARY")
    print(f"{'='*60}")
    print(f"  Trials processed: {len(nct_ids) - len(skipped)}")
    print(f"  Trials skipped: {len(skipped)}")
    print(f"  Rows auto-accepted: {len(all_accepted)}")

    if pending_trials:
        print(f"\n  TRIALS NEEDING REVIEW:")
        for nct_id, count in pending_trials:
            print(f"    {nct_id}: {count} rows pending")
        print(f"\n  Run 'python scripts/adjudicate.py <NCT_ID>' to resolve.")

    # Write accepted rows
    if all_accepted and not args.dry_run:
        append_to_csv(all_accepted, args.csv)
        print(f"\nDone! {len(all_accepted)} agreed rows written to {args.csv}")
    elif all_accepted and args.dry_run:
        print(f"\nDRY RUN: Would append {len(all_accepted)} rows to {args.csv}")
        print("Sample row:")
        for k, v in all_accepted[0].items():
            print(f"  {k}: {v}")
    elif not pending_trials:
        print("\nNo data to write.")


if __name__ == "__main__":
    main()
