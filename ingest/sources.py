"""The four inputs of milestone 1 and how each is cut into arrivals.

An *arrival* is the unit of incremental loading: one batch of a source that shows up at one time.
  * cms_beneficiary  one arrival per published yearly file (beneficiary_2015.csv -> arrival 2015)
  * cms_inpatient    CMS publishes one file for 2015-2023; it is replayed as one arrival per year of the
                     CMS weekly processing date (NCH_WKLY_PROC_DT), so a claim for a December stay that was
                     processed in January arrives with the next year's batch, as real run-out does
  * ref_icd10cm/pcs  one arrival per fiscal year of the ICD-10 code set
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ingest.settings import Paths


@dataclass(frozen=True)
class Source:
    name: str
    contract: str  # file name under contracts/
    raw_subdir: str  # where its raw files live under the raw root
    landing_file: str  # landing file name, formatted with {arrival}
    description: str


SOURCES: dict[str, Source] = {
    s.name: s
    for s in (
        Source(
            "ref_icd10cm",
            "ref_icd10cm.yml",
            "reference/icd10cm",
            "icd10cm_order_{arrival}.txt",
            "ICD-10-CM codes with billable flag, by fiscal year",
        ),
        Source(
            "ref_icd10pcs",
            "ref_icd10pcs.yml",
            "reference/icd10pcs",
            "icd10pcs_order_{arrival}.txt",
            "ICD-10-PCS codes with valid flag, by fiscal year",
        ),
        Source(
            "cms_beneficiary",
            "cms_beneficiary.yml",
            "cms_synthetic",
            "beneficiary_{arrival}.csv",
            "Master Beneficiary Summary File, one file per reference year",
        ),
        Source(
            "cms_inpatient",
            "cms_inpatient.yml",
            "cms_synthetic",
            "inpatient_{arrival}.csv",
            "Inpatient claim lines, replayed by CMS processing year",
        ),
    )
}

# Reference data first (claims are validated against it), then members, then claims.
LOAD_ORDER = ["ref_icd10cm", "ref_icd10pcs", "cms_beneficiary", "cms_inpatient"]

# Inpatient rows whose processing and through dates are both unreadable still land, in a final batch,
# so the contract (not the replay) decides what happens to them.
UNDATED = "undated"


def arrival_sort_key(arrival: str) -> tuple[int, str]:
    return (1, arrival) if arrival == UNDATED else (0, arrival)


def load_config(paths: Paths) -> dict[str, Any]:
    return yaml.safe_load(paths.sources_config.read_text(encoding="utf-8"))


def contract_path(paths: Paths, source: Source) -> Path:
    return paths.contracts / source.contract
