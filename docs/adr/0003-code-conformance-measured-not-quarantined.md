# ADR 0003: Measure code-set conformance; quarantine only structural problems

**Status:** Accepted · **Date:** 2026-10-18

## Context

The task calls for diagnosis-code validation against the correct code set. On the full CMS file,
against the ICD-10 code set of each claim's discharge fiscal year:

| Field | Valid | Main problems |
|---|---|---|
| Principal diagnosis | 74.8% | 13.0% truncated (`S134XX`), 11.3% header codes |
| Other diagnoses | 91.7% | 3.8% not valid in that fiscal year, 3.1% truncated |
| External cause | 1.2% | 98.6% three-character categories (`W19`, `V89`) |
| ICD procedure fields | 14.4% | 74.7% are ICD-10-CM *diagnosis* codes, 10.9% PCS table headers (`GZ3`) |
| HCPCS (format only) | 98.7% CPT or Level II shape | 1.0% modifiers in the code field (`QS`, `Q3`) |

## Decision

- **Quarantine** is for rows that cannot be trusted structurally: missing keys, unparseable or
  impossible dates, conflicting duplicates, claims whose lines disagree. A claim is quarantined whole.
- **Code-set status** is computed for every code (`macros/icd10.sql`), kept on the silver diagnosis and
  procedure rows, and summarized by field, role, status, and fiscal year in
  `silver_dq_code_conformance`. A warn-level test reports each fiscal year below a 95% target for
  valid principal diagnoses.
- Validation uses the code set **of the discharge fiscal year**, not the latest one. Claims before
  ICD-10 took effect (2015-10-01) are checked against FY2016 and flagged.
- HCPCS gets format checks only. No CPT description table is stored (AMA copyright).

## Why

Quarantining on code status would remove a quarter of all claims, and any claim with a procedure.
Real payer feeds force the same trade-off: an unknown code is a reason to investigate, measure, and
route for review, not to drop a paid claim. Measuring by fiscal year turns conformance into a trend
line, so a sudden change (a mapping break upstream) stands out from the source's steady background.

## Consequences

- Downstream marts must choose their own rule per measure, for example whether a truncated principal
  diagnosis can still be grouped at the category level. Silver keeps the status so they can.
- April 1 mid-year code updates are not loaded, so `U071` (COVID-19, effective 2020-04-01) appears as
  `not_valid_in_fiscal_year` on FY2020 claims. Loading the April addenda would remove that.
