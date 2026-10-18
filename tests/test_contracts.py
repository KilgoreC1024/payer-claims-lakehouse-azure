from __future__ import annotations

from pathlib import Path

import duckdb

from ingest.contracts import FAIL, PASS, WARN, scan_sql, validate
from tests.conftest import (
    INPATIENT_DEFAULTS,
    beneficiary_file,
    contract,
    header_for,
    inpatient_file,
    make_row,
    write_pipe,
)


def check(report, name):
    return next(c for c in report.checks if c.name == name)


def claim_lines(n: int) -> list[dict[str, str]]:
    return [{"CLM_ID": f"-1000090000{i:04d}", "CLM_LINE_NUM": "1"} for i in range(n)]


def test_column_groups_expand_to_numbered_columns():
    inpatient = contract("cms_inpatient")["columns"]
    members = contract("cms_beneficiary")["columns"]
    assert {"ICD_DGNS_CD1", "ICD_DGNS_CD25", "PRCDR_DT25"} <= set(inpatient)
    assert {"MDCR_ENTLMT_BUYIN_IND_01", "MDCR_ENTLMT_BUYIN_IND_12", "DUAL_STUS_CD_07"} <= set(members)
    assert members["DUAL_STUS_CD_07"]["on_violation"] == "flag"


def test_valid_arrival_passes(tmp_path: Path):
    path = inpatient_file(tmp_path / "inpatient.csv", claim_lines(3))
    report = validate(path, contract("cms_inpatient"), "2020")
    assert report.status == PASS, report.summary()
    assert report.row_count == 3


def test_whitespace_only_fields_are_empty(tmp_path: Path):
    # A single space in an optional column is fine; in a required column it is a missing value.
    rows = claim_lines(400)
    rows[0]["CLM_ADMSN_DT"] = " "
    rows[1]["BENE_ID"] = " "
    report = validate(inpatient_file(tmp_path / "i.csv", rows), contract("cms_inpatient"), "2020")
    budget = check(report, "values.row_error_budget")
    assert budget.status == WARN
    assert budget.observed["rows"] == 1
    assert [v["column"] for v in budget.samples] == ["BENE_ID"]


def test_bad_dates_within_budget_warn_and_over_budget_fail(tmp_path: Path):
    rows = claim_lines(400)
    rows[5]["CLM_THRU_DT"] = "2020-03-06"  # ISO instead of dd-Mon-yyyy
    report = validate(inpatient_file(tmp_path / "a.csv", rows), contract("cms_inpatient"), "2020")
    assert check(report, "values.row_error_budget").status == WARN

    for row in rows[:10]:
        row["CLM_THRU_DT"] = "31-Feb-2020"
    report = validate(inpatient_file(tmp_path / "b.csv", rows), contract("cms_inpatient"), "2020")
    assert report.status == FAIL
    assert "over budget" in check(report, "values.row_error_budget").message


def test_missing_contracted_column_blocks(tmp_path: Path):
    header = [c for c in header_for("cms_inpatient") if c != "CLM_THRU_DT"]
    path = write_pipe(tmp_path / "i.csv", header, [make_row(header, INPATIENT_DEFAULTS)])
    report = validate(path, contract("cms_inpatient"), "2020")
    assert report.status == FAIL
    assert check(report, "header.required_columns").observed == ["CLM_THRU_DT"]


def test_ragged_row_fails_parse(tmp_path: Path):
    path = inpatient_file(tmp_path / "i.csv", claim_lines(2))
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write("-1|-2|too|few|fields\n")
    report = validate(path, contract("cms_inpatient"), "2020")
    assert check(report, "file.parse").status == FAIL


def test_duplicate_grain_warns_for_claims_and_fails_for_members(tmp_path: Path):
    lines = claim_lines(3) + [claim_lines(1)[0]]
    claims = validate(inpatient_file(tmp_path / "i.csv", lines), contract("cms_inpatient"), "2020")
    assert check(claims, "grain.unique").status == WARN
    assert check(claims, "grain.unique").observed == 1

    members = validate(beneficiary_file(tmp_path / "b.csv", [{}, {}]), contract("cms_beneficiary"), "2020")
    assert check(members, "grain.unique").status == FAIL


def test_flag_rules_are_reported_with_their_action(tmp_path: Path):
    rows = [{"BENE_ID": f"-{i}"} for i in range(1, 400)]
    rows[0]["DUAL_STUS_CD_03"] = "AA"
    report = validate(beneficiary_file(tmp_path / "b.csv", rows), contract("cms_beneficiary"), "2020")
    violation = check(report, "values.row_error_budget").samples[0]
    assert violation["column"] == "DUAL_STUS_CD_03"
    assert violation["action"] == "flag"


def test_drift_against_previous_arrival_warns(tmp_path: Path):
    path = inpatient_file(tmp_path / "i.csv", claim_lines(1))
    previous = [*header_for("cms_inpatient"), "RETIRED_COLUMN"]
    report = validate(path, contract("cms_inpatient"), "2021", previous_header=previous)
    drift = check(report, "header.drift_vs_previous_arrival")
    assert drift.status == WARN
    assert drift.observed == {"added": [], "removed": ["RETIRED_COLUMN"]}


def test_source_line_points_at_the_file_line(tmp_path: Path):
    rows = claim_lines(50)
    path = inpatient_file(tmp_path / "i.csv", rows)
    lines = path.read_text(encoding="utf-8").splitlines()
    got = duckdb.sql(
        f'SELECT "CLM_ID", _source_line FROM {scan_sql(path, contract("cms_inpatient"))}'
    ).fetchall()
    assert all(lines[line - 1].split("|")[1] == clm for clm, line in got)


def test_order_file_layout(tmp_path: Path):
    path = tmp_path / "icd10cm_order_2023.txt"
    path.write_text(
        "00001 A00     0 \r\n00002 A000    1 \r\n00003 S134XXA 1 \r\n", encoding="ascii", newline=""
    )
    report = validate(path, contract("ref_icd10cm"), "2023")
    assert report.status == PASS, report.summary()
    rows = duckdb.sql(f"SELECT code, valid_flag FROM {scan_sql(path, contract('ref_icd10cm'))}").fetchall()
    assert rows == [("A00", "0"), ("A000", "1"), ("S134XXA", "1")]

    path.write_text("00001 A00     0 \n00002 A00     1 \n", encoding="ascii", newline="")
    assert validate(path, contract("ref_icd10cm"), "2023").status == FAIL


def test_small_arrivals_get_an_absolute_error_floor(tmp_path: Path):
    # 1 bad row in 30 is 3.3%, far over the 0.5% rate, but within the 3-row floor for small batches.
    rows = claim_lines(30)
    rows[0]["CLM_THRU_DT"] = "31-Feb-2020"
    report = validate(inpatient_file(tmp_path / "a.csv", rows), contract("cms_inpatient"), "2020")
    assert check(report, "values.row_error_budget").status == WARN

    for row in rows[:4]:
        row["CLM_THRU_DT"] = "31-Feb-2020"
    report = validate(inpatient_file(tmp_path / "b.csv", rows), contract("cms_inpatient"), "2020")
    assert check(report, "values.row_error_budget").status == FAIL
