{#-
    Member-month fact for inpatient care: one row per member-month in the rate denominator, with the
    inpatient stays counted in that month. Grain: (bene_id, month_start).

    Denominator: silver_member_months where in_claims_window and is_ffs_eligible. Months outside the
    claims window have members but no possible claims; months that are not FFS-eligible (no Part B, or
    Part C when var part_c_excludes_from_ffs is true) do not belong in a fee-for-service denominator.

    Numerator: stays with in_ffs_denominator from gold_inpatient_stays, by admission month. A month with
    no stay has zero admits, days, and paid amount, so summing any column over any slice of this table
    gives a numerator and count(*) gives its matching denominator.
-#}

with denominator as (

    select *
    from {{ ref('silver_member_months') }}
    where in_claims_window
      and is_ffs_eligible

),

stays_by_month as (

    select
        bene_id,
        incurred_month                  as month_start,
        count(*)                        as inpatient_admits,
        sum(inpatient_days)             as inpatient_days,
        sum(payment_amount)             as inpatient_paid_amount
    from {{ ref('gold_inpatient_stays') }}
    where in_ffs_denominator
    group by bene_id, incurred_month

)

select
    d.bene_id,
    d.month_start,
    d.year,
    d.month,
    d.has_part_c,
    d.has_part_d,
    d.is_full_dual,
    d.is_partial_dual,
    d.age_at_month_start,
    d.sex_code,
    d.race_code,
    d.state_code,
    d.has_esrd,
    coalesce(s.inpatient_admits, 0)                                 as inpatient_admits,
    cast(coalesce(s.inpatient_days, 0) as bigint)                   as inpatient_days,
    cast(coalesce(s.inpatient_paid_amount, 0) as decimal(18, 2))    as inpatient_paid_amount
from denominator as d
left join stays_by_month as s
  on s.bene_id = d.bene_id
 and s.month_start = d.month_start
