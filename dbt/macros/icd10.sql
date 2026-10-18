{#-
    Code-set status of each (code, code_set_fiscal_year) in `codes_relation`, checked against the
    ICD-10 code set of that fiscal year. The first matching rule wins:

      code_set_not_loaded              no code set loaded for that fiscal year
      valid                            billable in that fiscal year
      header_not_billable              in the code set, but a header that needs more characters
      truncated                        not in the code set, but the start of a billable code
                                       (e.g. an injury code missing its 7th character)
      not_valid_in_fiscal_year         billable in some other fiscal year, not this one
      diagnosis_code_in_procedure_field  (procedures only) an ICD-10-CM code in a procedure field
      invalid_format                   does not have the shape of a code in this system
      not_in_code_set                  well formed, but unknown
-#}
{% macro icd10_code_status(codes_relation, code_system, format_regex, wrong_system=none) -%}
    select
        c.code,
        c.code_set_fiscal_year,
        case
            when loaded.fiscal_year is null                  then 'code_set_not_loaded'
            when exact.is_billable                           then 'valid'
            when exact.code is not null                      then 'header_not_billable'
            when prefix.prefix is not null                   then 'truncated'
            when any_year.code is not null                   then 'not_valid_in_fiscal_year'
            {%- if wrong_system %}
            when wrong.code is not null                      then 'diagnosis_code_in_procedure_field'
            {%- endif %}
            when not regexp_full_match(c.code, '{{ format_regex }}') then 'invalid_format'
            else 'not_in_code_set'
        end as code_status
    from {{ codes_relation }} as c
    left join (
        select distinct fiscal_year from {{ ref('ref_icd10_codes') }} where code_system = '{{ code_system }}'
    ) as loaded
        on loaded.fiscal_year = c.code_set_fiscal_year
    left join {{ ref('ref_icd10_codes') }} as exact
        on exact.code_system = '{{ code_system }}'
       and exact.fiscal_year = c.code_set_fiscal_year
       and exact.code = c.code
    left join {{ ref('ref_icd10_billable_prefixes') }} as prefix
        on prefix.code_system = '{{ code_system }}'
       and prefix.fiscal_year = c.code_set_fiscal_year
       and prefix.prefix = c.code
    left join (
        select distinct code from {{ ref('ref_icd10_codes') }}
        where code_system = '{{ code_system }}' and is_billable
    ) as any_year
        on any_year.code = c.code
    {%- if wrong_system %}
    left join (
        select distinct code from {{ ref('ref_icd10_codes') }} where code_system = '{{ wrong_system }}'
    ) as wrong
        on wrong.code = c.code
    {%- endif %}
{%- endmacro %}
