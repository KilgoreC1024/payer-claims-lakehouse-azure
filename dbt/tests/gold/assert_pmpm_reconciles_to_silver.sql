-- The PMPM mart balances to silver, year by year (calendar year of admission):
--   * inpatient_paid_amount + paid_amount_outside_denominator = paid on every silver claim admitted in a
--     claims-window month, so no in-window dollar is lost and the split shows which are outside the rate
--   * member_months = silver member-months in the claims window and FFS-eligible
--   * inpatient_paid_amount = the member-month fact, and inpatient_pmpm = paid / member-months
-- Returns a row per year and check that fails.

with window_claims as (

    select
        cast(year(coalesce(admission_date, from_date)) as integer)          as year,
        coalesce(sum(payment_amount), 0)                                    as paid
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

    select year, sum(inpatient_paid_amount) as paid
    from {{ ref('gold_inpatient_member_months') }}
    group by year

),

joined as (

    select
        y.year,
        coalesce(c.paid, 0)                                                 as silver_paid,
        coalesce(d.member_months, 0)                                        as silver_member_months,
        coalesce(f.paid, 0)                                                 as fact_paid,
        coalesce(p.member_months, 0)                                        as member_months,
        coalesce(p.inpatient_paid_amount, 0)                                as paid,
        coalesce(p.paid_amount_outside_denominator, 0)                      as paid_outside,
        p.inpatient_pmpm
    from (select year from window_claims union select year from denominator) as y
    left join window_claims as c on c.year = y.year
    left join denominator as d on d.year = y.year
    left join fact as f on f.year = y.year
    left join {{ ref('gold_inpatient_pmpm_by_year') }} as p on p.year = y.year

)

select year, 'paid + paid outside the denominator = silver paid in the window' as failed_check,
       cast(silver_paid as double) as expected, cast(paid + paid_outside as double) as actual
from joined
where silver_paid <> paid + paid_outside

union all
select year, 'member_months = silver denominator', silver_member_months, member_months
from joined
where silver_member_months <> member_months

union all
select year, 'paid = member-month fact', fact_paid, paid
from joined
where fact_paid <> paid

union all
select year, 'inpatient_pmpm = paid / member_months', paid / nullif(member_months, 0), inpatient_pmpm
from joined
where member_months > 0
  and abs(inpatient_pmpm - paid / member_months) > 0.005
