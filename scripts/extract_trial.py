"""
Extract immunogenicity outcome data from ClinicalTrials.gov and append to wisspar dataset.

Usage:
    # Single trial:
    python scripts/extract_trial.py NCT06151288

    # Multiple trials:
    python scripts/extract_trial.py NCT06151288 NCT12345678

    # Dry run (preview without writing):
    python scripts/extract_trial.py --dry-run NCT06151288

    # Search for pneumococcal trials with results:
    python scripts/extract_trial.py --search "pneumococcal conjugate vaccine"

    # Specify output CSV (default: data/wisspar_export_2026_02_05.csv):
    python scripts/extract_trial.py --csv data/my_output.csv NCT06151288
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

# ---------------------------------------------------------------------------
# Paths (relative to project root)
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_CSV = os.path.join(PROJECT_ROOT, "data", "wisspar_export_2026_02_05.csv")
VACCINE_LOOKUP = os.path.join(PROJECT_ROOT, "data", "vaccine_lookup.csv")
COUNTRY_LOOKUP = os.path.join(PROJECT_ROOT, "data", "country_lookup.csv")

API_BASE = "https://clinicaltrials.gov/api/v2"
IMMUNO_KEYWORDS = ["OPA", "IGG", "GMT", "GMC", "GEOMETRIC"]

CSV_FIELDNAMES = [
    "clinical_trial_study_name",
    "clinical_trial_study_id",
    "clinical_trial_sponsor",
    "clinical_trial_responsible_party",
    "clinical_trial_phase",
    "location_country_code",
    "location_continent",
    "study_eligibility_standard_age_list",
    "study_eligibility_ethnicity",
    "outcome_overview_title",
    "outcome_overview_id",
    "outcome_overview_description",
    "outcome_overview_time_frame",
    "outcome_overview_assay",
    "outcome_overview_dose_number",
    "outcome_overview_participants",
    "outcome_overview_serotype",
    "outcome_overview_value",
    "outcome_overview_upper_limit",
    "outcome_overview_lower_limit",
    "outcome_overview_ratio",
    "outcome_overview_vaccine",
    "outcome_overview_immunocompromised_population",
    "outcome_overview_manufacturer",
    "outcome_overview_dose_description",
    "outcome_overview_schedule",
    "outcome_overview_time_frame_weeks",
    "outcome_overview_confidence_interval",
    "outcome_overview_percent_responders",
]


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

def load_vaccine_lookup():
    """Load vaccine keyword -> (vaccine_name, manufacturer) mapping."""
    lookup = []
    if not os.path.exists(VACCINE_LOOKUP):
        return lookup
    with open(VACCINE_LOOKUP, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lookup.append(row)
    return lookup


def load_country_lookup():
    """Load country_name -> (country_code, continent) mapping."""
    lookup = {}
    if not os.path.exists(COUNTRY_LOOKUP):
        return lookup
    with open(COUNTRY_LOOKUP, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lookup[row["country_name"]] = {
                "code": row["country_code"],
                "continent": row["continent"],
            }
    return lookup


def match_vaccine(text, vaccine_lookup):
    """Try to match a text against vaccine lookup keywords. Returns (vaccine_name, manufacturer) or None."""
    text_upper = text.upper()
    for entry in vaccine_lookup:
        if entry["keyword"].upper() in text_upper:
            return entry["vaccine_name"], entry["manufacturer"]
    return None, None


def map_countries(locations, country_lookup):
    """Map a list of location dicts to comma-separated country codes and continents."""
    codes = []
    continents = []
    for loc in locations:
        country_name = loc.get("country", "")
        if country_name in country_lookup:
            codes.append(country_lookup[country_name]["code"])
            continents.append(country_lookup[country_name]["continent"])
        else:
            codes.append("")
            continents.append("")
    return ",".join(codes), ",".join(continents)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(url):
    """Fetch JSON from the ClinicalTrials.gov API."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  ERROR: HTTP {e.code} for {url}")
        return None
    except urllib.error.URLError as e:
        print(f"  ERROR: {e.reason} for {url}")
        return None


def fetch_study(nct_id):
    """Fetch a single study by NCT ID."""
    url = f"{API_BASE}/studies/{nct_id}?fields=resultsSection,protocolSection"
    return api_get(url)


def search_studies(query, page_size=50):
    """Search for studies with results. Returns list of NCT IDs."""
    params = urllib.parse.urlencode({
        "query.term": query,
        "fields": "protocolSection.identificationModule",
        "filter.overallStatus": "COMPLETED",
        "pageSize": page_size,
        "countTotal": "true",
    })
    url = f"{API_BASE}/studies?{params}"
    data = api_get(url)
    if not data:
        return []
    total = data.get("totalCount", 0)
    studies = data.get("studies", [])
    nct_ids = []
    for s in studies:
        nct_id = (
            s.get("protocolSection", {})
            .get("identificationModule", {})
            .get("nctId", "")
        )
        if nct_id:
            nct_ids.append(nct_id)
    print(f"  Search returned {total} total results, fetched {len(nct_ids)} IDs")
    return nct_ids


# ---------------------------------------------------------------------------
# Extraction logic
# ---------------------------------------------------------------------------

def classify_assay(title):
    """Determine assay type from outcome measure title."""
    t = title.upper()
    if "OPA" in t:
        return "OPA"
    if "IGG" in t or "GMC" in t:
        return "GMC"
    if "GMT" in t:
        return "OPA"
    return "Unknown"


def is_immunogenicity_outcome(title):
    """Check if an outcome measure title represents immunogenicity data."""
    t = title.upper()
    return any(kw in t for kw in IMMUNO_KEYWORDS)


def parse_phase(phases):
    """Convert API phase list to display string."""
    if not phases:
        return ""
    mapping = {
        "EARLY_PHASE1": "Early Phase 1",
        "PHASE1": "Phase 1",
        "PHASE2": "Phase 2",
        "PHASE3": "Phase 3",
        "PHASE4": "Phase 4",
        "NA": "",
    }
    names = [mapping.get(p, p) for p in phases]
    names = [n for n in names if n]
    return "/".join(names)


def guess_schedule(age_list, dose_count):
    """Guess schedule string from age group and dose count."""
    is_child = any(
        a.upper() in ("CHILD",) for a in (age_list or [])
    )
    if is_child:
        if dose_count and int(dose_count) > 1:
            primary = int(dose_count) - 1
            return f"{primary}+1 child"
        return "Not selected"
    return f"{dose_count} dose adult" if dose_count else "1 dose adult"


def guess_time_frame_weeks(timeframe):
    """Estimate numeric weeks from a timeframe string."""
    t = timeframe.lower()
    if "1 month" in t or "30 day" in t or "4 week" in t:
        return "4"
    if "2 month" in t or "8 week" in t:
        return "8"
    if "6 month" in t or "26 week" in t:
        return "26"
    if "1 year" in t or "12 month" in t or "52 week" in t:
        return "52"
    if "2 year" in t or "24 month" in t:
        return "104"
    # Try to extract weeks directly
    import re
    m = re.search(r"(\d+)\s*week", t)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)\s*month", t)
    if m:
        return str(int(m.group(1)) * 4)
    m = re.search(r"(\d+)\s*day", t)
    if m:
        return str(round(int(m.group(1)) / 7))
    return ""


def extract_trial(nct_id, vaccine_lookup, country_lookup):
    """Extract immunogenicity rows from a single trial. Returns list of row dicts."""
    print(f"\nFetching {nct_id}...")
    data = fetch_study(nct_id)
    if not data:
        print(f"  SKIP: Could not fetch {nct_id}")
        return []

    proto = data.get("protocolSection", {})
    results = data.get("resultsSection")
    if not results:
        print(f"  SKIP: {nct_id} has no results posted")
        return []

    # -- Metadata --
    ident = proto.get("identificationModule", {})
    study_name = ident.get("officialTitle", ident.get("briefTitle", ""))
    sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
    sponsor = sponsor_mod.get("leadSponsor", {}).get("name", "")
    resp_party = sponsor_mod.get("responsibleParty", {}).get("organization", sponsor)
    design = proto.get("designModule", {})
    phase = parse_phase(design.get("phases", []))
    elig = proto.get("eligibilityModule", {})
    std_ages = elig.get("stdAges", [])
    age_list_str = json.dumps(std_ages) if std_ages else ""

    # Locations
    locs = proto.get("contactsLocationsModule", {}).get("locations", [])
    country_codes, continents = map_countries(locs, country_lookup)

    # Ethnicity (not always available)
    ethnicity = ""

    # -- Outcome measures --
    om_module = results.get("outcomeMeasuresModule", {})
    all_outcomes = om_module.get("outcomeMeasures", [])
    immuno_outcomes = [om for om in all_outcomes if is_immunogenicity_outcome(om.get("title", ""))]

    if not immuno_outcomes:
        print(f"  SKIP: {nct_id} has no immunogenicity outcome measures")
        print(f"  Available outcomes:")
        for om in all_outcomes:
            print(f"    - {om.get('title', 'N/A')}")
        return []

    print(f"  Found {len(immuno_outcomes)} immunogenicity outcome(s):")
    for om in immuno_outcomes:
        print(f"    - {om.get('title', '')} ({len(om.get('classes', []))} serotypes)")

    rows = []
    for om in immuno_outcomes:
        title = om.get("title", "")
        assay = classify_assay(title)
        description = om.get("description", "")
        timeframe = om.get("timeFrame", "")
        time_weeks = guess_time_frame_weeks(timeframe)

        # Groups
        groups = {g["id"]: g for g in om.get("groups", [])}

        # Participant counts from denoms
        denom_counts = {}
        for d in om.get("denoms", []):
            for c in d.get("counts", []):
                denom_counts[c["groupId"]] = c["value"]

        # Try to determine dose count from group descriptions
        dose_number = "1"  # default for adult single-dose

        # Build vaccine/manufacturer mapping per group
        group_info = {}
        for gid, g in groups.items():
            g_title = g.get("title", "")
            g_desc = g.get("description", "")
            combined_text = f"{g_title} {g_desc}"

            vac_name, mfr = match_vaccine(combined_text, vaccine_lookup)
            if not vac_name:
                # Fallback: use group title as vaccine name, sponsor as manufacturer
                vac_name = g_title
                mfr = sponsor

            group_info[gid] = {
                "title": g_title,
                "vaccine": vac_name,
                "manufacturer": mfr,
                "participants": denom_counts.get(gid, ""),
            }

        schedule = guess_schedule(std_ages, dose_number)
        dose_desc = f"{time_weeks}w post dose {dose_number} adult" if time_weeks else f"post dose {dose_number}"
        if any(a.upper() == "CHILD" for a in std_ages):
            dose_desc = dose_desc.replace("adult", "child")

        # Standardize dose description to match existing patterns
        if time_weeks == "4":
            dose_desc = f"1m post dose {dose_number} adult"
            if any(a.upper() == "CHILD" for a in std_ages):
                dose_desc = dose_desc.replace("adult", "child")

        # Extract measurements
        for cls in om.get("classes", []):
            serotype = cls.get("title", "")
            for cat in cls.get("categories", []):
                for m in cat.get("measurements", []):
                    gid = m.get("groupId", "")
                    if gid not in group_info:
                        continue
                    gi = group_info[gid]

                    row = {
                        "clinical_trial_study_name": study_name,
                        "clinical_trial_study_id": nct_id,
                        "clinical_trial_sponsor": sponsor,
                        "clinical_trial_responsible_party": resp_party,
                        "clinical_trial_phase": phase,
                        "location_country_code": country_codes,
                        "location_continent": continents,
                        "study_eligibility_standard_age_list": age_list_str,
                        "study_eligibility_ethnicity": ethnicity,
                        "outcome_overview_title": gi["title"],
                        "outcome_overview_id": gid,
                        "outcome_overview_description": description,
                        "outcome_overview_time_frame": timeframe,
                        "outcome_overview_assay": assay,
                        "outcome_overview_dose_number": dose_number,
                        "outcome_overview_participants": gi["participants"],
                        "outcome_overview_serotype": serotype,
                        "outcome_overview_value": m.get("value", ""),
                        "outcome_overview_upper_limit": m.get("upperLimit", ""),
                        "outcome_overview_lower_limit": m.get("lowerLimit", ""),
                        "outcome_overview_ratio": "",
                        "outcome_overview_vaccine": gi["vaccine"],
                        "outcome_overview_immunocompromised_population": "",
                        "outcome_overview_manufacturer": gi["manufacturer"],
                        "outcome_overview_dose_description": dose_desc,
                        "outcome_overview_schedule": schedule,
                        "outcome_overview_time_frame_weeks": time_weeks,
                        "outcome_overview_confidence_interval": "",
                        "outcome_overview_percent_responders": "0",
                    }
                    rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_rows(rows, nct_id):
    """Validate extracted rows and print a summary report."""
    if not rows:
        print(f"\n  VALIDATION: No rows to validate for {nct_id}")
        return False

    assays = {}
    serotypes = set()
    groups = set()
    issues = []

    for r in rows:
        assay = r["outcome_overview_assay"]
        assays[assay] = assays.get(assay, 0) + 1
        serotypes.add(r["outcome_overview_serotype"])
        groups.add(r["outcome_overview_title"])

        # Check for missing values
        if not r["outcome_overview_value"]:
            issues.append(f"Missing value for {r['outcome_overview_serotype']} / {r['outcome_overview_title']} / {assay}")
        if not r["outcome_overview_serotype"]:
            issues.append(f"Empty serotype in group {r['outcome_overview_title']} / {assay}")

    print(f"\n  VALIDATION REPORT for {nct_id}:")
    print(f"    Total rows: {len(rows)}")
    print(f"    Assays: {assays}")
    print(f"    Serotypes ({len(serotypes)}): {sorted(serotypes, key=lambda x: (not x.replace('F','').replace('A','').replace('B','').replace('C','').replace('N','').replace('V','').isdigit(), x))}")
    print(f"    Groups ({len(groups)}): {sorted(groups)}")
    expected = len(serotypes) * len(groups) * len(assays)
    print(f"    Expected rows (serotypes x groups x assays): {expected}")
    if len(rows) != expected:
        issues.append(f"Row count mismatch: got {len(rows)}, expected {expected}")

    if issues:
        print(f"    ISSUES ({len(issues)}):")
        for issue in issues:
            print(f"      - {issue}")
    else:
        print(f"    No issues found.")

    return len(issues) == 0


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


# ---------------------------------------------------------------------------
# CSV operations
# ---------------------------------------------------------------------------

def append_to_csv(rows, csv_path):
    """Append rows to the CSV file."""
    if not os.path.exists(csv_path):
        print(f"  Creating new CSV: {csv_path}")
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
    else:
        # Read existing, append, write back
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing = list(reader)
        all_rows = existing + rows
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(all_rows)

    print(f"  Appended {len(rows)} rows to {csv_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract immunogenicity data from ClinicalTrials.gov"
    )
    parser.add_argument(
        "nct_ids",
        nargs="*",
        help="One or more NCT IDs to extract (e.g., NCT06151288)",
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
        help="Preview extracted rows without writing to CSV",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing data for duplicate NCT IDs",
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

    # Process each trial
    all_new_rows = []
    skipped = []
    for nct_id in nct_ids:
        nct_id = nct_id.strip().upper()
        if not nct_id.startswith("NCT"):
            print(f"\n  SKIP: '{nct_id}' is not a valid NCT ID")
            skipped.append(nct_id)
            continue

        # Check duplicates
        if not args.force and check_duplicates(nct_id, args.csv):
            print(f"\n  SKIP: {nct_id} already exists in {args.csv} (use --force to overwrite)")
            skipped.append(nct_id)
            continue

        rows = extract_trial(nct_id, vaccine_lookup, country_lookup)
        if rows:
            validate_rows(rows, nct_id)
            all_new_rows.extend(rows)
        else:
            skipped.append(nct_id)

        # Rate limiting
        if len(nct_ids) > 1:
            time.sleep(1)

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"  Total new rows: {len(all_new_rows)}")
    print(f"  Trials extracted: {len(nct_ids) - len(skipped)}")
    print(f"  Trials skipped: {len(skipped)}")
    if skipped:
        print(f"  Skipped IDs: {', '.join(skipped)}")

    if all_new_rows and not args.dry_run:
        append_to_csv(all_new_rows, args.csv)
        print(f"\nDone! Data written to {args.csv}")
    elif all_new_rows and args.dry_run:
        print(f"\nDRY RUN: Would have appended {len(all_new_rows)} rows to {args.csv}")
        print("Sample row:")
        for k, v in all_new_rows[0].items():
            print(f"  {k}: {v}")
    else:
        print("\nNo data to write.")


if __name__ == "__main__":
    main()
