-- Visibility: any quarantined row raises a warning. It does not fail the build while within budget.
{{ config(severity = 'warn') }}

select source, arrival, source_line, natural_key, dq_errors
from {{ ref('silver_quarantine') }}
