{#-
    Typed inpatient claim lines from the newest version of every arrival, each with:

      dq_errors      structural problems that make the row untrustworthy -> routed to silver_quarantine
      dedup_status   what happens to the row when the same (CLM_ID, CLM_LINE_NUM) appears more than once:
                       kept             the one row that represents this claim line in silver
                       exact_duplicate  an identical copy of a kept row (same content); collapsed
                       superseded       an older version, replaced by a later arrival ("latest wins")
                       conflicting      two different versions in the same newest arrival; nobody can
                                        tell which is right, so every version is quarantined

    Code-set validity (ICD-10, HCPCS) is NOT an error here. It is measured in silver against the code set
    of the claim's discharge fiscal year, because this source fails it systematically (docs/adr/0001).
-#}

{%- set line_columns = ['CLM_LINE_NUM', 'REV_CNTR', 'HCPCS_CD', 'REV_CNTR_DDCTBL_COINSRNC_CD'] -%}
{%- set lineage = ['_source', '_arrival', '_source_file', '_source_sha256', '_source_line', '_loaded_at', '_contract_status'] -%}

with src as (

    {{ latest_arrival_versions(source('bronze', 'cms_inpatient')) }}

),

hashed as (

    select
        *,
        -- Content fingerprints (struct text includes field names and NULLs, so it is unambiguous).
        md5(struct_pack(*COLUMNS(* EXCLUDE ({{ lineage | join(', ') }})))::varchar)               as row_hash,
        md5(struct_pack(*COLUMNS(* EXCLUDE ({{ (lineage + line_columns) | join(', ') }})))::varchar) as claim_header_hash
    from src

),

typed as (

    select
        -- keys
        {{ cms_value('CLM_ID') }}                       as clm_id,
        {{ cms_int('CLM_LINE_NUM') }}                   as clm_line_num,
        {{ cms_value('BENE_ID') }}                      as bene_id,
        -- claim type and dates
        {{ cms_value('NCH_CLM_TYPE_CD') }}              as claim_type_code,
        {{ cms_value('CLM_FREQ_CD') }}                  as claim_frequency_code,
        {{ cms_date('CLM_FROM_DT') }}                   as from_date,
        {{ cms_date('CLM_THRU_DT') }}                   as thru_date,
        {{ cms_date('CLM_ADMSN_DT') }}                  as admission_date,
        {{ cms_date('NCH_BENE_DSCHRG_DT') }}            as discharge_date,
        {{ cms_date('NCH_WKLY_PROC_DT') }}              as processed_date,
        -- provider
        {{ cms_value('PRVDR_NUM') }}                    as provider_ccn,
        {{ cms_value('PRVDR_STATE_CD') }}               as provider_state_code,
        {{ cms_value('ORG_NPI_NUM') }}                  as organization_npi,
        {{ cms_value('AT_PHYSN_NPI') }}                 as attending_npi,
        -- admission, discharge, grouping
        {{ cms_value('CLM_IP_ADMSN_TYPE_CD') }}         as admission_type_code,
        {{ cms_value('CLM_SRC_IP_ADMSN_CD') }}          as admission_source_code,
        {{ cms_value('PTNT_DSCHRG_STUS_CD') }}          as discharge_status_code,
        {{ cms_value('CLM_DRG_CD') }}                   as drg_code,
        -- amounts
        {{ cms_amount('CLM_PMT_AMT') }}                 as payment_amount,
        {{ cms_amount('CLM_TOT_CHRG_AMT') }}            as total_charge_amount,
        {{ cms_amount('NCH_PRMRY_PYR_CLM_PD_AMT') }}    as primary_payer_paid_amount,
        {{ cms_amount('NCH_BENE_IP_DDCTBL_AMT') }}      as inpatient_deductible_amount,
        {{ cms_amount('NCH_BENE_PTA_COINSRNC_LBLTY_AM') }} as part_a_coinsurance_amount,
        {{ cms_int('CLM_UTLZTN_DAY_CNT') }}             as utilization_day_count,
        -- diagnoses and procedures (code-set status is decided in silver)
        {{ cms_value('ADMTG_DGNS_CD') }}                as admitting_dx_code,
        {{ cms_value('PRNCPAL_DGNS_CD') }}              as principal_dx_code,
        {{ cms_list('ICD_DGNS_CD', 25) }}               as dx_codes,
        {{ cms_list('CLM_POA_IND_SW', 25) }}            as dx_poa_indicators,
        [{{ cms_value('FST_DGNS_E_CD') }}] || {{ cms_list('ICD_DGNS_E_CD', 12) }} as external_cause_codes,
        {{ cms_list('ICD_PRCDR_CD', 25) }}              as px_codes,
        [{% for i in range(1, 26) %}{{ cms_date('PRCDR_DT' ~ i) }}{{ ', ' if not loop.last }}{% endfor %}] as px_dates,
        -- revenue center line
        {{ cms_value('REV_CNTR') }}                     as revenue_center_code,
        {{ cms_value('HCPCS_CD') }}                     as hcpcs_code,

        list_filter([
            case when {{ cms_value('CLM_ID') }} is null then 'CLM_ID: missing' end,
            case when {{ cms_value('BENE_ID') }} is null then 'BENE_ID: missing' end,
            case when {{ cms_int('CLM_LINE_NUM') }} is null or {{ cms_int('CLM_LINE_NUM') }} < 1
                 then 'CLM_LINE_NUM: missing or invalid' end,
            case when {{ cms_value('NCH_CLM_TYPE_CD') }} not in ('60', '61', '62', '63', '64')
                 then 'NCH_CLM_TYPE_CD: not an inpatient claim type' end,
            case when {{ cms_date('CLM_FROM_DT') }} is null then 'CLM_FROM_DT: missing or invalid' end,
            case when {{ cms_date('CLM_THRU_DT') }} is null then 'CLM_THRU_DT: missing or invalid' end,
            {{ parse_error('CLM_ADMSN_DT', 'date') }},
            {{ parse_error('NCH_BENE_DSCHRG_DT', 'date') }},
            {{ parse_error('CLM_PMT_AMT', 'amount') }},
            {{ parse_error('CLM_TOT_CHRG_AMT', 'amount') }},
            {{ parse_error('CLM_UTLZTN_DAY_CNT', 'int') }},
            case when {{ cms_date('CLM_THRU_DT') }} < {{ cms_date('CLM_FROM_DT') }}
                 then 'CLM_THRU_DT: before CLM_FROM_DT' end,
            case when {{ cms_date('NCH_BENE_DSCHRG_DT') }} < {{ cms_date('CLM_ADMSN_DT') }}
                 then 'NCH_BENE_DSCHRG_DT: before CLM_ADMSN_DT' end,
            case when {{ cms_date('CLM_ADMSN_DT') }} > {{ cms_date('CLM_FROM_DT') }}
                 then 'CLM_ADMSN_DT: after CLM_FROM_DT' end,
            case when {{ cms_amount('CLM_TOT_CHRG_AMT') }} < 0 then 'CLM_TOT_CHRG_AMT: negative' end,
            case when {{ cms_value('PRNCPAL_DGNS_CD') }} is null then 'PRNCPAL_DGNS_CD: missing' end
        ], lambda e: e is not null)                     as row_errors,

        row_hash,
        claim_header_hash,
        _arrival,
        _source_file,
        _source_sha256,
        _source_line,
        _loaded_at

    from hashed

),

versions as (

    select
        *,
        -- Newest arrival that carries this claim line ("latest wins" across arrivals).
        max({{ arrival_rank('_arrival') }}) over (partition by clm_id, clm_line_num)          as newest_arrival_rank,
        -- Distinct contents of this claim line inside its newest arrival.
        count(distinct row_hash) over (
            partition by clm_id, clm_line_num, {{ arrival_rank('_arrival') }}
        )                                                                                    as versions_in_arrival,
        row_number() over (
            partition by clm_id, clm_line_num, row_hash
            order by {{ arrival_rank('_arrival') }} desc, _source_line
        )                                                                                    as copy_rank
    from typed

),

classified as (

    select
        * exclude (newest_arrival_rank, versions_in_arrival, copy_rank),
        case
            when clm_id is null or clm_line_num is null              then 'kept'
            when {{ arrival_rank('_arrival') }} < newest_arrival_rank then 'superseded'
            when versions_in_arrival > 1                             then 'conflicting'
            when copy_rank > 1                                       then 'exact_duplicate'
            else 'kept'
        end as dedup_status
    from versions

),

with_line_errors as (

    select
        *,
        case
            when dedup_status = 'conflicting'
            then list_append(row_errors, 'CLM_ID/CLM_LINE_NUM: conflicting versions in one arrival')
            else row_errors
        end as line_errors
    from classified

),

claim_checks as (

    -- A claim is loaded whole or not at all: if any of its surviving lines is quarantined, or its lines
    -- disagree on claim-level fields, every line of the claim goes to quarantine.
    select
        *,
        bool_or(len(line_errors) > 0) over (partition by clm_id)            as claim_has_bad_line,
        count(distinct claim_header_hash) over (partition by clm_id)        as claim_header_versions
    from with_line_errors
    where dedup_status in ('kept', 'conflicting')

)

select
    * exclude (row_errors, line_errors, claim_has_bad_line, claim_header_versions),
    list_concat(line_errors, list_filter([
        case when claim_header_versions > 1 then 'claim-level fields differ between lines of this claim' end,
        case when claim_has_bad_line and len(line_errors) = 0 then 'another line of this claim is quarantined' end
    ], lambda e: e is not null)) as dq_errors
from claim_checks

union all by name

select
    * exclude (row_errors, line_errors),
    []::varchar[] as dq_errors
from with_line_errors
where dedup_status in ('superseded', 'exact_duplicate')
