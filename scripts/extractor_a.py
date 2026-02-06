"""
Agent A: Keyword-first extraction logic.

This agent uses the same approach as the original extract_trial.py:
- Assay classification by keyword priority in outcome title
- Vaccine matching by keyword lookup on group title/description
- Timeframe parsing by pattern-matching heuristics
- Schedule inference by age-list heuristics
"""

import re
from extractor_base import ExtractorBase, match_vaccine_keyword, IMMUNO_KEYWORDS


class ExtractorA(ExtractorBase):
    """Keyword-first extraction agent (mirrors original extract_trial.py logic)."""

    agent_name = "a"

    def classify_assay(self, outcome_measure, proto):
        """Keyword priority matching on outcome title: OPA > IGG/GMC > GMT."""
        title = outcome_measure.get("title", "").upper()
        if "OPA" in title:
            return "OPA"
        if "IGG" in title or "GMC" in title:
            return "GMC"
        if "GMT" in title:
            return "OPA"
        return "Unknown"

    def is_immunogenicity_outcome(self, outcome_measure, proto):
        """Check if outcome title contains any IMMUNO_KEYWORDS."""
        title = outcome_measure.get("title", "").upper()
        return any(kw in title for kw in IMMUNO_KEYWORDS)

    def resolve_vaccine(self, group, outcome_measure, metadata, proto):
        """Keyword match against vaccine_lookup.csv on group title + description."""
        g_title = group.get("title", "")
        g_desc = group.get("description", "")
        combined_text = f"{g_title} {g_desc}"

        vac_name, mfr = match_vaccine_keyword(combined_text, self.vaccine_lookup)
        if vac_name:
            return vac_name, mfr
        # Fallback: use group title as vaccine name, sponsor as manufacturer
        return g_title, metadata["sponsor"]

    def parse_timeframe_weeks(self, timeframe_text, outcome_measure):
        """Pattern-matching heuristics (same as original extract_trial.py)."""
        t = timeframe_text.lower()
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

    def infer_dose_number(self, outcome_measure, metadata, proto):
        """Default to '1' for adult single-dose trials (original behavior)."""
        return "1"

    def infer_schedule(self, outcome_measure, metadata, proto, dose_number):
        """Age-list heuristic: child -> 3+1, adult -> '1 dose adult'."""
        std_ages = metadata.get("std_ages", [])
        is_child = any(a.upper() == "CHILD" for a in std_ages)
        if is_child:
            if dose_number and int(dose_number) > 1:
                primary = int(dose_number) - 1
                return f"{primary}+1 child"
            return "Not selected"
        return f"{dose_number} dose adult" if dose_number else "1 dose adult"

    def infer_dose_description(self, outcome_measure, metadata, proto, dose_number, time_weeks):
        """Template-based dose description (original behavior)."""
        std_ages = metadata.get("std_ages", [])
        is_child = any(a.upper() == "CHILD" for a in std_ages)
        age_label = "child" if is_child else "adult"

        if time_weeks == "4":
            return f"1m post dose {dose_number} {age_label}"
        if time_weeks:
            return f"{time_weeks}w post dose {dose_number} {age_label}"
        return f"post dose {dose_number}"
