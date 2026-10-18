{#- Generic tests written in-project rather than pulled from a package, so the build needs no network
    access (`dbt deps`) and every rule is visible in this repository. Same set as the sibling platform. -#}

{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}

{% test value_between(model, column_name, min_value=none, max_value=none) %}
select {{ column_name }}
from {{ model }}
where {{ column_name }} is not null
  and (
    {%- if min_value is not none %} {{ column_name }} < {{ min_value }} {% else %} false {% endif %}
    or
    {%- if max_value is not none %} {{ column_name }} > {{ max_value }} {% else %} false {% endif %}
  )
{% endtest %}

{% test row_count_at_least(model, min_rows) %}
select count(*) as n from {{ model }} having count(*) < {{ min_rows }}
{% endtest %}
