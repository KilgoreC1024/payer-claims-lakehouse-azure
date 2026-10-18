{#-
    Every ICD procedure on every silver inpatient claim (ICD_PRCDR_CD1..25 with PRCDR_DT1..25), with its
    ICD-10-PCS status for the claim's discharge fiscal year. Grain: (clm_id, px_sequence).

    A valid ICD-10-PCS code is exactly seven characters. This source often puts ICD-10-CM diagnosis codes
    (Z741, G4730) or three-character PCS table headers (GZ3) in these fields; `code_status` says which.
-#}

with claims as (

    select
        clm_id,
        bene_id,
        discharge_fiscal_year,
        coded_before_icd10_effective,
        greatest(discharge_fiscal_year, {{ var('first_icd10_fiscal_year') }}) as code_set_fiscal_year
    from {{ ref('silver_inpatient_claims') }}

),

first_lines as (

    select clm_id, px_codes, px_dates
    from {{ ref('stg_cms__inpatient_lines') }}
    where dedup_status = 'kept'
      and len(dq_errors) = 0
    qualify row_number() over (partition by clm_id order by clm_line_num) = 1

),

procedures as (

    select
        l.clm_id,
        cast(i as integer)   as px_sequence,
        l.px_codes[i]        as px_code,
        l.px_dates[i]        as px_date,
        c.bene_id,
        c.discharge_fiscal_year,
        c.code_set_fiscal_year,
        c.coded_before_icd10_effective
    from first_lines as l
    cross join range(1, 26) as seq(i)
    join claims as c on c.clm_id = l.clm_id
    where l.px_codes[i] is not null

),

distinct_codes as (

    select distinct px_code as code, code_set_fiscal_year from procedures

),

status as (

    {{ icd10_code_status('distinct_codes', 'ICD-10-PCS', '[0-9A-HJ-NP-Z]{7}', wrong_system='ICD-10-CM') }}

)

select
    p.clm_id,
    p.bene_id,
    p.px_sequence,
    p.px_code,
    p.px_date,
    p.discharge_fiscal_year,
    p.code_set_fiscal_year,
    p.coded_before_icd10_effective,
    s.code_status,
    s.code_status = 'valid'           as is_valid_code
from procedures as p
join status as s
  on s.code = p.px_code
 and s.code_set_fiscal_year = p.code_set_fiscal_year
