# Wisspar Project Context

## Purpose & Context

This project works with vaccine immunogenicity data, particularly focusing on pneumococcal vaccine clinical trials. The work involves extracting and analyzing outcome measures like geometric mean concentrations (GMC) and opsonophagocytic activity (OPA) measurements across multiple serotypes and treatment arms from clinical trial datasets. It maintains structured datasets that track immunogenicity outcomes across different vaccine formulations, dosage levels, and geographic regions.

## Current State

The project is actively building and expanding a comprehensive immunogenicity dataset (wisspar_export.csv) by extracting clinical trial data from ClinicalTrials.gov. It recently added data from the Vaxcyte VAX-31 trial (NCT06151288), which tested a 31-valent pneumococcal conjugate vaccine against PCV20, expanding the dataset from over 5,000 to nearly 6,000 rows. The current dataset structure includes fields for treatment arms, serotypes, outcome measures, dosage information, geographic location, and timeframe data.

The current data extract is located at `data/wisspar_export_2026_02_05.csv`. The goal is to ingest and format additional trials from ClinicalTrials.gov into this dataset.

## Data Extraction Rules

- **ONLY** use data from the results tables and metadata tables from ClinicalTrials.gov. Do not use data from other sources without explicit user permission.
- **ALWAYS** extract data via the ClinicalTrials.gov API v2 — never scrape directly from the website.
- If additional data sources beyond ClinicalTrials.gov are needed, ask the user for permission first.
- If it is not clear which results tables or outcome measures should be extracted from a trial, ask the user for clarification before proceeding.
- When data is extracted from a manuscript or publication (rather than ClinicalTrials.gov results tables), **always log the source** in `data/manuscript_sources.csv` with the NCT ID, publication reference, tables extracted, and any relevant notes. Keep this file up to date whenever manuscript-sourced data is added.

## Manuscript-Sourced Data

Some trials do not have results posted on ClinicalTrials.gov. When the user provides a manuscript, data may be extracted directly from publication tables with explicit permission.

- **Registry**: `data/manuscript_sources.csv` tracks all trials with manuscript-extracted data.
- **Publication files**: Stored in `data/publications/`.
- Always record: NCT ID, publication citation, which tables were extracted, serotype count, treatment arms, and assay types.
- Manuscript-sourced data follows the same CSV formatting conventions as API-extracted data.

## Tools & Resources

This project uses the ClinicalTrials.gov API v2 for data extraction. API-based extraction is preferred over web scraping for reliability and consistency in data collection.

### ClinicalTrials.gov API v2

- **Base URL**: `https://clinicaltrials.gov/api/v2/`
- **API Reference**: https://clinicaltrials.gov/data-api/api
- **Authentication**: Public API, no authentication required.
- **Rate limits**: ~50 requests/minute per IP. Use the `fields` parameter to request only needed data.

**Key endpoints:**
- **Single study**: `GET https://clinicaltrials.gov/api/v2/studies/{NCT_ID}`
  - Use `?fields=resultsSection,protocolSection` to fetch outcome measures and trial metadata.
- **Search studies**: `GET https://clinicaltrials.gov/api/v2/studies`
  - Parameters: `query.cond` (condition), `query.intr` (intervention), `query.term` (general search), `pageSize`, `format` (json/csv), `fields`, `countTotal`.

**Response structure:** The API returns JSON with `protocolSection` (study info, eligibility, design) and `resultsSection` (outcomes and results when available).

This project works with structured CSV datasets and requires specific data formatting that includes treatment group information, serotype coverage, and dosage specifications.

## Key Learnings & Principles

- The ClinicalTrials.gov API requires specific field parameters to efficiently access immunogenicity results data.
- Outcome measures are structured as nested JSON with classes representing serotypes and categories containing measurements for each treatment group.
- Group IDs follow predictable patterns, and immunogenicity data can be identified by searching for "OPA" or "IgG" indicators in outcome titles.
- Maintaining consistent CSV formatting with predefined fieldnames ensures compatibility across dataset expansions.

## Extraction Script

The automated extraction script is at `scripts/extract_trial.py`. It handles fetching, parsing, validation, and appending in one step.

### Usage

```bash
# Extract a single trial:
python scripts/extract_trial.py NCT06151288

# Extract multiple trials:
python scripts/extract_trial.py NCT06151288 NCT12345678 NCT99999999

# Preview without writing (dry run):
python scripts/extract_trial.py --dry-run NCT06151288

# Search ClinicalTrials.gov for trials and extract all with immunogenicity results:
python scripts/extract_trial.py --search "pneumococcal conjugate vaccine"

# Force re-extraction of a trial already in the dataset:
python scripts/extract_trial.py --force NCT06151288

# Specify a different output CSV:
python scripts/extract_trial.py --csv data/my_output.csv NCT06151288
```

### What the Script Does

1. **Fetches JSON** from the ClinicalTrials.gov API v2 for each NCT ID.
2. **Extracts metadata** from `protocolSection`: title, NCT ID, sponsor, phase, country, age eligibility.
3. **Identifies immunogenicity outcomes** by checking titles for keywords: `OPA`, `IgG`, `GMT`, `GMC`, `GEOMETRIC`.
4. **Maps groups** to vaccine names and manufacturers using `data/vaccine_lookup.csv`.
5. **Maps countries** to ISO codes and continents using `data/country_lookup.csv`.
6. **Extracts measurements** for each class (serotype) x group combination: `value`, `upperLimit`, `lowerLimit`.
7. **Validates** extracted data: checks row counts, missing values, serotype completeness.
8. **Checks for duplicates** — skips NCT IDs already in the CSV (override with `--force`).
9. **Appends** new rows to the CSV.

### Built-in Safeguards

- **Duplicate detection**: Skips trials already in the dataset unless `--force` is used.
- **Validation report**: Printed after each trial showing row counts, serotype counts, group counts, and any issues.
- **Rate limiting**: 1-second delay between trials in batch mode.
- **Dry run**: `--dry-run` flag previews rows without writing.

### Post-Extraction Review (Required)

After running `extract_trial.py`, always verify the output against the trial's metadata before considering the extraction complete:

- **Check the extracted outcome tables** against the trial record to confirm the correct immunogenicity measures were selected (some trials have multiple OPA/IgG tables at different timepoints or for different subpopulations).
- **Verify group-to-vaccine mapping** — confirm that each treatment arm was assigned the correct standardized vaccine name and manufacturer.
- **Confirm dose number, schedule, and timeframe** — the script infers these from the data but may not always match the trial design (e.g., multi-dose regimens, booster-only timepoints).
- **If there is any uncertainty** about which tables to include, how to interpret group descriptions, or how to classify an outcome measure, **ask the user for clarification before finalizing the data**.
- The script is a starting point — its outputs should be treated as a draft that requires human review.

### Lookup Tables

- **`data/vaccine_lookup.csv`** — Maps keyword patterns to standardized vaccine names and manufacturers. Add new entries here when new vaccine products appear.
- **`data/country_lookup.csv`** — Maps country names to ISO 2-letter codes and continents. Add entries for countries not yet covered.

### Manual Extraction Process (when the script needs adjustment)

For trials with unusual structure, the step-by-step manual process is:

1. **Fetch JSON** — `curl` the API endpoint to a temp file (response is a single large line, too big to read directly; must parse with Python).
2. **Extract metadata** — From `protocolSection`: title, NCT ID, sponsor, phase, country, age eligibility.
3. **List all outcome measures** — Iterate `resultsSection.outcomeMeasuresModule.outcomeMeasures` and identify immunogenicity outcomes.
4. **Map groups** — Each outcome measure contains `groups` with IDs like `OG000`, `OG001`, etc. Map each group to its vaccine name, manufacturer, and participant count (from `denoms`).
5. **Extract measurements** — For each class (serotype) x group combination, extract: `value`, `upperLimit`, `lowerLimit`.
6. **Format rows** — Map to CSV fieldnames using conventions below.
7. **Check for duplicates** — Grep the CSV for the NCT ID before appending.
8. **Append** — Write new rows to the CSV.

## CSV Field Mapping Conventions

| CSV Field | Source / Rule |
|---|---|
| `outcome_overview_title` | Group title (e.g., "VAX-31 Low Dose", "PCV20") |
| `outcome_overview_id` | Group ID from API (e.g., OG000) |
| `outcome_overview_description` | Outcome measure description from API |
| `outcome_overview_assay` | `OPA` for OPA GMT outcomes, `GMC` for IgG GMC outcomes |
| `outcome_overview_serotype` | Class title from the outcome measure |
| `outcome_overview_value` | Measurement value |
| `outcome_overview_upper_limit` | Upper 95% CI bound |
| `outcome_overview_lower_limit` | Lower 95% CI bound |
| `outcome_overview_participants` | From denoms counts for each group |
| `outcome_overview_dose_number` | Number of doses (e.g., "1" for single-dose adult trials) |
| `outcome_overview_schedule` | Pattern: "1 dose adult", "3+1 child", "2 dose adult", etc. |
| `outcome_overview_dose_description` | Pattern: "1m post dose 1 adult", "12m post boost child", etc. |
| `outcome_overview_time_frame_weeks` | Numeric weeks (e.g., "4" for 1 month) |
| `outcome_overview_vaccine` | Standardized name (see naming conventions below) |
| `outcome_overview_manufacturer` | Company name |
| `clinical_trial_phase` | "Phase 1", "Phase 2", "Phase 3", "Phase 1/Phase 2", etc. |
| `location_country_code` | ISO 2-letter code (e.g., "US", "GM") |
| `location_continent` | "North America", "Africa", "Europe", etc. |

## Vaccine Naming Conventions

Existing names in the dataset (use these patterns for consistency):
- `PCV7`, `PCV10 (Synflorix)`, `PCV10 (Pneumosil)`, `PCV13`, `PCV13 (Pfizer)`, `PCV13 (Walvax)`
- `PCV15`, `PCV15 (high dose)`, `PCV15 (medium dose)`
- `PCV20`, `PCV21(Merck V116)`, `PCV24 (Vaxcyte VAX-24)`, `PCV25 (Inventprise)`, `PCV31 (Vaxcyte VAX-31)`
- `PPV23`, `PCV13+PPV23`, `PCV15+PPV23`, `PCV20+PPV23`
- Pattern: `PCV{valency} ({Manufacturer/Brand})` when disambiguation is needed.

## Known Edge Cases & Limitations

- **API response size**: The JSON response is a single large line (can exceed 40k tokens). Always parse with Python rather than reading directly.
- **Trials without results**: Many trials on ClinicalTrials.gov have no `resultsSection`. The script skips these automatically.
- **Trials without immunogenicity outcomes**: Some completed vaccine trials only post safety data, not OPA/IgG. The script lists available outcomes and skips.
- **Vaccine name matching**: The `vaccine_lookup.csv` uses keyword matching. For new/unusual vaccines, the script falls back to using the group title as the vaccine name and the sponsor as the manufacturer. Review the output and update the lookup table if needed.
- **Dose number / schedule inference**: The script defaults to "1 dose adult" for adult trials. Multi-dose or pediatric schedules may need manual adjustment after extraction.
- **Multi-site country fields**: The `location_country_code` and `location_continent` fields store comma-separated values for all sites (matching the existing dataset format).
- **Time frame estimation**: The script converts timeframe text (e.g., "1 month after vaccination") to numeric weeks. Unusual timeframe descriptions may not parse correctly.
