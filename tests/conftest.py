from __future__ import annotations

from pathlib import Path

import pytest

from ingest.contracts import load_contract
from ingest.settings import Paths

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = PROJECT_ROOT / "contracts"

# A minimal valid value for every contracted inpatient column that is required or patterned; every other
# column is written as a single space, the way CMS writes empty fields.
INPATIENT_DEFAULTS = {
    "BENE_ID": "-10000000000001",
    "CLM_ID": "-10000900000001",
    "CLM_LINE_NUM": "1",
    "NCH_CLM_TYPE_CD": "60",
    "NCH_NEAR_LINE_REC_IDENT_CD": "V",
    "CLM_FREQ_CD": "1",
    "CLM_FROM_DT": "03-Mar-2020",
    "CLM_THRU_DT": "06-Mar-2020",
    "NCH_WKLY_PROC_DT": "13-Mar-2020",
    "CLM_ADMSN_DT": "03-Mar-2020",
    "NCH_BENE_DSCHRG_DT": "06-Mar-2020",
    "PRVDR_NUM": "010001",
    "PRVDR_STATE_CD": "01",
    "ORG_NPI_NUM": "1234567893",
    "AT_PHYSN_NPI": "9999999991",
    "CLM_IP_ADMSN_TYPE_CD": "1",
    "CLM_SRC_IP_ADMSN_CD": "1",
    "PTNT_DSCHRG_STUS_CD": "1",
    "CLM_DRG_CD": "291",
    "CLM_PMT_AMT": "8000.00",
    "CLM_TOT_CHRG_AMT": "8000.00",
    "NCH_PRMRY_PYR_CLM_PD_AMT": "0",
    "NCH_BENE_IP_DDCTBL_AMT": "0.00",
    "NCH_BENE_PTA_COINSRNC_LBLTY_AM": "0.00",
    "CLM_UTLZTN_DAY_CNT": "3",
    "PRNCPAL_DGNS_CD": "I5023",
    "ICD_DGNS_CD1": "I5023",
    "CLM_POA_IND_SW1": "Y",
    "REV_CNTR": "0120",
    "HCPCS_CD": "99221",
}

BENEFICIARY_DEFAULTS = {
    "BENE_ID": "-10000000000001",
    "BENE_ENROLLMT_REF_YR": "2020",
    "BENE_BIRTH_DT": "16-Aug-1950",
    "SEX_IDENT_CD": "2",
    "BENE_RACE_CD": "1",
    "STATE_CODE": "01",
    "AGE_AT_END_REF_YR": "70",
    "ENTLMT_RSN_ORIG": "0",
    "ENTLMT_RSN_CURR": "0",
    "ESRD_IND": "0",
    "BENE_HI_CVRAGE_TOT_MONS": "12",
    "BENE_SMI_CVRAGE_TOT_MONS": "12",
    "BENE_STATE_BUYIN_TOT_MONS": "0",
    "BENE_HMO_CVRAGE_TOT_MONS": "0",
    "PTD_PLAN_CVRG_MONS": "0",
    "DUAL_ELGBL_MONS": "0",
    **{f"MDCR_ENTLMT_BUYIN_IND_{m:02d}": "3" for m in range(1, 13)},
    **{f"DUAL_STUS_CD_{m:02d}": "NA" for m in range(1, 13)},
}


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    """Project code from the repository, data written to a throwaway directory."""
    paths = Paths(project_root=PROJECT_ROOT, data_root=tmp_path / "data")
    paths.ensure()
    return paths


def contract(name: str) -> dict:
    return load_contract(CONTRACTS / f"{name}.yml")


def header_for(name: str, extra: tuple[str, ...] = ("CLAIM_QUERY_CODE",)) -> list[str]:
    """Every contracted column plus a few uncontracted ones, as in the real ~200-column files."""
    return [*contract(name)["columns"], *extra]


def make_row(header: list[str], defaults: dict[str, str], **overrides: str) -> list[str]:
    values = {**defaults, **overrides}
    return [values.get(column, " ") for column in header]


def write_pipe(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["|".join(header), *("|".join(r) for r in rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return path


def inpatient_file(path: Path, rows: list[dict[str, str]]) -> Path:
    header = header_for("cms_inpatient")
    return write_pipe(path, header, [make_row(header, INPATIENT_DEFAULTS, **r) for r in rows])


def beneficiary_file(path: Path, rows: list[dict[str, str]]) -> Path:
    header = header_for("cms_beneficiary", extra=("COUNTY_CD", "ZIP_CD"))
    return write_pipe(path, header, [make_row(header, BENEFICIARY_DEFAULTS, **r) for r in rows])
