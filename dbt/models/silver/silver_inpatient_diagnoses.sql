{#-
    Every diagnosis on every silver inpatient claim, one row per position, with its ICD-10-CM status for
    the claim's discharge fiscal year. Grain: (clm_id, dx_role, dx_sequence).

      dx_role          admitting | principal | other (ICD_DGNS_CD1..25) | external_cause
                       (FST_DGNS_E_CD is external_cause 0, ICD_DGNS_E_CD1..12 are 1..12)
      code_status      see macros/icd10.sql

    Claims discharged before ICD-10 took effect (2015-10-01) are checked against the FY2016 code set,
    the earliest ICD-10 set, and keep `coded_before_icd10_effective = true` so they can be excluded.
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

    select clm_id, admitting_dx_code, principal_dx_code, dx_codes, dx_poa_indicators, external_cause_codes
    from {{ ref('stg_cms__inpatient_lines') }}
    where dedup_status = 'kept'
      and len(dq_errors) = 0
    qualify row_number() over (partition by clm_id order by clm_line_num) = 1

),

positions as (

    select clm_id, 'admitting' as dx_role, 0 as dx_sequence, admitting_dx_code as dx_code, null::varchar as poa_indicator
    from first_lines

    union all
    select clm_id, 'principal', 0, principal_dx_code, null
    from first_lines

    union all
    select clm_id, 'other', i, dx_codes[i], dx_poa_indicators[i]
    from first_lines, range(1, 26) as seq(i)

    union all
    select clm_id, 'external_cause', i, external_cause_codes[i + 1], null
    from first_lines, range(0, 13) as seq(i)

),

diagnoses as (

    select p.*, c.bene_id, c.discharge_fiscal_year, c.code_set_fiscal_year, c.coded_before_icd10_effective
    from positions as p
    join claims as c using (clm_id)
    where p.dx_code is not null

),

distinct_codes as (

    select distinct dx_code as code, code_set_fiscal_year from diagnoses

),

status as (

    {{ icd10_code_status('distinct_codes', 'ICD-10-CM', '[A-Z][0-9][0-9A-Z]{1,5}') }}

)

select
    d.clm_id,
    d.bene_id,
    d.dx_role,
    cast(d.dx_sequence as integer)    as dx_sequence,
    d.dx_code,
    d.poa_indicator,
    d.discharge_fiscal_year,
    d.code_set_fiscal_year,
    d.coded_before_icd10_effective,
    s.code_status,
    s.code_status = 'valid'           as is_valid_code
from diagnoses as d
join status as s
  on s.code = d.dx_code
 and s.code_set_fiscal_year = d.code_set_fiscal_year
