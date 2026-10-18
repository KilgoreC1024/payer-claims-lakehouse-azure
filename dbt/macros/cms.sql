{#- Parsing helpers for CMS RIF text. Bronze keeps every column as VARCHAR exactly as published; these
    macros are the only place that decides what a value means. -#}

{#- Whitespace-only means "no value": CMS writes most empty fields as a single space. -#}
{% macro cms_value(column) -%}
    nullif(trim("{{ column }}"), '')
{%- endmacro %}

{% macro cms_date(column) -%}
    cast(try_strptime({{ cms_value(column) }}, '{{ var("cms_date_format") }}') as date)
{%- endmacro %}

{% macro cms_int(column) -%}
    try_cast({{ cms_value(column) }} as integer)
{%- endmacro %}

{% macro cms_amount(column) -%}
    try_cast({{ cms_value(column) }} as decimal(18, 2))
{%- endmacro %}

{#- An error label when a present value does not parse as `kind`, else NULL. -#}
{% macro parse_error(column, kind) -%}
    case
        when {{ cms_value(column) }} is not null
         and {% if kind == 'date' %}{{ cms_date(column) }}{% elif kind == 'int' %}{{ cms_int(column) }}{% else %}{{ cms_amount(column) }}{% endif %} is null
        then '{{ column }}: not a valid {{ kind }}'
    end
{%- endmacro %}

{#- A list of the trimmed values of numbered columns: PREFIX1..PREFIXn, or PREFIX01..PREFIX12 with pad=2. -#}
{% macro cms_list(prefix, n, pad=0) -%}
    [{% for i in range(1, n + 1) %}{{ cms_value(prefix ~ ('%0' ~ pad ~ 'd') % i if pad else prefix ~ i) }}{{ ', ' if not loop.last }}{% endfor %}]
{%- endmacro %}

{#- Federal fiscal year: October 1 starts the next fiscal year (2015-10-01 is FY2016). ICD-10 code sets
    are released per fiscal year, so a code is valid or not *for a discharge date's fiscal year*. -#}
{% macro fiscal_year(date_expression) -%}
    (year({{ date_expression }}) + case when month({{ date_expression }}) >= 10 then 1 else 0 end)
{%- endmacro %}

{#- Bronze keeps every version of every arrival. Silver reads the newest version of each arrival, so a
    corrected re-delivery replaces the original batch as a whole (rows it dropped disappear too). -#}
{% macro latest_arrival_versions(source_relation) -%}
    select *
    from {{ source_relation }}
    qualify _loaded_at = max(_loaded_at) over (partition by _arrival)
{%- endmacro %}

{#- Arrival order for "latest wins" decisions; undated batches rank below every dated one. -#}
{% macro arrival_rank(arrival) -%}
    coalesce(try_cast({{ arrival }} as integer), -1)
{%- endmacro %}
