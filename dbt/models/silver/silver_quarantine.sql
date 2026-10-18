-- Rows that failed a structural rule, with the reasons and a pointer back to the exact landed line.
-- Quarantine keeps bad data visible and auditable instead of dropping or coercing it silently.

select
    'cms_inpatient'                                     as source,
    _arrival                                            as arrival,
    _source_file                                        as source_file,
    _source_line                                        as source_line,
    _source_sha256                                      as source_sha256,
    concat_ws('|', clm_id, clm_line_num)                as natural_key,
    bene_id,
    dq_errors,
    current_timestamp                                   as quarantined_at
from {{ ref('stg_cms__inpatient_lines') }}
where len(dq_errors) > 0
