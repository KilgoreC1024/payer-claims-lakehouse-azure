-- Inpatient stays are Part A services. A claim whose member has no Part A member-month in the month of
-- admission cannot be attributed to a PMPM denominator; later marts must decide what to do with it.
{{ config(severity = 'warn') }}

select
    c.clm_id,
    c.bene_id,
    coalesce(c.admission_date, c.from_date) as admission_date
from {{ ref('silver_inpatient_claims') }} as c
left join {{ ref('silver_member_months') }} as mm
  on mm.bene_id = c.bene_id
 and mm.month_start = date_trunc('month', coalesce(c.admission_date, c.from_date))
 and mm.has_part_a
where mm.bene_id is null
