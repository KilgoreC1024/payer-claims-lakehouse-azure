{#-
    30-day all-cause readmissions, one row per inpatient stay. Grain: clm_id.

    The shape follows CMS's hospital-wide readmission measure: index stays, a 30-day follow-up, transfers
    and deaths excluded, the first readmission only. There is no risk adjustment and no planned-readmission
    algorithm, so the rate is unadjusted and all-cause (docs/marts.md lists every difference).

    Each stay gets the first exclusion that applies; a stay with none is an index stay:
      discharged_before_claims_window     discharged before var claims_window_start
      less_than_30_days_follow_up         discharge + 30 days is after var claims_window_end, so a
                                          readmission could fall after the last claim
      died_in_hospital                    discharge status 20, 40, 41, 42 (expired)
      left_against_medical_advice         discharge status 07
      transferred_to_acute_care           discharge status 02, 05, 66, or their planned-readmission
                                          variants 82, 85, 94: the receiving hospital's stay is the index
      continued_same_day                  the member's next stay starts on or before this discharge date
                                          (a transfer, or a same-day readmission Medicare bills as one
                                          stay); the last stay of the chain is the candidate index
      not_ffs_enrolled_through_follow_up  not FFS-eligible in every month from admission through
                                          discharge + 30 days, so a readmission might never be billed

    Readmission: the member's first stay admitted 1 to 30 days after an index stay's discharge, for any
    cause and whatever its own exclusions. A readmission can itself be an index stay.

    In this source PTNT_DSCHRG_STUS_CD is always "1" (home), so the status-based exclusions never fire on
    the published files; tests/test_pipeline_e2e.py injects the codes to exercise them.
-#}

{%- set follow_up_days = 30 -%}
{%- set died_codes = ['20', '40', '41', '42'] -%}
{%- set acute_transfer_codes = ['02', '05', '66', '82', '85', '94'] -%}

with stays as (

    select
        clm_id,
        bene_id,
        provider_ccn,
        admission_date,
        discharge_date,
        discharge_status_code,
        lead(admission_date) over (
            partition by bene_id order by admission_date, discharge_date, clm_id
        )                                                                   as next_admission_date
    from {{ ref('gold_inpatient_stays') }}

),

follow_up_enrollment as (

    select
        s.clm_id,
        date_diff(
            'month',
            date_trunc('month', s.admission_date),
            date_trunc('month', s.discharge_date + {{ follow_up_days }})
        ) + 1                                                               as months_required,
        count(mm.month_start)                                               as ffs_months
    from stays as s
    left join {{ ref('silver_member_months') }} as mm
      on mm.bene_id = s.bene_id
     and mm.is_ffs_eligible
     and mm.month_start between date_trunc('month', s.admission_date)
                            and s.discharge_date + {{ follow_up_days }}
    group by s.clm_id, s.admission_date, s.discharge_date

),

classified as (

    select
        s.*,
        case
            when s.discharge_date < date '{{ var("claims_window_start") }}'
                then 'discharged_before_claims_window'
            when s.discharge_date + {{ follow_up_days }} > date '{{ var("claims_window_end") }}'
                then 'less_than_30_days_follow_up'
            when s.discharge_status_code in ('{{ died_codes | join("', '") }}')
                then 'died_in_hospital'
            when s.discharge_status_code = '07'
                then 'left_against_medical_advice'
            when s.discharge_status_code in ('{{ acute_transfer_codes | join("', '") }}')
                then 'transferred_to_acute_care'
            when s.next_admission_date <= s.discharge_date
                then 'continued_same_day'
            when e.ffs_months < e.months_required
                then 'not_ffs_enrolled_through_follow_up'
        end                                                                 as index_exclusion_reason
    from stays as s
    join follow_up_enrollment as e using (clm_id)

),

first_readmission as (

    select
        i.clm_id,
        r.clm_id                                                            as readmission_clm_id,
        r.admission_date                                                    as readmission_date,
        date_diff('day', i.discharge_date, r.admission_date)                as days_to_readmission
    from classified as i
    join stays as r
      on r.bene_id = i.bene_id
     and r.admission_date between i.discharge_date + 1 and i.discharge_date + {{ follow_up_days }}
    where i.index_exclusion_reason is null
    qualify row_number() over (
        partition by i.clm_id order by r.admission_date, r.discharge_date, r.clm_id
    ) = 1

)

select
    c.clm_id,
    c.bene_id,
    c.provider_ccn,
    c.admission_date,
    c.discharge_date,
    cast(year(c.discharge_date) as integer)                                 as discharge_year,
    c.discharge_status_code,
    c.index_exclusion_reason is null                                        as is_index_stay,
    c.index_exclusion_reason,
    -- Only index stays have an outcome; excluded stays are NULL here, not false.
    case
        when c.index_exclusion_reason is null then r.readmission_clm_id is not null
    end                                                                     as is_readmitted_within_30_days,
    r.readmission_clm_id,
    r.readmission_date,
    r.days_to_readmission
from classified as c
left join first_readmission as r using (clm_id)
