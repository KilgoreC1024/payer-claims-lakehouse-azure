-- Visibility, not a gate: warns for each fiscal year in which fewer principal diagnoses than
-- var('principal_dx_valid_target') are billable ICD-10-CM codes for that year. This source misses the
-- target in every year (truncated injury codes and header codes, docs/adr/0001); a real feed that
-- suddenly dropped below it would point at a coding or mapping change upstream.
{{ config(severity = 'warn') }}

select
    code_set_fiscal_year,
    count(*)                                    as principal_diagnoses,
    round(avg(case when is_valid_code then 1.0 else 0.0 end), 4) as valid_share
from {{ ref('silver_inpatient_diagnoses') }}
where dx_role = 'principal'
group by code_set_fiscal_year
having avg(case when is_valid_code then 1.0 else 0.0 end) < {{ var('principal_dx_valid_target') }}
