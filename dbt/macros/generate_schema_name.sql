{#- Use the configured schema name as-is (staging, silver) instead of dbt's default
    "<target_schema>_<custom_schema>" so the medallion layers have stable, readable names. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name is not none else target.schema }}
{%- endmacro %}
