"""
Extract GMC and OPA data from Matur et al. (2024) Vaccine 42:3157-3165
Phase 3 study of BE-PCV-14 (PNEUBEVAX 14) vs PCV-13 in Indian infants
Registry: CTRI/2020/02/023129 (not on ClinicalTrials.gov)
"""

import csv
import os

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "wisspar_export_2026_02_05.csv")

FIELDNAMES = [
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

# -- Shared metadata --
STUDY_NAME = "Phase 3 Study of 14-valent PCV (PNEUBEVAX 14) in Healthy Indian Infants"
STUDY_ID = "CTRI/2020/02/023129"
SPONSOR = "Biological E Limited"
RESPONSIBLE_PARTY = "Biological E Limited"
PHASE = "Phase 3"
COUNTRY_CODE = "IN"
CONTINENT = "Asia"
AGE_LIST = '["Child"]'
ETHNICITY = ""
DOSE_NUMBER = 3
SCHEDULE = "3+0 child"
DOSE_DESC = "1m post dose 3 child"
TIME_FRAME = "One month after third dose (Day 86)"
TIME_FRAME_WEEKS = 4

# -- GMC data from Figure 2b --
# BE-PCV-14 (per-protocol n=641 for IgG)
GMC_BEPCV14 = {
    "1": 1.73, "3": 0.85, "4": 1.75, "5": 1.48,
    "6B": 1.69, "7F": 2.89, "9V": 3.03, "14": 11.18,
    "18C": 2.36, "19A": 5.39, "19F": 5.63, "23F": 2.03,
    "22F": 4.18, "33F": 1.5,
}

# PCV-13 (per-protocol n=626 for IgG)
GMC_PCV13 = {
    "1": 2.26, "3": 0.7, "4": 2.1, "5": 1.4,
    "6B": 1.8, "7F": 3.3, "9V": 2.72, "14": 9.52,
    "18C": 2.65, "19A": 6.02, "19F": 6.51, "23F": 2.19,
}

# 6A cross-protection data from Table 4 (post-vaccination GMC)
GMC_6A_BEPCV14 = 1.075  # n=645 (ITT)
GMC_6A_PCV13 = 1.906    # n=645 (ITT)

# GMC ratios (BE-PCV-14 / PCV-13) from Figure 2b
GMC_RATIOS = {
    "1": 0.77, "3": 1.23, "4": 0.83, "5": 1.06,
    "6B": 0.94, "7F": 0.88, "9V": 1.12, "14": 1.18,
    "18C": 0.89, "19A": 0.89, "19F": 0.87, "23F": 0.92,
    "22F": 6.01, "33F": 2.16,
}

# -- OPA GMT data from Table 2 --
# BE-PCV-14 (OPA subset n~192)
OPA_BEPCV14 = {
    "1": 23.21, "3": 59.6, "4": 426.67, "5": 66.47,
    "6B": 990.66, "7F": 1023.16, "9V": 856.81, "14": 1951.14,
    "18C": 460.7, "19A": 394.65, "19F": 604.3, "23F": 768.79,
    "22F": 691.6, "33F": 1859.67,
}

# PCV-13 (OPA subset n~194)
OPA_PCV13 = {
    "1": 19.94, "3": 63.01, "4": 626.04, "5": 78.17,
    "6B": 918.24, "7F": 1156.95, "9V": 746.44, "14": 1272.2,
    "18C": 561.01, "19A": 451.18, "19F": 439.94, "23F": 995.13,
}

# 6A OPA from Table 4 (post-vaccination GMT)
OPA_6A_BEPCV14 = 134.50  # n=192
OPA_6A_PCV13 = 690.63    # n=194


def make_row(title, assay, serotype, value, participants, vaccine, manufacturer, ratio=""):
    return {
        "clinical_trial_study_name": STUDY_NAME,
        "clinical_trial_study_id": STUDY_ID,
        "clinical_trial_sponsor": SPONSOR,
        "clinical_trial_responsible_party": RESPONSIBLE_PARTY,
        "clinical_trial_phase": PHASE,
        "location_country_code": COUNTRY_CODE,
        "location_continent": CONTINENT,
        "study_eligibility_standard_age_list": AGE_LIST,
        "study_eligibility_ethnicity": ETHNICITY,
        "outcome_overview_title": title,
        "outcome_overview_id": "",
        "outcome_overview_description": f"{assay} one month after third dose of primary series (6-10-14 weeks schedule)",
        "outcome_overview_time_frame": TIME_FRAME,
        "outcome_overview_assay": assay,
        "outcome_overview_dose_number": DOSE_NUMBER,
        "outcome_overview_participants": participants,
        "outcome_overview_serotype": serotype,
        "outcome_overview_value": value,
        "outcome_overview_upper_limit": "",
        "outcome_overview_lower_limit": "",
        "outcome_overview_ratio": ratio,
        "outcome_overview_vaccine": vaccine,
        "outcome_overview_immunocompromised_population": "",
        "outcome_overview_manufacturer": manufacturer,
        "outcome_overview_dose_description": DOSE_DESC,
        "outcome_overview_schedule": SCHEDULE,
        "outcome_overview_time_frame_weeks": TIME_FRAME_WEEKS,
        "outcome_overview_confidence_interval": "",
        "outcome_overview_percent_responders": 0,
    }


def main():
    rows = []

    # GMC rows - BE-PCV-14 (14 serotypes)
    for st, val in GMC_BEPCV14.items():
        ratio = GMC_RATIOS.get(st, "")
        rows.append(make_row("BE-PCV-14", "GMC", st, val, 641,
                             "PCV14 (PneuBevax)", "Biological E", ratio))

    # GMC rows - BE-PCV-14 serotype 6A (cross-protection)
    rows.append(make_row("BE-PCV-14", "GMC", "6A", GMC_6A_BEPCV14, 645,
                         "PCV14 (PneuBevax)", "Biological E"))

    # GMC rows - PCV-13 (12 common serotypes)
    for st, val in GMC_PCV13.items():
        rows.append(make_row("PCV-13", "GMC", st, val, 626,
                             "PCV13 (Pfizer)", "Pfizer"))

    # GMC rows - PCV-13 serotype 6A
    rows.append(make_row("PCV-13", "GMC", "6A", GMC_6A_PCV13, 645,
                         "PCV13 (Pfizer)", "Pfizer"))

    # OPA rows - BE-PCV-14 (14 serotypes)
    for st, val in OPA_BEPCV14.items():
        rows.append(make_row("BE-PCV-14", "OPA", st, val, 192,
                             "PCV14 (PneuBevax)", "Biological E"))

    # OPA rows - BE-PCV-14 serotype 6A (cross-protection)
    rows.append(make_row("BE-PCV-14", "OPA", "6A", OPA_6A_BEPCV14, 192,
                         "PCV14 (PneuBevax)", "Biological E"))

    # OPA rows - PCV-13 (12 common serotypes)
    for st, val in OPA_PCV13.items():
        rows.append(make_row("PCV-13", "OPA", st, val, 194,
                             "PCV13 (Pfizer)", "Pfizer"))

    # OPA rows - PCV-13 serotype 6A
    rows.append(make_row("PCV-13", "OPA", "6A", OPA_6A_PCV13, 194,
                         "PCV13 (Pfizer)", "Pfizer"))

    # Check for duplicates
    existing_ids = set()
    with open(CSV_PATH, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            existing_ids.add(r["clinical_trial_study_id"])

    if STUDY_ID in existing_ids:
        print(f"WARNING: {STUDY_ID} already exists in the CSV. Use --force to overwrite.")
        return

    # Append rows
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        for row in rows:
            writer.writerow(row)

    print(f"Appended {len(rows)} rows for {STUDY_ID}")
    print(f"  GMC rows: {sum(1 for r in rows if r['outcome_overview_assay'] == 'GMC')}")
    print(f"  OPA rows: {sum(1 for r in rows if r['outcome_overview_assay'] == 'OPA')}")
    print(f"  BE-PCV-14 rows: {sum(1 for r in rows if r['outcome_overview_title'] == 'BE-PCV-14')}")
    print(f"  PCV-13 rows: {sum(1 for r in rows if r['outcome_overview_title'] == 'PCV-13')}")

    # Summary by serotype
    serotypes = sorted(set(r["outcome_overview_serotype"] for r in rows),
                       key=lambda s: (not s.replace("F","").isdigit(), s))
    print(f"  Serotypes ({len(serotypes)}): {', '.join(serotypes)}")


if __name__ == "__main__":
    main()
