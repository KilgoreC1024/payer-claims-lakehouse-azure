-- Error budget: per source, quarantined rows may not exceed the larger of var('quarantine_error_rate')
-- of the source's rows and var('quarantine_error_floor') rows (the same rule as the contracts). Above
-- budget the problem is systemic, not a few bad rows, and the build must fail.

with totals as (

    select 'cms_inpatient' as source, count(*) as total_rows from {{ ref('stg_cms__inpatient_lines') }}
    union all
    select 'cms_beneficiary', count(*) from {{ ref('stg_cms__beneficiary') }}

),

quarantined as (

    select source, count(*) as quarantined_rows from {{ ref('silver_quarantine') }} group by source

)

select
    t.source,
    t.total_rows,
    coalesce(q.quarantined_rows, 0)                                                 as quarantined_rows,
    greatest({{ var('quarantine_error_floor') }}, {{ var('quarantine_error_rate') }} * t.total_rows) as allowed_rows
from totals as t
left join quarantined as q using (source)
where coalesce(q.quarantined_rows, 0)
      > greatest({{ var('quarantine_error_floor') }}, {{ var('quarantine_error_rate') }} * t.total_rows)
