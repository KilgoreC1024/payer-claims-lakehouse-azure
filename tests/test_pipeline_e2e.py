"""End to end on the committed fixture: replay -> contract-gated bronze -> dbt silver and audits.

Each fault-injection test copies the fixture into its own data directory as `raw/`, breaks it the way a
real feed breaks, and runs the same CLI a person would. dbt runs as a subprocess, so these take a while.
"""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from ingest.bronze import read_load_log
from ingest.cli import main
from ingest.settings import Paths
from tests.conftest import PROJECT_ROOT

pytestmark = pytest.mark.e2e

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "raw"


def run(data_dir: Path, *args: str) -> int:
    return main(["--data-dir", str(data_dir), *args])


def query(data_dir: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(data_dir / "warehouse" / "lakehouse.duckdb"), read_only=True)
    try:
        return con.sql(sql).fetchall()
    finally:
        con.close()


def scalar(data_dir: Path, sql: str):
    return query(data_dir, sql)[0][0]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A data directory whose raw/ is a private, editable copy of the fixture."""
    shutil.copytree(FIXTURE, tmp_path / "raw")
    return tmp_path


# ---- helpers to edit the raw inpatient file like a broken feed would ---------------------------------


def inpatient_lines(data_dir: Path) -> tuple[list[str], list[list[str]]]:
    lines = (data_dir / "raw" / "cms_synthetic" / "inpatient.csv").read_text(encoding="utf-8").splitlines()
    return lines[0].split("|"), [line.split("|") for line in lines[1:]]


def write_inpatient(data_dir: Path, header: list[str], rows: list[list[str]]) -> None:
    text = "\n".join("|".join(r) for r in [header, *rows]) + "\n"
    (data_dir / "raw" / "cms_synthetic" / "inpatient.csv").write_text(text, encoding="utf-8", newline="")


def single_line_claims(header: list[str], rows: list[list[str]], processed_year: str) -> list[int]:
    """Row indexes of claims that have exactly one line and were processed in `processed_year`."""
    clm, proc = header.index("CLM_ID"), header.index("NCH_WKLY_PROC_DT")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r[clm]] = counts.get(r[clm], 0) + 1
    return [i for i, r in enumerate(rows) if counts[r[clm]] == 1 and r[proc].endswith(processed_year)]


# ---- the happy path -----------------------------------------------------------------------------------


def test_fixture_runs_end_to_end(tmp_path: Path):
    assert run(tmp_path, "run", "--profile", "fixture") == 0

    assert scalar(tmp_path, "select count(*) from silver.silver_inpatient_claims") == 39
    assert scalar(tmp_path, "select count(*) from silver.silver_inpatient_claim_lines") == 66
    assert scalar(tmp_path, "select count(*) from silver.silver_members") == 31
    assert scalar(tmp_path, "select count(*) from silver.silver_quarantine") == 0

    # A December stay processed in January arrives with the next year's batch.
    [(thru, arrival)] = query(
        tmp_path,
        "select thru_date, _arrival from silver.silver_inpatient_claims where year(thru_date) <> cast(_arrival as int)",
    )
    assert (thru.year, arrival) == (2017, "2018")

    # Every code-set defect the fixture was chosen for is detected, and none of them quarantines a claim.
    dx = {r[0] for r in query(tmp_path, "select distinct code_status from silver.silver_inpatient_diagnoses")}
    px = {
        r[0] for r in query(tmp_path, "select distinct code_status from silver.silver_inpatient_procedures")
    }
    hcpcs = {
        r[0] for r in query(tmp_path, "select distinct hcpcs_format from silver.silver_inpatient_claim_lines")
    }
    assert {"valid", "truncated", "header_not_billable", "not_valid_in_fiscal_year"} <= dx
    assert {"valid", "diagnosis_code_in_procedure_field", "header_not_billable"} <= px
    assert {"cpt_category_i", "hcpcs_level_ii", "modifier_in_code_field", "invalid_format"} <= hcpcs
    assert (
        scalar(
            tmp_path, "select count(*) from silver.silver_inpatient_claims where coded_before_icd10_effective"
        )
        == 2
    )

    # Member-months: exact from the monthly flags; the invalid dual status is nulled, not dropped.
    assert scalar(tmp_path, "select count(*) from silver.silver_member_months") == 2949
    assert query(
        tmp_path,
        "select dual_status_code from silver.silver_member_months "
        "where bene_id = '-10000010254824' and year = 2022 group by all",
    ) == [(None,)]


def test_arrivals_replay_the_passage_of_time(data_dir: Path):
    assert run(data_dir, "run", "--profile", "full", "--through", "2018") == 0
    early_claims = scalar(data_dir, "select count(*) from silver.silver_inpatient_claims")
    assert early_claims == 2 + 3 + 3 + 3  # claims processed 2015-2018
    assert scalar(data_dir, "select max(year) from silver.silver_member_months") == 2018

    assert run(data_dir, "run", "--profile", "full") == 0
    assert scalar(data_dir, "select count(*) from silver.silver_inpatient_claims") == 39
    statuses = [
        (e["source"], e["arrival"], e["status"]) for e in read_load_log(Paths(PROJECT_ROOT, data_dir))
    ]
    # The second run skips every arrival it already had and loads only the new years.
    assert ("cms_inpatient", "2018", "skipped") in statuses
    assert ("cms_inpatient", "2019", "loaded") in statuses
    assert statuses.count(("cms_inpatient", "2018", "loaded")) == 1


# ---- fault injection ----------------------------------------------------------------------------------


def test_corrected_redelivery_replaces_the_arrival(data_dir: Path):
    assert run(data_dir, "run", "--profile", "full") == 0
    header, rows = inpatient_lines(data_dir)
    target = rows[single_line_claims(header, rows, "2019")[0]]
    clm_id = target[header.index("CLM_ID")]
    target[header.index("CLM_PMT_AMT")] = "12345.67"
    write_inpatient(data_dir, header, rows)

    assert run(data_dir, "run", "--profile", "full") == 0
    assert scalar(
        data_dir, f"select payment_amount from silver.silver_inpatient_claims where clm_id = '{clm_id}'"
    ) == Decimal("12345.67")
    assert scalar(data_dir, "select count(*) from silver.silver_inpatient_claim_lines") == 66
    versions = list((data_dir / "bronze" / "cms_inpatient" / "arrival=2019").iterdir())
    assert len(versions) == 2  # bronze keeps the original and the correction


def test_exact_duplicates_collapse_and_conflicts_are_quarantined(data_dir: Path):
    header, rows = inpatient_lines(data_dir)
    exact, conflicting = (rows[i] for i in single_line_claims(header, rows, "2021")[:2])
    changed = list(conflicting)
    changed[header.index("HCPCS_CD")] = "99999"
    write_inpatient(data_dir, header, [*rows, list(exact), changed])

    assert run(data_dir, "run", "--profile", "full") == 0
    clm = header.index("CLM_ID")
    assert (
        scalar(data_dir, f"select count(*) from silver.silver_inpatient_claims where clm_id = '{exact[clm]}'")
        == 1
    )
    assert (
        scalar(
            data_dir,
            f"select count(*) from silver.silver_inpatient_claims where clm_id = '{conflicting[clm]}'",
        )
        == 0
    )
    quarantined = query(data_dir, "select natural_key, dq_errors from silver.silver_quarantine")
    assert len(quarantined) == 2
    assert all("conflicting versions in one arrival" in " ".join(errors) for _, errors in quarantined)
    assert scalar(data_dir, "select count(*) from silver.silver_inpatient_claim_lines") == 65


def test_contract_failure_stops_the_pipeline_before_dbt(data_dir: Path):
    assert run(data_dir, "run", "--profile", "full") == 0
    before = scalar(data_dir, "select count(*) from silver.silver_inpatient_claims")

    header, rows = inpatient_lines(data_dir)
    proc, from_dt = header.index("NCH_WKLY_PROC_DT"), header.index("CLM_FROM_DT")
    for r in rows:
        if r[proc].endswith("2020"):
            r[from_dt] = "2020-01-01"  # ISO dates in a dd-Mon-yyyy feed: the whole batch is malformed
    write_inpatient(data_dir, header, rows)

    assert run(data_dir, "run", "--profile", "full") == 2
    last = read_load_log(Paths(PROJECT_ROOT, data_dir))[-1]
    assert (last["source"], last["arrival"], last["status"]) == ("cms_inpatient", "2020", "rejected")
    assert len(list((data_dir / "bronze" / "cms_inpatient" / "arrival=2020").iterdir())) == 1
    assert scalar(data_dir, "select count(*) from silver.silver_inpatient_claims") == before


def test_quarantine_over_budget_fails_the_build(data_dir: Path):
    header, rows = inpatient_lines(data_dir)
    thru = header.index("CLM_THRU_DT")
    targets = [i for year in ("2020", "2021") for i in single_line_claims(header, rows, year)][:5]
    for i in targets:
        rows[i][thru] = "01-Jan-2010"  # passes the contract (a valid date) but ends before it starts
    write_inpatient(data_dir, header, rows)

    assert run(data_dir, "replay", "--profile", "full") == 0
    assert run(data_dir, "load") == 0
    assert run(data_dir, "transform") == 3
    assert scalar(data_dir, "select count(*) from silver.silver_quarantine") == 5


def test_partial_year_enrollment_and_death_shape_member_months(data_dir: Path):
    """The published files never have these, so they are injected: April enrollment, February death."""
    folder = data_dir / "raw" / "cms_synthetic"

    def edit(year: str, **values: str) -> str:
        path = folder / f"beneficiary_{year}.csv"
        lines = path.read_text(encoding="utf-8").splitlines()
        header = lines[0].split("|")
        later = {
            line.split("|")[0]
            for line in (folder / "beneficiary_2025.csv").read_text(encoding="utf-8").splitlines()[1:]
        }
        index = next(i for i, line in enumerate(lines[1:], 1) if line.split("|")[0] in later)
        row = lines[index].split("|")
        for column, value in values.items():
            row[header.index(column)] = value
        lines[index] = "|".join(row)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
        return row[0]

    # Enrolled from April 2019: no entitlement January-March, so CMS would report 9 Part A months.
    bene = edit(
        "2019",
        MDCR_ENTLMT_BUYIN_IND_01="0",
        MDCR_ENTLMT_BUYIN_IND_02="0",
        MDCR_ENTLMT_BUYIN_IND_03="0",
        BENE_HI_CVRAGE_TOT_MONS="9",
        BENE_SMI_CVRAGE_TOT_MONS="9",
    )
    # Died 2025-02-15: alive on February 1, not on March 1, so 2 months even though the flag says 3.
    assert (
        edit("2025", BENE_DEATH_DT="15-Feb-2025", BENE_HI_CVRAGE_TOT_MONS="2", BENE_SMI_CVRAGE_TOT_MONS="2")
        == bene
    )

    assert run(data_dir, "run", "--profile", "full") == 0  # Part A reconciliation still passes
    months = query(
        data_dir,
        f"select year, count(*), min(month) from silver.silver_member_months where bene_id = '{bene}' "
        "and year in (2019, 2025) group by year order by year",
    )
    assert months == [(2019, 9, 4), (2025, 2, 1)]
