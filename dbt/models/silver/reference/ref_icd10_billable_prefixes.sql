{#-
    Every proper prefix (3 or more characters) of every billable code, by code system and fiscal year.
    Grain: (code_system, fiscal_year, prefix).

    A claim code that is not itself in the code set but is a prefix of a billable code is *truncated*:
    for example S134XX is the start of S134XXA/D/S, which need the 7th "encounter" character. Turning the
    prefix test into an equi-join keeps it fast on the full code sets (no row-by-row LIKE scan).
-#}

select distinct
    code_system,
    fiscal_year,
    left(code, n) as prefix
from {{ ref('ref_icd10_codes') }},
     range(3, 7) as lengths(n)
where is_billable
  and length(code) > n
