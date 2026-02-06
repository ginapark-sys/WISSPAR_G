"""
Agent B: Schema-aware extraction logic.

This agent uses API metadata fields before falling back to keywords:
- Assay classification from unitOfMeasure, paramType, and dispersionType
- Vaccine matching via interventionsModule arm-group mapping
- Timeframe parsing with structured regex and unit conversion
- Schedule inference from designModule arm counts
"""

import re
from extractor_base import ExtractorBase, match_vaccine_keyword, IMMUNO_KEYWORDS


# Unit conversion table: (number, unit) -> weeks
UNIT_TO_WEEKS = {
    "week": 1,
    "weeks": 1,
    "month": 4.33,
    "months": 4.33,
    "day": 1 / 7,
    "days": 1 / 7,
    "year": 52,
    "years": 52,
}


class ExtractorB(ExtractorBase):
    """Schema-aware extraction agent (independent implementation)."""

    agent_name = "b"

    def classify_assay(self, outcome_measure, proto):
        """Use explicit title keywords first, then schema fields, then keyword fallback.

        Title keywords (OPA, IgG) are checked first because they are
        unambiguous and override potentially incorrect unitOfMeasure metadata
        (e.g., OPA outcomes mislabeled as mcg/mL in the API).
        """
        title = outcome_measure.get("title", "").upper()
        unit = outcome_measure.get("unitOfMeasure", "").upper()
        param_type = outcome_measure.get("paramType", "").upper()

        # Step 1: Explicit title keywords (highest confidence)
        if "OPA" in title or "OPSONOPHAGOCYTIC" in title:
            return "OPA"
        if "IGG" in title or "IMMUNOGLOBULIN" in title:
            return "GMC"

        # Step 2: Schema-based (unitOfMeasure) when title is ambiguous
        if unit:
            if "TITER" in unit or "1/" in unit or "DILUTION" in unit:
                return "OPA"
            if "UG/ML" in unit or "MCG/ML" in unit or "µG/ML" in unit:
                return "GMC"
            if "EU/ML" in unit:
                return "GMC"

        # Step 3: paramType + title disambiguation
        if "GEOMETRIC" in param_type:
            if "GMC" in title or "CONCENTRATION" in title:
                return "GMC"
            if "GMT" in title or "TITER" in title:
                return "OPA"

        # Step 4: Keyword fallback
        if "GMC" in title:
            return "GMC"
        if "GMT" in title:
            return "OPA"

        return "Unknown"

    def is_immunogenicity_outcome(self, outcome_measure, proto):
        """Check title keywords AND paramType for geometric mean indicators."""
        title = outcome_measure.get("title", "").upper()

        # Standard keyword check
        if any(kw in title for kw in IMMUNO_KEYWORDS):
            return True

        # Schema-based: check if paramType indicates geometric mean
        param_type = outcome_measure.get("paramType", "").upper()
        if "GEOMETRIC" in param_type:
            return True

        return False

    def resolve_vaccine(self, group, outcome_measure, metadata, proto):
        """Map group -> arm -> intervention via armsInterventionsModule, then fall back to keywords."""
        g_title = group.get("title", "")

        # Try structured mapping via interventionsModule
        arms_module = proto.get("armsInterventionsModule", {})
        arm_groups = arms_module.get("armGroups", [])
        interventions = arms_module.get("interventions", [])

        # Build intervention name lookup
        intervention_names = {}
        for inv in interventions:
            for arm_label in inv.get("armGroupLabels", []):
                intervention_names[arm_label] = inv.get("name", "")

        # Try to match group title to an arm label
        for arm in arm_groups:
            arm_label = arm.get("label", "")
            arm_desc = arm.get("description", "")
            # Check if group title matches arm label (exact or substring)
            if (g_title and arm_label and
                (g_title.upper() in arm_label.upper() or
                 arm_label.upper() in g_title.upper())):
                # Found matching arm -- get intervention name
                inv_name = intervention_names.get(arm_label, "")
                combined = f"{arm_label} {arm_desc} {inv_name}"
                vac_name, mfr = match_vaccine_keyword(combined, self.vaccine_lookup)
                if vac_name:
                    return vac_name, mfr

        # Fallback: keyword match on group title + description (same as Agent A)
        g_desc = group.get("description", "")
        combined_text = f"{g_title} {g_desc}"
        vac_name, mfr = match_vaccine_keyword(combined_text, self.vaccine_lookup)
        if vac_name:
            return vac_name, mfr

        return g_title, metadata["sponsor"]

    def parse_timeframe_weeks(self, timeframe_text, outcome_measure):
        """Structured regex: extract (number, unit) pairs and convert via lookup table."""
        if not timeframe_text:
            return ""

        t = timeframe_text.lower().strip()

        # Extract all (number, unit) pairs
        pairs = re.findall(r"(\d+(?:\.\d+)?)\s*(week|weeks|month|months|day|days|year|years)", t)

        if pairs:
            # Use the last (number, unit) pair -- typically the measurement timepoint
            number_str, unit = pairs[-1]
            number = float(number_str)
            multiplier = UNIT_TO_WEEKS.get(unit, 1)
            weeks = number * multiplier
            return str(round(weeks))

        # No numeric pattern found -- try common phrases
        if "one month" in t:
            return "4"
        if "one year" in t:
            return "52"
        if "six month" in t:
            return "26"

        return ""

    def infer_dose_number(self, outcome_measure, metadata, proto):
        """Analyze armsInterventionsModule for dose information, default to '1'."""
        arms_module = proto.get("armsInterventionsModule", {})
        interventions = arms_module.get("interventions", [])

        # Check intervention descriptions for dose count information
        for inv in interventions:
            desc = inv.get("description", "").lower()
            # Look for explicit dose count mentions
            m = re.search(r"(\d+)\s*(?:dose|injection|vaccination)s?", desc)
            if m:
                return m.group(1)

        # Check outcome timeframe for booster indicators
        timeframe = outcome_measure.get("timeFrame", "").lower()
        title = outcome_measure.get("title", "").lower()
        if "booster" in timeframe or "booster" in title:
            # Pediatric booster is typically dose 4 (3+1)
            std_ages = metadata.get("std_ages", [])
            if any(a.upper() == "CHILD" for a in std_ages):
                return "4"
            return "2"  # Adult booster

        return "1"

    def infer_schedule(self, outcome_measure, metadata, proto, dose_number):
        """Use designModule arm count and age to infer schedule."""
        std_ages = metadata.get("std_ages", [])
        is_child = any(a.upper() == "CHILD" for a in std_ages)

        dn = int(dose_number) if dose_number else 1

        if is_child:
            if dn > 1:
                primary = dn - 1
                return f"{primary}+1 child"
            return "Not selected"

        return f"{dn} dose adult"

    def infer_dose_description(self, outcome_measure, metadata, proto, dose_number, time_weeks):
        """Build dose description from timeframe and dose number."""
        std_ages = metadata.get("std_ages", [])
        is_child = any(a.upper() == "CHILD" for a in std_ages)
        age_label = "child" if is_child else "adult"

        timeframe = outcome_measure.get("timeFrame", "").lower()

        # Check for booster-specific descriptions
        if "booster" in timeframe:
            if time_weeks:
                return f"{time_weeks}w post boost {age_label}"
            return f"post boost {age_label}"

        # Standard description
        if time_weeks == "4":
            return f"1m post dose {dose_number} {age_label}"
        if time_weeks:
            return f"{time_weeks}w post dose {dose_number} {age_label}"
        return f"post dose {dose_number}"
