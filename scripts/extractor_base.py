"""
Shared base class for dual-extraction agents.

Handles API fetching, lookup table loading, metadata extraction, and structural
traversal of ClinicalTrials.gov JSON responses. Subclasses (Agent A and Agent B)
override the interpretation methods to provide independent extraction logic.
"""

import csv
import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_CSV = os.path.join(PROJECT_ROOT, "data", "wisspar_export_2026_02_05.csv")
VACCINE_LOOKUP = os.path.join(PROJECT_ROOT, "data", "vaccine_lookup.csv")
COUNTRY_LOOKUP = os.path.join(PROJECT_ROOT, "data", "country_lookup.csv")
EXTRACTIONS_DIR = os.path.join(PROJECT_ROOT, "data", "extractions")

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

VERSION = "1.0.0"


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


def map_countries(locations, country_lookup):
    """Map a list of location dicts to comma-separated country codes and continents."""
    codes = []
    continents = []
    seen = set()
    for loc in locations:
        country_name = loc.get("country", "")
        if country_name in seen:
            continue
        seen.add(country_name)
        if country_name in country_lookup:
            codes.append(country_lookup[country_name]["code"])
            continents.append(country_lookup[country_name]["continent"])
        elif country_name:
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


def fetch_and_cache(nct_id):
    """Fetch study JSON and cache to disk. Returns parsed JSON."""
    trial_dir = os.path.join(EXTRACTIONS_DIR, nct_id)
    raw_path = os.path.join(trial_dir, "raw.json")

    if os.path.exists(raw_path):
        with open(raw_path, "r", encoding="utf-8") as f:
            return json.load(f)

    data = fetch_study(nct_id)
    if data:
        os.makedirs(trial_dir, exist_ok=True)
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    return data


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
# Shared metadata extraction (deterministic, no interpretation)
# ---------------------------------------------------------------------------

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


def extract_metadata(proto, country_lookup):
    """Extract study metadata from protocolSection. No interpretation -- deterministic."""
    ident = proto.get("identificationModule", {})
    study_name = ident.get("officialTitle", ident.get("briefTitle", ""))
    nct_id = ident.get("nctId", "")

    sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
    sponsor = sponsor_mod.get("leadSponsor", {}).get("name", "")
    resp_party = sponsor_mod.get("responsibleParty", {}).get("organization", sponsor)

    design = proto.get("designModule", {})
    phase = parse_phase(design.get("phases", []))

    elig = proto.get("eligibilityModule", {})
    std_ages = elig.get("stdAges", [])
    age_list_str = json.dumps(std_ages) if std_ages else ""

    locs = proto.get("contactsLocationsModule", {}).get("locations", [])
    country_codes, continents = map_countries(locs, country_lookup)

    return {
        "study_name": study_name,
        "nct_id": nct_id,
        "sponsor": sponsor,
        "resp_party": resp_party,
        "phase": phase,
        "std_ages": std_ages,
        "age_list_str": age_list_str,
        "country_codes": country_codes,
        "continents": continents,
    }


# ---------------------------------------------------------------------------
# Base extractor class
# ---------------------------------------------------------------------------

class ExtractorBase:
    """Base class for extraction agents. Subclasses override interpret_* methods."""

    agent_name = "base"

    def __init__(self, vaccine_lookup, country_lookup):
        self.vaccine_lookup = vaccine_lookup
        self.country_lookup = country_lookup

    def extract(self, data, nct_id):
        """Main extraction entry point. Returns list of row dicts with _source_address."""
        proto = data.get("protocolSection", {})
        results = data.get("resultsSection")
        if not results:
            return []

        meta = extract_metadata(proto, self.country_lookup)
        all_outcomes = (
            results.get("outcomeMeasuresModule", {})
            .get("outcomeMeasures", [])
        )

        rows = []
        for om_idx, om in enumerate(all_outcomes):
            if not self.is_immunogenicity_outcome(om, proto):
                continue

            title = om.get("title", "")
            assay = self.classify_assay(om, proto)
            description = om.get("description", "")
            timeframe = om.get("timeFrame", "")
            time_weeks = self.parse_timeframe_weeks(timeframe, om)

            # Groups
            groups = {g["id"]: g for g in om.get("groups", [])}

            # Participant counts from denoms
            denom_counts = {}
            for d in om.get("denoms", []):
                for c in d.get("counts", []):
                    denom_counts[c["groupId"]] = c["value"]

            # Build vaccine/manufacturer mapping per group
            group_info = {}
            for gid, g in groups.items():
                vac_name, mfr = self.resolve_vaccine(g, om, meta, proto)
                group_info[gid] = {
                    "title": g.get("title", ""),
                    "vaccine": vac_name,
                    "manufacturer": mfr,
                    "participants": denom_counts.get(gid, ""),
                }

            dose_number = self.infer_dose_number(om, meta, proto)
            schedule = self.infer_schedule(om, meta, proto, dose_number)
            dose_desc = self.infer_dose_description(
                om, meta, proto, dose_number, time_weeks
            )

            # Extract measurements
            for cls_idx, cls in enumerate(om.get("classes", [])):
                serotype = cls.get("title", "")
                for cat_idx, cat in enumerate(cls.get("categories", [])):
                    for m_idx, m in enumerate(cat.get("measurements", [])):
                        gid = m.get("groupId", "")
                        if gid not in group_info:
                            continue
                        gi = group_info[gid]

                        source_address = {
                            "outcome_index": om_idx,
                            "class_index": cls_idx,
                            "category_index": cat_idx,
                            "measurement_index": m_idx,
                            "group_id": gid,
                            "outcome_title": title,
                        }

                        row = {
                            "clinical_trial_study_name": meta["study_name"],
                            "clinical_trial_study_id": nct_id,
                            "clinical_trial_sponsor": meta["sponsor"],
                            "clinical_trial_responsible_party": meta["resp_party"],
                            "clinical_trial_phase": meta["phase"],
                            "location_country_code": meta["country_codes"],
                            "location_continent": meta["continents"],
                            "study_eligibility_standard_age_list": meta["age_list_str"],
                            "study_eligibility_ethnicity": "",
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
                            "_source_address": source_address,
                            "_agent": self.agent_name,
                        }
                        rows.append(row)

        return rows

    # ------------------------------------------------------------------
    # Methods subclasses MUST override
    # ------------------------------------------------------------------

    def classify_assay(self, outcome_measure, proto):
        """Determine assay type (OPA, GMC, Unknown) from an outcome measure."""
        raise NotImplementedError

    def is_immunogenicity_outcome(self, outcome_measure, proto):
        """Return True if this outcome measure should be extracted."""
        raise NotImplementedError

    def resolve_vaccine(self, group, outcome_measure, metadata, proto):
        """Return (vaccine_name, manufacturer) for a group."""
        raise NotImplementedError

    def parse_timeframe_weeks(self, timeframe_text, outcome_measure):
        """Convert timeframe text to numeric weeks string."""
        raise NotImplementedError

    def infer_schedule(self, outcome_measure, metadata, proto, dose_number):
        """Infer schedule string (e.g. '1 dose adult', '3+1 child')."""
        raise NotImplementedError

    def infer_dose_number(self, outcome_measure, metadata, proto):
        """Infer dose number string."""
        raise NotImplementedError

    def infer_dose_description(self, outcome_measure, metadata, proto, dose_number, time_weeks):
        """Infer dose description string."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Utility: match vaccine from keyword lookup (shared helper, agents may or may not use)
# ---------------------------------------------------------------------------

def match_vaccine_keyword(text, vaccine_lookup):
    """Try to match text against vaccine lookup keywords. Returns (vaccine_name, manufacturer) or (None, None)."""
    text_upper = text.upper()
    for entry in vaccine_lookup:
        if entry["keyword"].upper() in text_upper:
            return entry["vaccine_name"], entry["manufacturer"]
    return None, None


# ---------------------------------------------------------------------------
# Utility: save/load extraction results as JSON
# ---------------------------------------------------------------------------

def save_extraction(rows, nct_id, agent_name):
    """Save agent extraction results to JSON."""
    trial_dir = os.path.join(EXTRACTIONS_DIR, nct_id)
    os.makedirs(trial_dir, exist_ok=True)
    path = os.path.join(trial_dir, f"agent_{agent_name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "nct_id": nct_id,
            "agent": agent_name,
            "version": VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "row_count": len(rows),
            "rows": rows,
        }, f, indent=2)
    return path


def load_extraction(nct_id, agent_name):
    """Load agent extraction results from JSON."""
    path = os.path.join(EXTRACTIONS_DIR, nct_id, f"agent_{agent_name}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def now_iso():
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()
