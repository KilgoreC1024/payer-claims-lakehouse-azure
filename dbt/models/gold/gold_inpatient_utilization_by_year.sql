{#-
    Inpatient admits and days per 1,000 member-years by calendar year of admission. Grain: year.

      member_years            = member_months / 12
      admits_per_1000         = inpatient_admits / member_years * 1,000
      days_per_1000           = inpatient_days / member_years * 1,000
      average_length_of_stay  = inpatient_days / inpatient_admits

    All four come from gold_inpatient_member_months, so numerators and denominators cover the same
    members and months. Each stay is one admission, with all of its days in the admission month; stays
    chained on the same day are not merged here (gold_readmission_30d_stays does that for readmissions).
-#}

with by_year as (

    select
        year,
        count(*)                                as member_months,
        cast(sum(inpatient_admits) as bigint)   as inpatient_admits,
        cast(sum(inpatient_days) as bigint)     as inpatient_days
    from {{ ref('gold_inpatient_member_months') }}
    group by year

)

select
    year,
    member_months,
    round(member_months / 12, 2)                                    as member_years,
    inpatient_admits,
    inpatient_days,
    round(inpatient_admits * 12000 / member_months, 2)              as admits_per_1000,
    round(inpatient_days * 12000 / member_months, 2)                as days_per_1000,
    round(inpatient_days / nullif(inpatient_admits, 0), 2)          as average_length_of_stay
from by_year
