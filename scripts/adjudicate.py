"""
Human adjudication interface for resolving extraction disagreements.

Reads review.csv with human decisions and applies them to the reconciliation,
or runs in interactive mode for terminal-based resolution.

Usage:
    python scripts/adjudicate.py NCT06151288              # from review.csv
    python scripts/adjudicate.py --interactive NCT06151288 # terminal prompts
    python scripts/adjudicate.py --status NCT06151288      # check status
    python scripts/adjudicate.py --accept-a NCT06151288    # accept all Agent A values
    python scripts/adjudicate.py --accept-b NCT06151288    # accept all Agent B values
"""

import argparse
import csv
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from extractor_base import (
    CSV_FIELDNAMES, DEFAULT_CSV, EXTRACTIONS_DIR, now_iso,
)
from reconcile import (
    load_reconciliation, save_reconciliation,
    get_final_rows, print_summary,
)


def apply_review_csv(reconciliation, review_path):
    """Read edited review.csv and apply human decisions to reconciliation.

    Returns number of resolved disagreements.
    """
    if not os.path.exists(review_path):
        print(f"  ERROR: Review file not found: {review_path}")
        return 0

    # Read review decisions
    decisions = {}  # (row_index, field) -> (chosen_value, notes)
    with open(review_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            chosen = row.get("chosen_value", "").strip()
            if not chosen:
                continue
            key = (int(row["row_index"]), row["field"])
            decisions[key] = (chosen, row.get("notes", ""))

    if not decisions:
        print("  No decisions found in review CSV. Fill in the 'chosen_value' column.")
        return 0

    resolved_count = 0
    timestamp = now_iso()

    for rr in reconciliation["rows"]:
        if rr["resolution"] != "pending_review":
            continue

        all_resolved = True
        for d in rr["disagreements"]:
            key = (rr["row_index"], d["field"])
            if key in decisions:
                chosen, notes = decisions[key]
                d["resolution"] = "human_adjudicated"
                d["chosen_value"] = chosen
                d["resolved_by"] = "user"
                d["resolved_at"] = timestamp
                d["notes"] = notes
            else:
                all_resolved = False

        if all_resolved:
            # Build final row from resolved decisions
            final_row = _build_final_row(rr)
            if final_row:
                rr["final_row"] = final_row
                rr["resolution"] = "human_adjudicated"
                rr["resolved_by"] = "user"
                rr["resolved_at"] = timestamp
                resolved_count += 1

    # Update summary status
    _, pending = get_final_rows(reconciliation)
    if pending == 0:
        reconciliation["summary"]["status"] = "HUMAN_ADJUDICATED"
    else:
        reconciliation["summary"]["status"] = "PARTIALLY_RESOLVED"

    return resolved_count


def apply_accept_agent(reconciliation, agent):
    """Accept all values from one agent (a or b) for unresolved rows.

    Returns number of resolved rows.
    """
    resolved_count = 0
    timestamp = now_iso()

    for rr in reconciliation["rows"]:
        if rr["resolution"] != "pending_review":
            continue

        if agent == "a" and rr["agent_a_row"]:
            source_row = rr["agent_a_row"]
        elif agent == "b" and rr["agent_b_row"]:
            source_row = rr["agent_b_row"]
        else:
            continue

        rr["final_row"] = {k: source_row[k] for k in CSV_FIELDNAMES}
        rr["resolution"] = f"accepted_agent_{agent}"
        rr["resolved_by"] = "user"
        rr["resolved_at"] = timestamp

        for d in rr["disagreements"]:
            d["resolution"] = f"accepted_agent_{agent}"
            d["chosen_value"] = source_row.get(d["field"], d[f"agent_{agent}_value"])
            d["resolved_by"] = "user"
            d["resolved_at"] = timestamp

        resolved_count += 1

    reconciliation["summary"]["status"] = "HUMAN_ADJUDICATED"
    return resolved_count


def interactive_adjudicate(reconciliation):
    """Present each disagreement as a terminal prompt.

    Returns number of resolved rows.
    """
    pending_rows = [rr for rr in reconciliation["rows"] if rr["resolution"] == "pending_review"]

    if not pending_rows:
        print("  No rows pending review.")
        return 0

    print(f"\n  {len(pending_rows)} rows to review.\n")
    resolved_count = 0
    timestamp = now_iso()

    for rr in pending_rows:
        addr = rr.get("source_address", {})
        agent_a = rr.get("agent_a_row") or {}
        agent_b = rr.get("agent_b_row") or {}
        context = agent_a or agent_b

        print(f"  --- Row {rr['row_index']} ---")
        print(f"  Serotype: {context.get('outcome_overview_serotype', 'N/A')}")
        print(f"  Group ID: {addr.get('group_id', 'N/A')}")
        print(f"  Value: {context.get('outcome_overview_value', 'N/A')}")
        print(f"  Outcome: {addr.get('outcome_title', 'N/A')[:80]}")

        if rr["status"] == "SELECTION_DISAGREE":
            included_by = "A" if rr.get("agent_a_row") else "B"
            print(f"\n  Agent {included_by} included this row; the other excluded it.")
            choice = input("  Include this row? [y/n/s(kip)]: ").strip().lower()
            if choice == "y":
                source = rr["agent_a_row"] or rr["agent_b_row"]
                rr["final_row"] = {k: source[k] for k in CSV_FIELDNAMES}
                rr["resolution"] = "human_adjudicated"
                rr["resolved_by"] = "user"
                rr["resolved_at"] = timestamp
                resolved_count += 1
            elif choice == "n":
                rr["final_row"] = None  # Explicitly excluded
                rr["resolution"] = "human_excluded"
                rr["resolved_by"] = "user"
                rr["resolved_at"] = timestamp
                resolved_count += 1
            else:
                print("  Skipped.")
            print()
            continue

        # Regular disagreements
        all_resolved = True
        for d in rr["disagreements"]:
            print(f"\n  Field: {d['field']}")
            print(f"    Agent A: {d['agent_a_value']}")
            print(f"    Agent B: {d['agent_b_value']}")
            choice = input("  Choose [a/b/custom/s(kip)]: ").strip().lower()

            if choice == "a":
                d["chosen_value"] = d["agent_a_value"]
                d["resolution"] = "human_adjudicated"
            elif choice == "b":
                d["chosen_value"] = d["agent_b_value"]
                d["resolution"] = "human_adjudicated"
            elif choice == "s":
                all_resolved = False
                continue
            else:
                d["chosen_value"] = choice
                d["resolution"] = "human_adjudicated"

            d["resolved_by"] = "user"
            d["resolved_at"] = timestamp
            notes = input("  Notes (optional): ").strip()
            if notes:
                d["notes"] = notes

        if all_resolved:
            final_row = _build_final_row(rr)
            if final_row:
                rr["final_row"] = final_row
                rr["resolution"] = "human_adjudicated"
                rr["resolved_by"] = "user"
                rr["resolved_at"] = timestamp
                resolved_count += 1

        print()

    return resolved_count


def _build_final_row(rr):
    """Build a final row from a reconciliation row entry using resolved disagreements."""
    # Start with Agent A row as base (or Agent B if A is missing)
    base = rr.get("agent_a_row") or rr.get("agent_b_row")
    if not base:
        return None

    final = {k: base.get(k, "") for k in CSV_FIELDNAMES}

    # Apply chosen values from resolved disagreements
    for d in rr.get("disagreements", []):
        field = d.get("field", "")
        chosen = d.get("chosen_value")
        if chosen is not None and field in CSV_FIELDNAMES:
            final[field] = chosen

    return final


def show_status(nct_id):
    """Print the current resolution status for a trial."""
    recon = load_reconciliation(nct_id)
    if not recon:
        print(f"  No reconciliation found for {nct_id}")
        return

    print_summary(recon)

    final_rows, pending = get_final_rows(recon)
    print(f"\n    Finalized rows: {len(final_rows)}")
    print(f"    Pending rows: {pending}")

    if pending > 0:
        review_path = os.path.join(EXTRACTIONS_DIR, nct_id, "review.csv")
        if os.path.exists(review_path):
            print(f"\n    Review file: {review_path}")
        print(f"    Run 'python scripts/adjudicate.py {nct_id}' to resolve.")


def append_final_rows(nct_id, csv_path):
    """Append finalized rows from a resolved reconciliation to the CSV."""
    recon = load_reconciliation(nct_id)
    if not recon:
        print(f"  No reconciliation found for {nct_id}")
        return 0

    final_rows, pending = get_final_rows(recon)
    if pending > 0:
        print(f"  WARNING: {pending} rows still pending review. Only appending resolved rows.")

    if not final_rows:
        print(f"  No final rows to append for {nct_id}")
        return 0

    if not os.path.exists(csv_path):
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(final_rows)
    else:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing = list(reader)
        all_rows = existing + final_rows
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(all_rows)

    print(f"  Appended {len(final_rows)} resolved rows to {csv_path}")
    return len(final_rows)


def main():
    parser = argparse.ArgumentParser(
        description="Resolve extraction disagreements via human adjudication"
    )
    parser.add_argument(
        "nct_ids",
        nargs="+",
        help="NCT IDs to adjudicate",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Interactive terminal prompts for each disagreement",
    )
    parser.add_argument(
        "--accept-a",
        action="store_true",
        help="Accept all Agent A values for unresolved rows",
    )
    parser.add_argument(
        "--accept-b",
        action="store_true",
        help="Accept all Agent B values for unresolved rows",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current resolution status",
    )
    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV,
        help=f"Output CSV for resolved rows (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append resolved rows to CSV after adjudication",
    )
    args = parser.parse_args()

    for nct_id in args.nct_ids:
        nct_id = nct_id.strip().upper()
        print(f"\n{'='*60}")
        print(f"ADJUDICATION: {nct_id}")
        print(f"{'='*60}")

        if args.status:
            show_status(nct_id)
            continue

        recon = load_reconciliation(nct_id)
        if not recon:
            print(f"  No reconciliation found for {nct_id}")
            print(f"  Run 'python scripts/dual_extract.py {nct_id}' first.")
            continue

        if args.accept_a:
            n = apply_accept_agent(recon, "a")
            print(f"  Accepted Agent A values for {n} rows")
        elif args.accept_b:
            n = apply_accept_agent(recon, "b")
            print(f"  Accepted Agent B values for {n} rows")
        elif args.interactive:
            n = interactive_adjudicate(recon)
            print(f"  Resolved {n} rows interactively")
        else:
            # CSV-based adjudication
            review_path = os.path.join(EXTRACTIONS_DIR, nct_id, "review.csv")
            n = apply_review_csv(recon, review_path)
            print(f"  Applied {n} decisions from review CSV")

        # Save updated reconciliation
        save_reconciliation(recon, nct_id)

        # Show updated status
        final_rows, pending = get_final_rows(recon)
        print(f"  Finalized: {len(final_rows)} rows | Pending: {pending} rows")

        # Append to CSV if requested
        if args.append and final_rows:
            append_final_rows(nct_id, args.csv)


if __name__ == "__main__":
    main()
