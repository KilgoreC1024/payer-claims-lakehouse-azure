{#-
    ICD-10-CM and ICD-10-PCS codes by federal fiscal year, from the CMS order files.
    Grain: (code_system, fiscal_year, code).

    `is_billable` is the order file's flag: 1 = valid for HIPAA-covered transactions; 0 = a header
    (a category or table that has more specific codes beneath it).
-#}

select
    'ICD-10-CM'                       as code_system,
    cast(_arrival as integer)         as fiscal_year,
    code,
    valid_flag = '1'                  as is_billable,
    cast(order_number as integer)     as order_number
from ({{ latest_arrival_versions(source('bronze', 'ref_icd10cm')) }})

union all

select
    'ICD-10-PCS',
    cast(_arrival as integer),
    code,
    valid_flag = '1',
    cast(order_number as integer)
from ({{ latest_arrival_versions(source('bronze', 'ref_icd10pcs')) }})
