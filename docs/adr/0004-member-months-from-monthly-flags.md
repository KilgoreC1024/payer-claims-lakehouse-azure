# ADR 0004: Member-months from the monthly entitlement flags

**Status:** Accepted · **Date:** 2026-10-18

## Context

PMPM and utilization per 1,000 need member-months as the denominator. The beneficiary files carry both
twelve monthly indicators per member-year and CMS's annual month counts. In this release the two do not
always agree, and Part C (Medicare Advantage) enrollment does not stop fee-for-service claims.

## Decision

- A member-month exists when that month's `MDCR_ENTLMT_BUYIN_IND_mm` shows Part A and/or Part B
  entitlement and the member was alive on the first of the month. Part A/B, buy-in, Part C, Part D, and
  dual status all come from the same month's indicators.
- Part A member-months must equal `BENE_HI_CVRAGE_TOT_MONS` for every member-year (error-level test).
  Part B and Part D comparisons are warnings; buy-in, HMO, and dual annual counts are not used because
  they are fixed placeholder values in this release.
- The FFS denominator flag (`is_ffs_eligible`) keeps Part C months by default. A dbt var,
  `part_c_excludes_from_ffs`, switches to the CCW convention for real data.
- `in_claims_window` marks the months the published claims can cover (2015-03 to 2023-02).

## Why

- Monthly flags say exactly which months were covered; annual counts cannot.
- Part A is the reconciliation that matters for inpatient (a Part A benefit), and it reconciles exactly
  on all 953,004 member-months of the full file, which shows the flags are read correctly.
- Synthea exports FFS claims for Part C enrollees (user guide 3.3.3). 58.6% of member-months have a
  Part C contract, so the CCW rule would divide every claim by 41% of the members.

## Consequences

- Exact relative to the file, coarse relative to reality: in this release every member is entitled for
  all 12 months of each year they appear in, and nobody dies. The logic for partial years and deaths
  is exercised by an injected edge case instead (`test_partial_year_enrollment_and_death_shape_member_months`:
  enrolled from April, died in February, Part A still reconciles).
- The enrollment files extend to 2025 while claims end in March 2023. Rate marts must restrict to
  `in_claims_window`, or they will divide by members who could not have had claims.
