{#-
    One row per inpatient claim line (revenue center line) that survived deduplication and the
    structural checks. Grain: (clm_id, clm_line_num).

    HCPCS codes are classified by *format only*. CPT (Level I) codes are AMA-copyrighted, so this project
    ships no CPT description table and makes no claim about whether a CPT code exists; it only checks that
    the code has a CPT or HCPCS Level II shape.
-#}

select
    clm_id,
    clm_line_num,
    bene_id,
    revenue_center_code,
    hcpcs_code,
    case
        when hcpcs_code is null                                   then 'missing'
        when regexp_full_match(hcpcs_code, '[0-9]{5}')            then 'cpt_category_i'
        when regexp_full_match(hcpcs_code, '[0-9]{4}F')           then 'cpt_category_ii'
        when regexp_full_match(hcpcs_code, '[0-9]{4}T')           then 'cpt_category_iii'
        when regexp_full_match(hcpcs_code, '[0-9]{4}U')           then 'cpt_pla'
        when regexp_full_match(hcpcs_code, '[A-V][0-9]{4}')       then 'hcpcs_level_ii'
        -- Two characters is the shape of a HCPCS modifier (QS, Q3), not of a procedure code.
        when regexp_full_match(hcpcs_code, '[A-Z0-9]{2}')         then 'modifier_in_code_field'
        else 'invalid_format'
    end as hcpcs_format,
    _arrival,
    _source_sha256,
    _source_line
from {{ ref('stg_cms__inpatient_lines') }}
where dedup_status = 'kept'
  and len(dq_errors) = 0
