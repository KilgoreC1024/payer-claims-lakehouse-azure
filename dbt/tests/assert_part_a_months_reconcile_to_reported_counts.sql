-- Member-months are built from the monthly indicators; CMS also reports an annual Part A count for the
-- same year (BENE_HI_CVRAGE_TOT_MONS). The two must agree exactly, member by member and year by year.
-- A mismatch means the monthly flags were misread (wrong column, wrong code mapping) or the file is
-- internally inconsistent; either way the denominators cannot be trusted.

with monthly as (

    select bene_id, year, count(*) filter (where has_part_a) as part_a_months
    from {{ ref('silver_member_months') }}
    group by bene_id, year

)

select
    y.bene_id,
    y.ref_year,
    y.reported_part_a_months,
    coalesce(m.part_a_months, 0) as part_a_months
from {{ ref('stg_cms__beneficiary') }} as y
left join monthly as m
  on m.bene_id = y.bene_id
 and m.year = y.ref_year
where len(y.dq_errors) = 0
  and coalesce(m.part_a_months, 0) <> coalesce(y.reported_part_a_months, 0)
