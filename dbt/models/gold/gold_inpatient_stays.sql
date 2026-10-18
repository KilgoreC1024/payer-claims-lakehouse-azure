{#-
    One row per inpatient stay (silver claim), placed in the month it counts in and matched to the rate
    denominator. Grain: clm_id. Every gold mart reads stays from here, so they all count a stay the same way.

    A stay counts in its admission month (the incurred month): its payment, its admission, and all of its
    inpatient days go to the month the member was admitted. Each claim is one stay.

      in_claims_window    the admission month lies inside var claims_window_start .. claims_window_end
      in_ffs_denominator  the member has a denominator member-month (in_claims_window and is_ffs_eligible
                          in silver_member_months) in the admission month. Only these stays are numerators
                          of PMPM and per-1,000 rates, so numerator and denominator cover the same members
                          and months. In-window stays without one (for example a Part A-only member) are
                          reported beside the rates, not dropped silently.
-#}

with claims as (

    select
        *,
        coalesce(admission_date, from_date)                         as stay_admission_date
    from {{ ref('silver_inpatient_claims') }}

),

denominator_months as (

    select bene_id, month_start
    from {{ ref('silver_member_months') }}
    where in_claims_window
      and is_ffs_eligible

)

select
    c.clm_id,
    c.bene_id,
    c.provider_ccn,
    c.stay_admission_date                                           as admission_date,
    c.discharge_or_thru_date                                        as discharge_date,
    cast(date_trunc('month', c.stay_admission_date) as date)        as incurred_month,
    cast(year(c.stay_admission_date) as integer)                    as incurred_year,
    c.inpatient_days,
    c.payment_amount,
    -- CMS writes single-digit codes without the leading zero ("1"); the code lists are two-digit.
    lpad(c.discharge_status_code, 2, '0')                           as discharge_status_code,
    date_trunc('month', c.stay_admission_date)
        between date '{{ var("claims_window_start") }}'
            and date '{{ var("claims_window_end") }}'               as in_claims_window,
    d.bene_id is not null                                           as in_ffs_denominator
from claims as c
left join denominator_months as d
  on d.bene_id = c.bene_id
 and d.month_start = date_trunc('month', c.stay_admission_date)
