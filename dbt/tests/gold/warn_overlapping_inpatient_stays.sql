-- Stays of the same member that overlap (the next admission is before this discharge). Each claim counts
-- as one admission with all of its days, so overlaps inflate admits and days per 1,000; the readmission
-- mart chains them (continued_same_day). A warning, not a gate: an overlap is a source problem to
-- investigate, not a reason to stop the build.
{{ config(severity = 'warn') }}

select *
from (
    select
        clm_id,
        bene_id,
        admission_date,
        discharge_date,
        lead(clm_id) over member_stays                  as next_clm_id,
        lead(admission_date) over member_stays          as next_admission_date
    from {{ ref('gold_inpatient_stays') }}
    window member_stays as (partition by bene_id order by admission_date, discharge_date, clm_id)
)
where next_admission_date < discharge_date
