# Gold marts

The gold layer turns silver claims and member-months into the rates a health plan reports: inpatient
paid amount per member per month (PMPM), admits and days per 1,000, and 30-day all-cause readmissions.

**The data is synthetic.** The CMS files were generated with Synthea, and CMS warns they have "very
limited inferential research value". Payments and lengths of stay do not resemble real Medicare. These
marts show how the measures are built and tested. **They are engineering demonstrations, not findings**
about Medicare, hospitals, or members.

## Tables

| Table | Grain | What it holds |
|---|---|---|
| `gold_inpatient_stays` | stay (claim) | Admission month, paid amount, days, normalized discharge status, and whether the stay is in the claims window and in the rate denominator |
| `gold_inpatient_member_months` | member x month | Member-month fact: every denominator member-month with that month's inpatient admits, days, and paid amount (zero when there was no stay) |
| `gold_inpatient_pmpm_by_year` | year | Member-months, paid amount, PMPM, and the stays and dollars outside the denominator |
| `gold_inpatient_utilization_by_year` | year | Member-years, admits, days, admits and days per 1,000, average length of stay |
| `gold_readmission_30d_stays` | stay (claim) | Index-stay decision, the one exclusion that applies, and the first readmission within 30 days |
| `gold_readmission_30d_by_year` | year | Stays, index stays, readmissions, readmission rate, and a count per exclusion |

## Rules every mart shares

- **Denominator.** Member-months from `silver_member_months` with `in_claims_window` (2015-03 to
  2023-02, the months the published claims can cover) and `is_ffs_eligible` (Parts A and B; Part C
  months are kept by default because Synthea bills Part C enrollees as fee-for-service, see
  [ADR 0004](adr/0004-member-months-from-monthly-flags.md)).
- **A stay is a claim**, counted in its **admission month**: its payment, its admission, and all of its
  days go to the month the member was admitted (the incurred-date convention).
- **Numerators match denominators.** A stay is in a rate only if its member has a denominator
  member-month in the admission month (`in_ffs_denominator`). Other in-window stays, such as those of a
  member with Part A only, are reported next to PMPM as `stays_outside_denominator` and
  `paid_amount_outside_denominator`, not dropped silently.
- **Paid** is `CLM_PMT_AMT`, the amount Medicare paid, not charges or beneficiary cost sharing.
- **Years** are calendar years of admission (of discharge, for readmissions). 2015 starts in March and
  2023 ends in February. Their rates are still comparable, because the denominator covers the same months.

## Definitions

**PMPM** = inpatient paid amount / member-months.

**Admits per 1,000** = admits / member-years x 1,000, and **days per 1,000** = inpatient days /
member-years x 1,000, where member-years = member-months / 12. Inpatient days count a same-day stay as
one day. Stays chained on the same day count as separate admissions here.

**30-day all-cause readmissions.** An **index stay** is a stay that passes every exclusion below. Its
**readmission** is the member's first stay admitted **1 to 30 days** after the index discharge, for any
cause. Only the first one counts, it counts whatever its own exclusions, and it can be an index stay
itself. Rate = readmissions / index stays, by year of the index discharge. Each stay gets the first
exclusion that applies:

| Exclusion | Rule | Why |
|---|---|---|
| `discharged_before_claims_window` | Discharged before 2015-03-01 | Outside the measurement period |
| `less_than_30_days_follow_up` | Discharge + 30 days is after 2023-02-28 | A readmission could fall after the last claim |
| `died_in_hospital` | Discharge status 20, 40, 41, 42 | No readmission is possible |
| `left_against_medical_advice` | Discharge status 07 | The hospital could not finish care or plan the discharge |
| `transferred_to_acute_care` | Discharge status 02, 05, 66, or the planned-readmission variants 82, 85, 94 | The receiving hospital's discharge is the index |
| `continued_same_day` | The member's next stay starts on or before this discharge date | A transfer, or a same-day readmission that Medicare bills as one stay. The last stay of the chain is the candidate index |
| `not_ffs_enrolled_through_follow_up` | Not FFS-eligible in every month from admission through discharge + 30 days | A readmission might never be billed as FFS |

Discharge status codes are compared as two digits, because CMS writes `1`, not `01`.
**In this source `PTNT_DSCHRG_STUS_CD` is always `1` (home)**, so the death, against-advice, and
transfer-by-status exclusions never fire on the published files. They are implemented for real data and
exercised by an end-to-end test that injects the codes. Same-day chaining does fire on the fixture.

**How this differs from CMS's hospital-wide readmission measure.** The structure is the same: index
stays, a 30-day follow-up, transfer and death exclusions, and only the first readmission. These parts
are left out:

- **No risk adjustment.** The rate is crude, so it also needs no 12-month enrollment lookback.
- **No planned-readmission algorithm.** It needs procedure and diagnosis categories. In this source,
  75% of ICD procedure fields hold diagnosis codes, so the algorithm could not be applied faithfully.
- **No age, cohort, or hospital-level rules.** All ages are included, nothing is grouped into specialty
  cohorts, and rates are member-level (a payer view), not attributed to hospitals.

## Tests

Every mart has key tests (unique, not null, relationships). Error-level singular tests in
[`dbt/tests/gold/`](../dbt/tests/gold/) recompute each mart from silver without the gold models:

- **Member-month fact:** member-months, admits, days, and paid amount equal silver's denominator
  member-months and the silver claims attributed to them. This catches fan-out or dropped stays.
- **PMPM:** for each year, paid plus paid outside the denominator equals all in-window paid in silver.
  Member-months equal silver, paid equals the fact, and PMPM equals paid / member-months.
- **Utilization:** for each year, admits plus stays outside the denominator equal all in-window silver
  stays, and admits, days, and both per-1,000 rates follow from the fact.
- **Readmissions:** every silver claim has exactly one decision. Each readmission is recomputed from
  silver as the first stay 1 to 30 days after discharge, and excluded stays carry no outcome. For each
  year, stays = index stays + every exclusion column, which also catches an exclusion missing from the
  by-year mart.
- A warning flags overlapping stays of one member, because they would inflate admits and days.

## On the fixture

The committed fixture (31 members, 39 stays) shows each rule at work:

| | |
|---|---|
| Silver member-months | 2,949 |
| Outside the claims window (2015-01..02, 2023-03..2025-03) | 783 |
| In the window but not FFS-eligible (one Part A-only member) | 50 |
| Denominator member-months | 2,116 |
| Stays in the rates | 38 ($270,768.34) |
| Stays outside the denominator | 1 ($555.75, the Part A-only member) |
| Index stays / readmissions | 37 / 2 (both 21 days after discharge) |
| Not index stays | 1 `continued_same_day` (admitted again on the discharge day), 1 `not_ffs_enrolled_through_follow_up` |
