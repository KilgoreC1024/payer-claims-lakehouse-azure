{#-
    Eligibility member-months: one row per member per calendar month with Medicare entitlement.
    Grain: (bene_id, month_start).

    Exact, not approximated: each month comes from that month's own indicator in the beneficiary file
    (MDCR_ENTLMT_BUYIN_IND_01..12), not from an annual count. A month counts when the member is entitled
    to Part A and/or Part B in it and was alive on its first day.

    Denominator flags for later PMPM and per-1,000 marts:
      is_ffs_eligible   Parts A and B, and not Part C when var('part_c_excludes_from_ffs') is true.
                        It is false by default for this source, because Synthea exports fee-for-service
                        claims for Part C enrollees too (dbt_project.yml explains the choice).
      in_claims_window  the month lies inside the period the published claims can cover
                        (var claims_window_start .. claims_window_end). The 2024-2025 enrollment files
                        and early 2015 have members but no possible claims.
-#}

with member_years as (

    select *
    from {{ ref('stg_cms__beneficiary') }}
    where len(dq_errors) = 0

),

months as (

    select
        y.*,
        cast(m as integer)                      as month_number,
        make_date(y.ref_year, cast(m as integer), 1) as month_start,
        y.entitlement_by_month[m]               as entitlement_code,
        y.hmo_by_month[m]                       as hmo_code,
        y.part_c_contract_by_month[m]           as part_c_contract_id,
        y.part_d_contract_by_month[m]           as part_d_contract_id,
        y.dual_status_by_month[m]               as dual_status_code
    from member_years as y
    cross join range(1, 13) as months(m)

),

flagged as (

    select
        *,
        entitlement_code in ('1', '3', 'A', 'C')                                    as has_part_a,
        entitlement_code in ('2', '3', 'B', 'C')                                    as has_part_b,
        entitlement_code in ('A', 'B', 'C')                                         as has_state_buyin,
        -- HMO_IND 1/2/A/B/C is Medicare Advantage (4 is an FFS demonstration); this release leaves
        -- HMO_IND blank and records Part C in the monthly contract ID instead.
        coalesce(hmo_code in ('1', '2', 'A', 'B', 'C'), false)
            or part_c_contract_id is not null                                       as has_part_c,
        coalesce(part_d_contract_id not in ('0', 'N', '*'), false)                  as has_part_d
    from months
    where entitlement_code is not null
      and entitlement_code <> '0'
      and (death_date is null or month_start <= death_date)

)

select
    bene_id,
    month_start,
    ref_year                                                                    as year,
    month_number                                                                as month,
    entitlement_code,
    has_part_a,
    has_part_b,
    has_part_a and has_part_b                                                   as has_part_a_and_b,
    has_state_buyin,
    has_part_c,
    part_c_contract_id,
    has_part_d,
    part_d_contract_id,
    dual_status_code,
    coalesce(dual_status_code in ('02', '04', '08'), false)                     as is_full_dual,
    coalesce(dual_status_code in ('01', '03', '05', '06'), false)               as is_partial_dual,
    has_part_a and has_part_b
        {%- if var('part_c_excludes_from_ffs') %} and not has_part_c{% endif %} as is_ffs_eligible,
    month_start between date '{{ var("claims_window_start") }}'
                    and date '{{ var("claims_window_end") }}'                   as in_claims_window,
    cast(date_part('year', age(month_start, birth_date)) as integer)            as age_at_month_start,
    sex_code,
    race_code,
    state_code,
    has_esrd,
    current_entitlement_reason_code,
    _arrival,
    _source_line
from flagged
