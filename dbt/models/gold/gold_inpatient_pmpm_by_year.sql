{#-
    Inpatient paid amount per member per month (PMPM) by calendar year of admission. Grain: year.

      inpatient_pmpm = inpatient_paid_amount / member_months, both from gold_inpatient_member_months

    In-window stays without a denominator member-month are not in the PMPM. Their count and paid amount
    are reported beside it, so inpatient_paid_amount + paid_amount_outside_denominator is every in-window
    dollar in silver (a tested reconciliation). The claims window makes 2015 start in March and 2023 end
    in February; those years still give comparable rates because the denominator covers the same months.
-#}

with denominator as (

    select
        year,
        count(*)                        as member_months,
        sum(inpatient_paid_amount)      as inpatient_paid_amount
    from {{ ref('gold_inpatient_member_months') }}
    group by year

),

outside_denominator as (

    select
        incurred_year                   as year,
        count(*)                        as stays_outside_denominator,
        sum(payment_amount)             as paid_amount_outside_denominator
    from {{ ref('gold_inpatient_stays') }}
    where in_claims_window
      and not in_ffs_denominator
    group by incurred_year

)

select
    coalesce(d.year, o.year)                                                    as year,
    coalesce(d.member_months, 0)                                                as member_months,
    round(coalesce(d.member_months, 0) / 12, 2)                                 as member_years,
    cast(coalesce(d.inpatient_paid_amount, 0) as decimal(18, 2))                as inpatient_paid_amount,
    cast(round(d.inpatient_paid_amount / nullif(d.member_months, 0), 2) as decimal(18, 2)) as inpatient_pmpm,
    coalesce(o.stays_outside_denominator, 0)                                    as stays_outside_denominator,
    cast(coalesce(o.paid_amount_outside_denominator, 0) as decimal(18, 2))      as paid_amount_outside_denominator
from denominator as d
full outer join outside_denominator as o
  on o.year = d.year
