-- The member-month fact neither loses nor double counts. Recomputed from silver, without the gold models:
--   member-months        silver member-months in the claims window and FFS-eligible
--   admits, days, paid   silver claims whose member has such a member-month in the admission month
-- A mismatch means the join fanned out (a duplicated member-month), dropped stays, or counted a stay in a
-- different month than intended. Returns a row per measure that differs.

with denominator as (

    select bene_id, month_start
    from {{ ref('silver_member_months') }}
    where in_claims_window
      and is_ffs_eligible

),

attributed_claims as (

    select c.*
    from {{ ref('silver_inpatient_claims') }} as c
    where exists (
        select 1
        from denominator as d
        where d.bene_id = c.bene_id
          and d.month_start = date_trunc('month', coalesce(c.admission_date, c.from_date))
    )

),

expected as (

    select
        (select count(*) from denominator)                                  as member_months,
        (select count(*) from attributed_claims)                            as admits,
        (select coalesce(sum(inpatient_days), 0) from attributed_claims)    as days,
        (select coalesce(sum(payment_amount), 0) from attributed_claims)    as paid

),

actual as (

    select
        count(*)                                                            as member_months,
        coalesce(sum(inpatient_admits), 0)                                  as admits,
        coalesce(sum(inpatient_days), 0)                                    as days,
        coalesce(sum(inpatient_paid_amount), 0)                             as paid
    from {{ ref('gold_inpatient_member_months') }}

)

select measure, expected, actual
from (
    select 'member_months' as measure, cast(e.member_months as decimal(38, 2)) as expected,
           cast(a.member_months as decimal(38, 2)) as actual
    from expected as e, actual as a
    union all
    select 'inpatient_admits', cast(e.admits as decimal(38, 2)), cast(a.admits as decimal(38, 2))
    from expected as e, actual as a
    union all
    select 'inpatient_days', cast(e.days as decimal(38, 2)), cast(a.days as decimal(38, 2))
    from expected as e, actual as a
    union all
    select 'inpatient_paid_amount', cast(e.paid as decimal(38, 2)), cast(a.paid as decimal(38, 2))
    from expected as e, actual as a
)
where expected <> actual
