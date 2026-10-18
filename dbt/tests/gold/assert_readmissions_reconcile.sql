-- The readmission marts account for every stay, and every outcome follows the rule, recomputed from silver:
--   * every silver claim is exactly one row of gold_readmission_30d_stays (unique is tested separately)
--   * an index stay's readmission is the same member's first stay admitted 1-30 days after discharge,
--     and an index stay is not readmitted only when no such stay exists
--   * excluded stays carry no outcome
--   * by year: stays = index_stays + every excluded_* column, index_stays and readmissions match the
--     stay mart, and readmission_rate = readmissions / index_stays
-- Returns a row per stay or year and check that fails.

with stays as (

    select * from {{ ref('gold_readmission_30d_stays') }}

),

silver_stays as (

    select
        clm_id,
        bene_id,
        coalesce(admission_date, from_date)                                 as admission_date
    from {{ ref('silver_inpatient_claims') }}

),

expected_readmission as (

    select
        i.clm_id,
        min(r.admission_date)                                               as readmission_date
    from stays as i
    join silver_stays as r
      on r.bene_id = i.bene_id
     and r.admission_date between i.discharge_date + 1 and i.discharge_date + 30
    where i.is_index_stay
    group by i.clm_id

),

by_year as (

    select
        year,
        stays,
        index_stays,
        readmissions,
        readmission_rate,
        list_sum(list_value(*COLUMNS('^excluded_')))                        as excluded_stays
    from {{ ref('gold_readmission_30d_by_year') }}

),

stay_counts as (

    select
        discharge_year                                                      as year,
        count(*)                                                            as stays,
        count(*) filter (where is_index_stay)                               as index_stays,
        count(*) filter (where is_readmitted_within_30_days)                as readmissions
    from stays
    group by discharge_year

)

select 'all' as key, 'one row per silver claim' as failed_check
where (select count(*) from silver_stays) <> (select count(*) from stays)

union all
select s.clm_id, 'readmission is the first stay 1-30 days after discharge'
from stays as s
left join expected_readmission as e on e.clm_id = s.clm_id
where s.is_index_stay
  and (s.readmission_date is distinct from e.readmission_date
       or s.is_readmitted_within_30_days <> (e.readmission_date is not null))

union all
select s.clm_id, 'readmission belongs to the same member'
from stays as s
join silver_stays as r on r.clm_id = s.readmission_clm_id
where r.bene_id <> s.bene_id

union all
select clm_id, 'excluded stays have no outcome'
from stays
where not is_index_stay
  and (is_readmitted_within_30_days is not null or readmission_clm_id is not null)

union all
select cast(year as varchar), 'stays = index_stays + excluded'
from by_year
where stays <> index_stays + excluded_stays

union all
select cast(coalesce(y.year, c.year) as varchar), 'by-year counts match the stay mart'
from by_year as y
full outer join stay_counts as c on c.year = y.year
where y.stays is distinct from c.stays
   or y.index_stays is distinct from c.index_stays
   or y.readmissions is distinct from c.readmissions

union all
select cast(year as varchar), 'readmission_rate = readmissions / index_stays'
from by_year
where index_stays > 0
  and abs(readmission_rate - readmissions / index_stays) > 0.00005
