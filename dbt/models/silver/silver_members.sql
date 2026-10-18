{#-
    One row per member, with attributes from their most recent valid reference year and the span of
    years and months they appear in. Grain: bene_id.
-#}

with member_years as (

    select *
    from {{ ref('stg_cms__beneficiary') }}
    where len(dq_errors) = 0

),

latest as (

    select *
    from member_years
    qualify row_number() over (partition by bene_id order by ref_year desc) = 1

),

spans as (

    select
        bene_id,
        min(ref_year)                    as first_ref_year,
        max(ref_year)                    as last_ref_year,
        count(*)                         as member_years,
        count(distinct birth_date)       as distinct_birth_dates
    from member_years
    group by bene_id

),

months as (

    select
        bene_id,
        count(*)                                          as member_months,
        count(*) filter (where in_claims_window)          as member_months_in_claims_window,
        min(month_start)                                  as first_member_month,
        max(month_start)                                  as last_member_month
    from {{ ref('silver_member_months') }}
    group by bene_id

)

select
    l.bene_id,
    l.birth_date,
    l.death_date,
    l.sex_code,
    l.race_code,
    l.state_code,
    l.original_entitlement_reason_code,
    l.current_entitlement_reason_code,
    l.has_esrd,
    s.first_ref_year,
    s.last_ref_year,
    s.member_years,
    s.distinct_birth_dates,
    coalesce(m.member_months, 0)                    as member_months,
    coalesce(m.member_months_in_claims_window, 0)   as member_months_in_claims_window,
    m.first_member_month,
    m.last_member_month
from latest as l
join spans as s using (bene_id)
left join months as m using (bene_id)
