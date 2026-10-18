{#-
    How well the claims conform to their code sets, by field, role, and status: the measurement that
    replaces quarantining claims for code problems. One row per (code_field, role, status, fiscal year).

    `share_of_role` is the share of that field/role's codes with this status in that fiscal year, so a
    jump in `truncated` or `not_in_code_set` from one arrival to the next is easy to spot.
-#}

with codes as (

    select 'diagnosis' as code_field, dx_role as role, code_status as status,
           code_set_fiscal_year as fiscal_year, dx_code as code
    from {{ ref('silver_inpatient_diagnoses') }}

    union all
    select 'procedure', 'icd_procedure', code_status, code_set_fiscal_year, px_code
    from {{ ref('silver_inpatient_procedures') }}

    union all
    select 'hcpcs', 'revenue_line', l.hcpcs_format, c.discharge_fiscal_year, l.hcpcs_code
    from {{ ref('silver_inpatient_claim_lines') }} as l
    join {{ ref('silver_inpatient_claims') }} as c using (clm_id)

)

select
    code_field,
    role,
    status,
    fiscal_year,
    count(*)                                                            as code_count,
    count(distinct code)                                                as distinct_codes,
    round(count(*) / sum(count(*)) over (partition by code_field, role, fiscal_year), 4) as share_of_role,
    list(distinct code order by code)[1:5]                              as example_codes
from codes
group by code_field, role, status, fiscal_year
