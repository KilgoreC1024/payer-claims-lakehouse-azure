-- The admits and days per 1,000 mart balances to silver, year by year (calendar year of admission):
--   * inpatient_admits + stays outside the denominator (PMPM mart) = silver claims admitted in a
--     claims-window month, so every in-window stay is either a rate numerator or reported as outside
--   * member_months = silver member-months in the claims window and FFS-eligible
--   * admits and days = the member-month fact, and the rates follow their formulas (per 1,000 member-years)
-- Returns a row per year and check that fails.

with window_claims as (

    select
        cast(year(coalesce(admission_date, from_date)) as integer)          as year,
        count(*)                                                            as stays
    from {{ ref('silver_inpatient_claims') }}
    where date_trunc('month', coalesce(admission_date, from_date))
          between date '{{ var("claims_window_start") }}' and date '{{ var("claims_window_end") }}'
    group by 1

),

denominator as (

    select year, count(*) as member_months
    from {{ ref('silver_member_months') }}
    where in_claims_window
      and is_ffs_eligible
    group by year

),

fact as (

    select year, sum(inpatient_admits) as admits, sum(inpatient_days) as days
    from {{ ref('gold_inpatient_member_months') }}
    group by year

),

joined as (

    select
        y.year,
        coalesce(c.stays, 0)                                                as silver_stays,
        coalesce(d.member_months, 0)                                        as silver_member_months,
        coalesce(f.admits, 0)                                               as fact_admits,
        coalesce(f.days, 0)                                                 as fact_days,
        coalesce(p.stays_outside_denominator, 0)                            as stays_outside,
        coalesce(u.member_months, 0)                                        as member_months,
        coalesce(u.inpatient_admits, 0)                                     as admits,
        coalesce(u.inpatient_days, 0)                                       as days,
        u.admits_per_1000,
        u.days_per_1000
    from (select year from window_claims union select year from denominator) as y
    left join window_claims as c on c.year = y.year
    left join denominator as d on d.year = y.year
    left join fact as f on f.year = y.year
    left join {{ ref('gold_inpatient_pmpm_by_year') }} as p on p.year = y.year
    left join {{ ref('gold_inpatient_utilization_by_year') }} as u on u.year = y.year

)

select year, 'admits + stays outside the denominator = silver stays in the window' as failed_check,
       cast(silver_stays as double) as expected, cast(admits + stays_outside as double) as actual
from joined
where silver_stays <> admits + stays_outside

union all
select year, 'member_months = silver denominator', silver_member_months, member_months
from joined
where silver_member_months <> member_months

union all
select year, 'admits = member-month fact', fact_admits, admits
from joined
where fact_admits <> admits

union all
select year, 'days = member-month fact', fact_days, days
from joined
where fact_days <> days

union all
select year, 'admits_per_1000 = admits / (member_months / 12) * 1000', admits * 12000 / member_months, admits_per_1000
from joined
where member_months > 0
  and abs(admits_per_1000 - admits * 12000 / member_months) > 0.005

union all
select year, 'days_per_1000 = days / (member_months / 12) * 1000', days * 12000 / member_months, days_per_1000
from joined
where member_months > 0
  and abs(days_per_1000 - days * 12000 / member_months) > 0.005
