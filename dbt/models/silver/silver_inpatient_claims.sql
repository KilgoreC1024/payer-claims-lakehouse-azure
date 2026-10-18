{#-
    One row per inpatient claim (stay). Grain: clm_id.

    Claim-level fields repeat on every line of a RIF claim; staging has already quarantined any claim
    whose lines disagree, so taking them from the first line is safe.

    Length of stay follows the usual convention: discharge date minus admission date, and a same-day stay
    counts as one inpatient day. The fiscal year of the discharge decides which ICD-10 code set the claim
    is validated against (see silver_inpatient_diagnoses).
-#}

with lines as (

    select *
    from {{ ref('stg_cms__inpatient_lines') }}
    where dedup_status = 'kept'
      and len(dq_errors) = 0

),

claims as (

    select
        *,
        count(*) over (partition by clm_id) as line_count
    from lines
    qualify row_number() over (partition by clm_id order by clm_line_num) = 1

)

select
    clm_id,
    bene_id,
    claim_type_code,
    claim_frequency_code,
    from_date,
    thru_date,
    admission_date,
    discharge_date,
    processed_date,
    coalesce(discharge_date, thru_date)                                         as discharge_or_thru_date,
    {{ fiscal_year('coalesce(discharge_date, thru_date)') }}                     as discharge_fiscal_year,
    coalesce(discharge_date, thru_date) < date '{{ var("icd10_effective_date") }}' as coded_before_icd10_effective,
    date_diff('day', coalesce(admission_date, from_date), coalesce(discharge_date, thru_date)) as length_of_stay_days,
    greatest(date_diff('day', coalesce(admission_date, from_date), coalesce(discharge_date, thru_date)), 1) as inpatient_days,
    provider_ccn,
    provider_state_code,
    organization_npi,
    attending_npi,
    admission_type_code,
    admission_source_code,
    discharge_status_code,
    drg_code,
    drg_code is not null                                                        as has_drg,
    payment_amount,
    total_charge_amount,
    primary_payer_paid_amount,
    inpatient_deductible_amount,
    part_a_coinsurance_amount,
    utilization_day_count,
    admitting_dx_code,
    principal_dx_code,
    len(list_filter(dx_codes, lambda c: c is not null))                         as dx_code_count,
    len(list_filter(px_codes, lambda c: c is not null))                         as px_code_count,
    line_count,
    _arrival,
    _source_sha256
from claims
