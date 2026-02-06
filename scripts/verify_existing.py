"""
Retroactive verification of existing dataset.

Re-extracts all trials in the current CSV using both agents,
compares against existing data, and produces a verification report.

Usage:
    python scripts/verify_existing.py                              # verify all
    python scripts/verify_existing.py NCT06151288                  # verify one
    python scripts/verify_existing.py --summary                    # summary only
    python scripts/verify_existing.py --report data/report.csv     # write report CSV
"""

import argparse
import csv
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from extractor_base import (
    CSV_FIELDNAMES, DEFAULT_CSV, EXTRACTIONS_DIR,
    load_vaccine_lookup, load_country_lookup,
    fetch_and_cache, save_extraction, now_iso,
)
from extractor_a import ExtractorA
from extractor_b import ExtractorB
from reconcile import (
    reconcile, save_reconciliation, generate_review_csv,
    get_final_rows, print_summary,
)

# Trials sourced from manuscripts (not from ClinicalTrials.gov results)
MANUSCRIPT_SOURCES_CSV = os.path.join(
    os.path.dirname(SCRIPT_DIR), "data", "manuscript_sources.csv"
)


def load_manuscript_nct_ids():
    """Load NCT IDs of manuscript-sourced trials to exclude from API verification."""
    ids = set()
    if not os.path.exists(MANUSCRIPT_SOURCES_CSV):
        return ids
    with open(MANUSCRIPT_SOURCES_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nct_id = row.get("nct_id", "").strip()
            if nct_id:
                ids.add(nct_id)
    return ids


def load_existing_data(csv_path):
    """Load existing CSV and group rows by NCT ID.

    Returns dict: nct_id -> list of row dicts.
    """
    by_trial = {}
    if not os.path.exists(csv_path):
        return by_trial
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nct_id = row.get("clinical_trial_study_id", "").strip()
            if nct_id:
                by_trial.setdefault(nct_id, []).append(row)
    return by_trial


def _normalize_value(val):
    """Normalize a numeric value string for comparison (strip trailing zeros)."""
    try:
        return str(float(val))
    except (ValueError, TypeError):
        return str(val).strip()


def match_existing_to_extraction(existing_rows, extracted_rows):
    """Match existing CSV rows to newly extracted rows by content fingerprint.

    Fingerprint: (serotype, group_id, normalized_value) for uniqueness within a trial.
    Values are normalized to handle trailing zero differences (e.g., "6.4" vs "6.40").

    Returns:
        matched: list of (existing_row, extracted_row) tuples
        extra_existing: existing rows with no extraction match
        missing_existing: extracted rows with no existing match
    """
    # Build index from extracted rows
    extracted_index = {}
    for row in extracted_rows:
        key = (
            row.get("outcome_overview_serotype", ""),
            row.get("outcome_overview_id", ""),
            _normalize_value(row.get("outcome_overview_value", "")),
        )
        extracted_index.setdefault(key, []).append(row)

    matched = []
    extra_existing = []
    used_extracted = set()

    for ex_row in existing_rows:
        key = (
            ex_row.get("outcome_overview_serotype", ""),
            ex_row.get("outcome_overview_id", ""),
            _normalize_value(ex_row.get("outcome_overview_value", "")),
        )
        candidates = extracted_index.get(key, [])
        found = False
        for i, ext_row in enumerate(candidates):
            eid = id(ext_row)
            if eid not in used_extracted:
                matched.append((ex_row, ext_row))
                used_extracted.add(eid)
                found = True
                break
        if not found:
            extra_existing.append(ex_row)

    missing_existing = [
        row for row in extracted_rows if id(row) not in used_extracted
    ]

    return matched, extra_existing, missing_existing


def compare_existing_vs_extracted(existing_row, extracted_row):
    """Compare an existing CSV row against a freshly extracted row.

    Returns list of field-level differences.
    """
    diffs = []
    for field in CSV_FIELDNAMES:
        old_val = str(existing_row.get(field, "")).strip()
        new_val = str(extracted_row.get(field, "")).strip()
        if old_val != new_val:
            diffs.append({
                "field": field,
                "existing_value": old_val,
                "extracted_value": new_val,
            })
    return diffs


def verify_trial(nct_id, existing_rows, agent_a, agent_b):
    """Run dual extraction and compare against existing data for one trial.

    Returns a verification result dict.
    """
    print(f"\n  Verifying {nct_id}...")

    # Fetch and run both agents
    data = fetch_and_cache(nct_id)
    if not data:
        return {
            "nct_id": nct_id,
            "status": "FETCH_FAILED",
            "rows_existing": len(existing_rows),
            "rows_agent_a": 0,
            "rows_agent_b": 0,
            "notes": "Could not fetch from API",
        }

    if not data.get("resultsSection"):
        return {
            "nct_id": nct_id,
            "status": "NO_RESULTS",
            "rows_existing": len(existing_rows),
            "rows_agent_a": 0,
            "rows_agent_b": 0,
            "notes": "Trial has no results section in API",
        }

    rows_a = agent_a.extract(data, nct_id)
    save_extraction(rows_a, nct_id, "a")
    rows_b = agent_b.extract(data, nct_id)
    save_extraction(rows_b, nct_id, "b")

    # Reconcile A vs B
    recon = reconcile(rows_a, rows_b, nct_id)
    save_reconciliation(recon, nct_id)

    ab_agreements = recon["summary"]["agreements"]
    ab_categorical = recon["summary"]["categorical_disagreements"]
    ab_numeric = recon["summary"]["numeric_disagreements"]
    ab_selection = recon["summary"]["selection_disagreements"]

    # Compare existing vs Agent A (as representative extraction)
    matched_ex, extra_existing, missing_existing = match_existing_to_extraction(
        existing_rows, rows_a
    )

    # Check field-level differences in matched rows
    field_diffs = 0
    diff_details = []
    for ex_row, ext_row in matched_ex:
        diffs = compare_existing_vs_extracted(ex_row, ext_row)
        if diffs:
            field_diffs += 1
            diff_details.append({
                "serotype": ex_row.get("outcome_overview_serotype", ""),
                "group_id": ex_row.get("outcome_overview_id", ""),
                "diffs": diffs,
            })

    # Determine status
    if (not extra_existing and not missing_existing and field_diffs == 0
            and ab_categorical == 0 and ab_numeric == 0 and ab_selection == 0):
        status = "VERIFIED"
    elif ab_numeric > 0:
        status = "CRITICAL"
    elif extra_existing or missing_existing or ab_categorical or ab_selection:
        status = "NEEDS_REVIEW"
    else:
        status = "FIELD_DIFFS"

    # Generate review CSV if there are disagreements
    if recon["summary"]["status"] != "FULLY_AGREED":
        review_path = os.path.join(EXTRACTIONS_DIR, nct_id, "review.csv")
        generate_review_csv(recon, review_path)

    result = {
        "nct_id": nct_id,
        "status": status,
        "rows_existing": len(existing_rows),
        "rows_agent_a": len(rows_a),
        "rows_agent_b": len(rows_b),
        "rows_matched_ab": recon["summary"]["matched_pairs"],
        "ab_agreements": ab_agreements,
        "ab_categorical_disagreements": ab_categorical,
        "ab_numeric_disagreements": ab_numeric,
        "ab_selection_disagreements": ab_selection,
        "rows_matched_existing": len(matched_ex),
        "rows_extra_existing": len(extra_existing),
        "rows_missing_existing": len(missing_existing),
        "rows_with_field_diffs": field_diffs,
        "notes": "",
    }

    if extra_existing:
        result["notes"] += f"{len(extra_existing)} existing rows not in new extraction. "
    if missing_existing:
        result["notes"] += f"{len(missing_existing)} new rows not in existing CSV. "
    if field_diffs:
        result["notes"] += f"{field_diffs} rows with field differences. "

    return result


def write_report(results, report_path):
    """Write verification results to a CSV report."""
    fieldnames = [
        "nct_id", "status", "rows_existing", "rows_agent_a", "rows_agent_b",
        "rows_matched_ab", "ab_agreements", "ab_categorical_disagreements",
        "ab_numeric_disagreements", "ab_selection_disagreements",
        "rows_matched_existing", "rows_extra_existing", "rows_missing_existing",
        "rows_with_field_diffs", "notes",
    ]
    with open(report_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\n  Verification report written to: {report_path}")


def print_report_summary(results):
    """Print a summary of verification results."""
    verified = sum(1 for r in results if r["status"] == "VERIFIED")
    needs_review = sum(1 for r in results if r["status"] == "NEEDS_REVIEW")
    field_diffs = sum(1 for r in results if r["status"] == "FIELD_DIFFS")
    critical = sum(1 for r in results if r["status"] == "CRITICAL")
    failed = sum(1 for r in results if r["status"] in ("FETCH_FAILED", "NO_RESULTS"))

    total = len(results)
    print(f"\n{'='*60}")
    print(f"VERIFICATION SUMMARY")
    print(f"{'='*60}")
    print(f"  Total trials: {total}")
    print(f"  Verified (exact match): {verified}")
    print(f"  Field differences only: {field_diffs}")
    print(f"  Needs review (disagreements): {needs_review}")
    print(f"  Critical (numeric mismatch): {critical}")
    print(f"  Failed (no API data): {failed}")

    if needs_review or critical:
        print(f"\n  Trials needing attention:")
        for r in results:
            if r["status"] in ("NEEDS_REVIEW", "CRITICAL"):
                print(f"    {r['nct_id']}: {r['status']} - {r['notes']}")


def main():
    parser = argparse.ArgumentParser(
        description="Retroactive verification of existing dataset"
    )
    parser.add_argument(
        "nct_ids",
        nargs="*",
        help="Specific NCT IDs to verify (default: all in dataset)",
    )
    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV,
        help=f"Path to existing CSV (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--report",
        help="Write verification report to this CSV path",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print summary only (no detailed output per trial)",
    )
    args = parser.parse_args()

    # Load existing data
    print("Loading existing dataset...")
    existing_by_trial = load_existing_data(args.csv)
    print(f"  Found {len(existing_by_trial)} distinct trial IDs")
    print(f"  Total rows: {sum(len(v) for v in existing_by_trial.values())}")

    # Exclude manuscript-sourced trials
    manuscript_ids = load_manuscript_nct_ids()
    if manuscript_ids:
        print(f"  Excluding {len(manuscript_ids)} manuscript-sourced trials: {', '.join(manuscript_ids)}")

    # Determine which trials to verify
    if args.nct_ids:
        trial_ids = [nid.strip().upper() for nid in args.nct_ids]
    else:
        trial_ids = sorted(existing_by_trial.keys())

    # Filter out manuscript-sourced trials
    trial_ids = [nid for nid in trial_ids if nid not in manuscript_ids]
    print(f"  Verifying {len(trial_ids)} trials")

    # Load lookups and create agents
    vaccine_lookup = load_vaccine_lookup()
    country_lookup = load_country_lookup()
    agent_a = ExtractorA(vaccine_lookup, country_lookup)
    agent_b = ExtractorB(vaccine_lookup, country_lookup)

    os.makedirs(EXTRACTIONS_DIR, exist_ok=True)

    # Verify each trial
    results = []
    for i, nct_id in enumerate(trial_ids):
        if not args.summary:
            print(f"\n[{i+1}/{len(trial_ids)}] {nct_id}")

        existing_rows = existing_by_trial.get(nct_id, [])
        result = verify_trial(nct_id, existing_rows, agent_a, agent_b)
        results.append(result)

        if not args.summary:
            print(f"  Status: {result['status']}")
            if result["notes"]:
                print(f"  Notes: {result['notes']}")

        # Rate limiting
        if i < len(trial_ids) - 1:
            time.sleep(1)

    # Report
    print_report_summary(results)

    if args.report:
        write_report(results, args.report)


if __name__ == "__main__":
    main()
