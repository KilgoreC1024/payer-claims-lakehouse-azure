# Methodology

How raw CMS files become trusted silver tables, and every rule and approximation along the way.
Decisions with real trade-offs are recorded as ADRs in [`adr/`](adr/). The gold marts built on silver
(PMPM, admits and days per 1,000, 30-day readmissions) are defined in [`marts.md`](marts.md).

## 1. Arrivals: loading one year at a time

A payer never receives five years of claims at once. To make the pipeline behave like a production
feed, `pcl replay` cuts each raw file into **arrivals**, and `pcl load` loads them oldest first.

| Source | Arrival | How it is cut |
|---|---|---|
| `cms_beneficiary` | reference year | One published file per year (`beneficiary_2019.csv` -> arrival 2019) |
| `cms_inpatient` | CMS processing year | The single `inpatient.csv` is split by the year of `NCH_WKLY_PROC_DT` |
| `ref_icd10cm`, `ref_icd10pcs` | fiscal year | The order file is extracted from each yearly CMS zip |

Inpatient arrivals use the **processing** date, not the service date, because that is when a payer
would receive the claim. A stay ending 2017-12-29 and processed 2018-01-05 arrives with 2018, which is
claims run-out in miniature. (In this source CMS processes every claim within two weeks, so 651 of
58,066 lines cross a year boundary.)

Landing is lossless: arrival files contain the raw lines unchanged, so the union of the inpatient
arrivals is the raw file. Every landing file has a `_landing.json` sidecar with its SHA-256, size,
line count, and the raw file it came from. `pcl run --through 2018` stops member and claim arrivals
after 2018; a later `pcl run` adds the rest, skipping everything already loaded.

## 2. Contracts at the landing -> bronze boundary

Each arrival is checked against its contract (`contracts/*.yml`) before anything is written to bronze.
The engine and severity model are shared with the sibling
[healthcare-cost-utilization-platform](https://github.com/KilgoreC1024/healthcare-cost-utilization-platform)
project (its ADR 0004).

| Check | Failure means |
|---|---|
| Header: duplicate or missing contracted columns | **Block** the arrival |
| Header: columns added or removed since the previous arrival | Warn (block for reference data) |
| Parse: strict pipe-delimited read, no quoting, column count per row | **Block** |
| Volume: row count within bounds | **Block** |
| Grain: repeated key in the file | Block (members, reference) or warn (claims, resolved in silver) |
| Values: type (`dd-Mon-yyyy` dates, integers, decimals), range, pattern, accepted values | Warn within the error budget, **block** above it |

**Error budget.** An arrival may carry up to the larger of `row_error_budget` (0.5% of rows) and
`row_error_floor` (3 rows) with value problems. A pure rate is too strict for small batches: one
invalid code in a 29-member fixture file is 3.4%. A pure count is too loose for large ones. The same
rule is enforced again on silver quarantine by a dbt test.

**Quarantine or flag.** A row that breaks a value rule is quarantined in silver, unless the column is
marked `on_violation: flag`. Dual status is the only flagged column: an unknown dual code (`AA` appears
in the published files) does not make a member less enrolled, so the value is nulled and flagged and
the member-month is kept.

## 3. Bronze

`bronze/<source>/arrival=<year>/sha256=<first 16>/data.parquet`, every column VARCHAR as published,
plus lineage: `_source`, `_arrival`, `_source_file`, `_source_sha256`, `_source_line` (the line in
the landed file, header = 1), `_loaded_at`, `_contract_status`.

- **Idempotent:** content already in bronze (same SHA-256) is skipped.
- **Versioned:** a re-delivered arrival with different content is written next to the original.
  Staging reads the newest version of each arrival, so a correction replaces its batch as a whole.
- **Audited:** every attempt (loaded, skipped, rejected) is appended to `bronze/_load_log.jsonl`, and
  every contract report is kept under `bronze/_reports/`.
- **Tamper-evident:** a landing file whose checksum no longer matches its sidecar is refused.

## 4. Silver: claims

### Typing

Whitespace-only fields are empty (CMS writes most empty fields as one space). Dates are parsed with
`%d-%b-%Y`, amounts as `decimal(18,2)`. Parsing lives in one place, `dbt/macros/cms.sql`.

### Structural errors (quarantined)

Missing claim, line, or member ID; non-inpatient claim type; missing or unparseable from/through
dates; unparseable admission, discharge, amount, or day-count values; through date before from date;
discharge before admission; admission after the claim's from date; negative total charges; missing
principal diagnosis.

**A claim loads whole or not at all.** If any line of a claim is quarantined, or its lines disagree
on claim-level fields (claim fields repeat on every RIF line), every line goes to quarantine.

### Deduplication

When the same `(CLM_ID, CLM_LINE_NUM)` appears more than once in the newest versions of the arrivals:

| Situation | Outcome |
|---|---|
| Identical content | `exact_duplicate`: one copy is kept, the rest are collapsed |
| Different content, different arrivals | `superseded`: the later arrival wins |
| Different content, same arrival | `conflicting`: every version is quarantined (nobody can tell which is right) |

A dbt test balances the books: every newest-version bronze row is in silver, in quarantine, or
counted as a collapsed or superseded row.

### Claim fields

- `discharge_fiscal_year`: federal fiscal year of the discharge (or through) date. October starts the
  next fiscal year.
- `length_of_stay_days` = discharge - admission; `inpatient_days` counts a same-day stay as 1.
- `coded_before_icd10_effective`: discharged before 2015-10-01 (1,103 claims in the full file).

### Code-set validation (measured, not quarantined)

Each diagnosis (admitting, principal, `ICD_DGNS_CD1..25`, external cause) and each ICD procedure
(`ICD_PRCDR_CD1..25`) is checked against the **ICD-10 code set of the claim's discharge fiscal year**
(FY2016-FY2023 CMS order files). Claims before ICD-10 took effect are checked against FY2016, the
first ICD-10 set. The first matching rule wins:

| Status | Meaning | Example |
|---|---|---|
| `valid` | Billable in that fiscal year | `E119` |
| `header_not_billable` | In the code set, but a category that needs more characters | `W19`, `S46009` |
| `truncated` | Not in the code set, but the start of a billable code | `S134XX` (needs `A`/`D`/`S`) |
| `not_valid_in_fiscal_year` | Billable in another fiscal year only | `N1830` before FY2021 |
| `diagnosis_code_in_procedure_field` | An ICD-10-CM code in a procedure field | `Z741` |
| `invalid_format` | Not shaped like a code of that system | |
| `not_in_code_set` | Well formed but unknown | `T148XX` |
| `code_set_not_loaded` | No code set loaded for that fiscal year | |

"Truncated" is an equi-join against a table of every proper prefix of every billable code
(`ref_icd10_billable_prefixes`), so it stays fast on the full code sets.

Code problems are **not** quarantined. They are systemic in this source (about 25% of principal
diagnoses and 86% of procedure codes are not valid), so quarantine would empty the tables. Payers face
the same choice with real feeds. Conformance is reported in `silver_dq_code_conformance`, and a warn
test flags any fiscal year where fewer than 95% of principal diagnoses are valid.

**HCPCS** is checked by **format only**: CPT Category I/II/III/PLA shapes, HCPCS Level II
(`[A-V]####`), a two-character modifier in the code field, or invalid. CPT descriptions are
AMA-copyrighted, so this project ships no CPT table and makes no claim that a CPT code exists.

**Known limitation:** mid-year (April 1) code-set updates are not loaded. For example, `U071` (COVID-19),
effective 2020-04-01, shows as `not_valid_in_fiscal_year` on FY2020 claims. The generator also uses it
on claims before 2020.

## 5. Silver: eligibility and member-months

### Definition

One row per member per calendar month in which the member is entitled to Medicare
(`MDCR_ENTLMT_BUYIN_IND_mm` is 1, 2, 3, A, B, or C) and was alive on the first day of the month.
Each month comes from **that month's own indicator**, so member-months are exact, not inferred from
annual counts.

| Flag | Rule |
|---|---|
| `has_part_a` / `has_part_b` | Entitlement code 1/3/A/C and 2/3/B/C |
| `has_state_buyin` | Code A, B, or C |
| `has_part_c` | `HMO_IND_mm` in 1/2/A/B/C, or a Part C contract in `PTC_CNTRCT_ID_mm` (this release leaves `HMO_IND` blank) |
| `has_part_d` | A Part D contract in `PTD_CNTRCT_ID_mm` (not 0/N/*) |
| `is_full_dual` / `is_partial_dual` | `DUAL_STUS_CD_mm` in 02/04/08, or in 01/03/05/06 |
| `is_ffs_eligible` | Parts A and B; also excludes Part C months only when `part_c_excludes_from_ffs` is true |
| `in_claims_window` | Month between 2015-03 and 2023-02, the period the published claims can cover |

### Approximations and choices, stated plainly

- **Part C and the FFS denominator.** The CCW convention drops Medicare Advantage months from FFS
  denominators because MA care is not billed as FFS. Synthea exports FFS claims for Part C enrollees
  anyway (user guide 3.3.3), and 58.6% of member-months have a Part C contract. Dropping them would
  divide all the claims by 41% of the members and overstate PMPM, so the default keeps them. The rule
  is one dbt var (`part_c_excludes_from_ffs`) for use on real data.
- **The source is coarse even where we are exact.** In every published file, each member is entitled
  for all 12 months of every year they appear in (all 3 months of the partial 2025 file). There is no
  mid-year enrollment or disenrollment, and there are no deaths. Member-months are exact with respect to the file;
  the file itself is coarse. Partial years and deaths are therefore tested with an injected edge case
  (a member enrolled from April who dies in February).
- **Coverage beyond the claims.** The enrollment files run through 2025 and grow to 10,000 members,
  but claims stop on 2023-03-02, and CMS describes the population as 8,671 members (the size of the
  2022 file). `in_claims_window` marks the months where a claim could exist.
- **Annual counts that cannot be reconciled.** Part A member-months must equal
  `BENE_HI_CVRAGE_TOT_MONS` for every member-year (an error-level test, which passes on the full
  file). Part B and Part D differences are warnings: `BENE_SMI_CVRAGE_TOT_MONS` mirrors the Part A
  count even for Part A-only members, and `PTD_PLAN_CVRG_MONS` disagrees for about 2% of members.
  `BENE_STATE_BUYIN_TOT_MONS` (always 12), `BENE_HMO_CVRAGE_TOT_MONS`, and `DUAL_ELGBL_MONS` (always 0)
  are fixed placeholder values in this release and are not used.
- **Age** is exact age at the first of the month (`age(month_start, birth_date)`).

### Structural errors (quarantined member-years)

Missing member ID; missing or invalid reference year, or one that does not match the file's year;
missing or invalid birth date; invalid death date or death before birth; an entitlement code outside
0/1/2/3/A/B/C; the same member-year in two arrivals.

## 6. What the tests guarantee

| Layer | Tests |
|---|---|
| Contracts | 12 unit tests covering each rule family and the small-batch floor |
| Fetch | Download plan, checksum manifest, no re-download, truncated downloads rejected (offline, fake HTTP) |
| Landing and bronze | Replay is lossless and idempotent; zips yield only the order file; loads are ordered, lineage-stamped, idempotent, versioned on re-delivery, blocked on contract failure, and refuse tampered files |
| dbt (every build) | Keys unique and not null; relationships (lines -> claims, diagnoses/procedures -> claims, member-months -> members); accepted values; ranges; bronze rows accounted for; quarantine within budget; Part A months reconcile; warnings for quarantine, code conformance, Part B/D counts, and claims without Part A eligibility |
| dbt gold (every build) | Keys and relationships; each mart recomputed from silver (member-months, admits, days, and every in-window dollar, year by year); rate formulas; readmissions recomputed as the first stay 1-30 days after discharge, and every stay given exactly one index decision; warning for overlapping stays |
| End to end | Fixture run; gold marts on the fixture; arrivals over time; corrected re-delivery; exact and conflicting duplicates; contract rejection stops the run before dbt; quarantine over budget fails the build; partial-year enrollment and death; injected in-hospital death, transfer, discharge against advice, and late discharge in the readmission mart |
