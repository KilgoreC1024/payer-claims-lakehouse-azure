-- Part B and Part D months from the monthly indicators versus the annual counts CMS reports.
-- A warning, not a gate, because in this synthetic release the annual counts are not reliable:
--   * BENE_SMI_CVRAGE_TOT_MONS equals the Part A count even for Part A-only members (code 1), so
--     every Part A-only member-year disagrees on Part B.
--   * PTD_PLAN_CVRG_MONS disagrees with the monthly Part D contracts for about 2% of members.
-- Not compared at all: BENE_STATE_BUYIN_TOT_MONS (always 12), BENE_HMO_CVRAGE_TOT_MONS and
-- DUAL_ELGBL_MONS (always 0) are fixed placeholder values (user guide section 6.1 and inspection).
-- Member-months use the monthly indicators, which are internally consistent with Part A.
{{ config(severity = 'warn') }}

with monthly as (

    select
        bene_id,
        year,
        count(*) filter (where has_part_b) as part_b_months,
        count(*) filter (where has_part_d) as part_d_months
    from {{ ref('silver_member_months') }}
    group by bene_id, year

)

select
    y.bene_id,
    y.ref_year,
    y.reported_part_b_months,
    coalesce(m.part_b_months, 0) as part_b_months,
    y.reported_part_d_months,
    coalesce(m.part_d_months, 0) as part_d_months
from {{ ref('stg_cms__beneficiary') }} as y
left join monthly as m on m.bene_id = y.bene_id and m.year = y.ref_year
where len(y.dq_errors) = 0
  and (
      coalesce(m.part_b_months, 0) <> coalesce(y.reported_part_b_months, 0)
      or coalesce(m.part_d_months, 0) <> coalesce(y.reported_part_d_months, 0)
  )
