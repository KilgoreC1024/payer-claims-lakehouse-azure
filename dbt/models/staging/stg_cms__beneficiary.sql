{#-
    Typed member-years (one row per member per reference year) from the newest version of every yearly
    arrival, with the twelve monthly indicators kept as lists:

      entitlement_by_month       MDCR_ENTLMT_BUYIN_IND_01..12   0 none, 1 A, 2 B, 3 A+B, A/B/C with buy-in
      hmo_by_month               HMO_IND_01..12                 blank in this release
      part_c_contract_by_month   PTC_CNTRCT_ID_01..12
      part_d_contract_by_month   PTD_CNTRCT_ID_01..12
      dual_status_by_month       DUAL_STUS_CD_01..12            invalid codes nulled (see dq_flags)

      dq_errors   problems that make member-months uncomputable -> silver_quarantine
      dq_flags    problems that are corrected (value nulled) without dropping the member-year
-#}

{%- set dual_codes = ["NA", "00", "01", "02", "03", "04", "05", "06", "08", "09", "10", "99"] -%}
{%- set entitlement_codes = ["0", "1", "2", "3", "A", "B", "C"] -%}

with src as (

    {{ latest_arrival_versions(source('bronze', 'cms_beneficiary')) }}

),

typed as (

    select
        {{ cms_value('BENE_ID') }}                          as bene_id,
        {{ cms_int('BENE_ENROLLMT_REF_YR') }}               as ref_year,
        {{ cms_date('BENE_BIRTH_DT') }}                     as birth_date,
        {{ cms_date('BENE_DEATH_DT') }}                     as death_date,
        {{ cms_value('SEX_IDENT_CD') }}                     as sex_code,
        {{ cms_value('BENE_RACE_CD') }}                     as race_code,
        {{ cms_value('STATE_CODE') }}                       as state_code,
        {{ cms_int('AGE_AT_END_REF_YR') }}                  as age_at_end_of_year,
        {{ cms_value('ENTLMT_RSN_ORIG') }}                  as original_entitlement_reason_code,
        {{ cms_value('ENTLMT_RSN_CURR') }}                  as current_entitlement_reason_code,
        {{ cms_value('ESRD_IND') }} = 'Y'                   as has_esrd,
        {{ parse_error('BENE_DEATH_DT', 'date') }}          as death_date_error,
        -- annual counts as reported by CMS (reconciled against the monthly flags in tests/)
        {{ cms_int('BENE_HI_CVRAGE_TOT_MONS') }}            as reported_part_a_months,
        {{ cms_int('BENE_SMI_CVRAGE_TOT_MONS') }}           as reported_part_b_months,
        {{ cms_int('BENE_STATE_BUYIN_TOT_MONS') }}          as reported_buyin_months,
        {{ cms_int('BENE_HMO_CVRAGE_TOT_MONS') }}           as reported_hmo_months,
        {{ cms_int('PTD_PLAN_CVRG_MONS') }}                 as reported_part_d_months,
        {{ cms_int('DUAL_ELGBL_MONS') }}                    as reported_dual_months,
        -- monthly indicators
        {{ cms_list('MDCR_ENTLMT_BUYIN_IND_', 12, pad=2) }} as entitlement_by_month,
        {{ cms_list('HMO_IND_', 12, pad=2) }}               as hmo_by_month,
        {{ cms_list('PTC_CNTRCT_ID_', 12, pad=2) }}         as part_c_contract_by_month,
        {{ cms_list('PTD_CNTRCT_ID_', 12, pad=2) }}         as part_d_contract_by_month,
        {{ cms_list('DUAL_STUS_CD_', 12, pad=2) }}          as dual_status_raw_by_month,
        _arrival,
        _source_file,
        _source_sha256,
        _source_line,
        _loaded_at
    from src

),

checked as (

    select
        * exclude (dual_status_raw_by_month, death_date_error),
        list_transform(
            dual_status_raw_by_month,
            lambda d: case when d in ('{{ dual_codes | join("', '") }}') then d end
        )                                                   as dual_status_by_month,
        list_filter([
            case when bene_id is null then 'BENE_ID: missing' end,
            case when ref_year is null then 'BENE_ENROLLMT_REF_YR: missing or invalid' end,
            case when ref_year <> try_cast(_arrival as integer)
                 then 'BENE_ENROLLMT_REF_YR: does not match the file year' end,
            case when birth_date is null then 'BENE_BIRTH_DT: missing or invalid' end,
            death_date_error,
            case when death_date < birth_date then 'BENE_DEATH_DT: before BENE_BIRTH_DT' end,
            case when len(list_filter(entitlement_by_month,
                                      lambda e: e is not null and e not in ('{{ entitlement_codes | join("', '") }}'))) > 0
                 then 'MDCR_ENTLMT_BUYIN_IND: invalid code' end
        ], lambda e: e is not null)                         as row_errors,
        list_filter([
            case when len(list_filter(dual_status_raw_by_month,
                                      lambda d: d is not null and d not in ('{{ dual_codes | join("', '") }}'))) > 0
                 then 'DUAL_STUS_CD: invalid code nulled' end
        ], lambda e: e is not null)                         as dq_flags
    from typed

)

select
    * exclude (row_errors),
    case
        when count(*) over (partition by bene_id, ref_year) > 1
        then list_append(row_errors, 'duplicate member-year across arrivals')
        else row_errors
    end as dq_errors
from checked
