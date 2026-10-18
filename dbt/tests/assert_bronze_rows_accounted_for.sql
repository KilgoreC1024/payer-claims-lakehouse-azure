-- Nothing is lost between bronze and silver. For the newest version of every arrival, each bronze row
-- must end up in exactly one place: silver, quarantine, or counted as a collapsed exact duplicate or a
-- superseded older version. Returns a row per source whose books do not balance.

with bronze_inpatient as (

    select count(*) as n from ({{ latest_arrival_versions(source('bronze', 'cms_inpatient')) }})

),

inpatient as (

    select
        'cms_inpatient'                                                          as source,
        (select n from bronze_inpatient)                                         as bronze_rows,
        (select count(*) from {{ ref('silver_inpatient_claim_lines') }})         as in_silver,
        (select count(*) from {{ ref('silver_quarantine') }} where source = 'cms_inpatient') as quarantined,
        (select count(*) from {{ ref('stg_cms__inpatient_lines') }}
          where dedup_status in ('exact_duplicate', 'superseded'))               as collapsed

),

bronze_beneficiary as (

    select count(*) as n from ({{ latest_arrival_versions(source('bronze', 'cms_beneficiary')) }})

),

beneficiary as (

    select
        'cms_beneficiary',
        (select n from bronze_beneficiary),
        (select count(*) from {{ ref('stg_cms__beneficiary') }} where len(dq_errors) = 0),
        (select count(*) from {{ ref('silver_quarantine') }} where source = 'cms_beneficiary'),
        0

)

select *
from (select * from inpatient union all select * from beneficiary)
where bronze_rows <> in_silver + quarantined + collapsed
