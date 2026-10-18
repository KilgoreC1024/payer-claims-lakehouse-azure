{#-
    30-day all-cause readmission rate by calendar year of the index discharge. Grain: year.

      readmission_rate = readmissions / index_stays

    Every stay discharged in the year is counted once, as an index stay or under exactly one exclusion,
    so stays = index_stays + the excluded_* columns (a tested reconciliation, which also catches an
    exclusion added to gold_readmission_30d_stays but missing here).
-#}

{%- set exclusion_reasons = [
    'discharged_before_claims_window',
    'less_than_30_days_follow_up',
    'died_in_hospital',
    'left_against_medical_advice',
    'transferred_to_acute_care',
    'continued_same_day',
    'not_ffs_enrolled_through_follow_up',
] -%}

select
    discharge_year                                                          as year,
    count(*)                                                                as stays,
    count(*) filter (where is_index_stay)                                   as index_stays,
    count(*) filter (where is_readmitted_within_30_days)                    as readmissions,
    round(
        count(*) filter (where is_readmitted_within_30_days)
        / nullif(count(*) filter (where is_index_stay), 0),
        4
    )                                                                       as readmission_rate,
    {%- for reason in exclusion_reasons %}
    count(*) filter (where index_exclusion_reason = '{{ reason }}')         as excluded_{{ reason }}{{ ',' if not loop.last }}
    {%- endfor %}
from {{ ref('gold_readmission_30d_stays') }}
group by discharge_year
