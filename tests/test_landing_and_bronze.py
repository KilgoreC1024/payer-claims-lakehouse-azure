from __future__ import annotations

import json
import zipfile
from pathlib import Path

import duckdb
import pytest

from ingest.bronze import load_all, read_load_log, version_dir
from ingest.contracts import ContractViolation
from ingest.landing import read_sidecar, replay, replay_inpatient, replay_reference
from ingest.settings import Paths, posix
from tests.conftest import beneficiary_file, inpatient_file


def raw_inpatient(raw_root: Path) -> Path:
    """Three claims: one processed in 2019, one December 2019 stay processed in January 2020, one in 2020."""
    return inpatient_file(
        raw_root / "cms_synthetic" / "inpatient.csv",
        [
            {"CLM_ID": "-1", "CLM_THRU_DT": "10-Jun-2019", "NCH_WKLY_PROC_DT": "14-Jun-2019"},
            {
                "CLM_ID": "-2",
                "CLM_LINE_NUM": "1",
                "CLM_THRU_DT": "30-Dec-2019",
                "NCH_WKLY_PROC_DT": "03-Jan-2020",
            },
            {
                "CLM_ID": "-2",
                "CLM_LINE_NUM": "2",
                "CLM_THRU_DT": "30-Dec-2019",
                "NCH_WKLY_PROC_DT": "03-Jan-2020",
            },
            {"CLM_ID": "-3", "CLM_THRU_DT": "02-Feb-2020", "NCH_WKLY_PROC_DT": "07-Feb-2020"},
        ],
    )


def landed_lines(paths: Paths, source: str, arrival: str, name: str) -> list[str]:
    return (paths.landing / source / f"arrival={arrival}" / name).read_text(encoding="utf-8").splitlines()


def bronze_rows(paths: Paths, source: str, query: str = "SELECT count(*)") -> list[tuple]:
    pattern = posix(paths.bronze / source) + "/*/*/data.parquet"
    return duckdb.sql(f"{query} FROM read_parquet('{pattern}', hive_partitioning = false)").fetchall()


def test_inpatient_replay_splits_by_processing_year_without_changing_lines(paths: Paths, tmp_path: Path):
    raw = raw_inpatient(tmp_path / "raw")
    landed = replay_inpatient(tmp_path / "raw", paths.landing)
    assert [(item.arrival, item.rows) for item in landed] == [("2019", 1), ("2020", 3)]

    raw_lines = raw.read_text(encoding="utf-8").splitlines()
    y2019 = landed_lines(paths, "cms_inpatient", "2019", "inpatient_2019.csv")
    y2020 = landed_lines(paths, "cms_inpatient", "2020", "inpatient_2020.csv")
    # Same header on every arrival; the December stay processed in January arrives with 2020.
    assert y2019[0] == y2020[0] == raw_lines[0]
    assert y2019[1:] == raw_lines[1:2]
    assert y2020[1:] == raw_lines[2:]
    sidecar = read_sidecar(paths.landing / "cms_inpatient" / "arrival=2020")
    assert sidecar["data_lines"] == 3
    assert sidecar["derived_from"]["split_on"] == "NCH_WKLY_PROC_DT year"


def test_replay_is_idempotent(paths: Paths, tmp_path: Path):
    raw_inpatient(tmp_path / "raw")
    assert all(item.written for item in replay(tmp_path / "raw", paths.landing))
    assert not any(item.written for item in replay(tmp_path / "raw", paths.landing))


def test_reference_zip_lands_only_the_order_file(paths: Paths, tmp_path: Path):
    folder = tmp_path / "raw" / "reference" / "icd10cm"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "icd10cm_2023.zip", "w") as archive:
        archive.writestr("Code Descriptions/icd10cm_order_2023.txt", "00001 A00     0 Cholera\r\n")
        archive.writestr("Code Descriptions/icd10cm_order_addenda_2023.txt", "Add: A001\r\n")
    [item] = replay_reference(tmp_path / "raw", paths.landing, "ref_icd10cm")
    assert (item.arrival, item.path.name) == ("2023", "icd10cm_order_2023.txt")
    assert item.path.read_bytes() == b"00001 A00     0 Cholera\r\n"


def test_load_is_ordered_lineage_stamped_and_idempotent(paths: Paths, tmp_path: Path):
    raw_inpatient(tmp_path / "raw")
    beneficiary_file(
        tmp_path / "raw" / "cms_synthetic" / "beneficiary_2019.csv", [{"BENE_ENROLLMT_REF_YR": "2019"}]
    )
    replay(tmp_path / "raw", paths.landing)

    first = load_all(paths, run_id="r1")
    assert [(r.source, r.arrival, r.status) for r in first] == [
        ("cms_beneficiary", "2019", "loaded"),
        ("cms_inpatient", "2019", "loaded"),
        ("cms_inpatient", "2020", "loaded"),
    ]
    sha = read_sidecar(paths.landing / "cms_inpatient" / "arrival=2020")["sha256"]
    lineage = bronze_rows(
        paths, "cms_inpatient", 'SELECT "CLM_ID", "CLM_LINE_NUM", _arrival, _source_line, _source_sha256'
    )
    assert sorted(lineage) == sorted(
        [
            ("-1", "1", "2019", 2, read_sidecar(paths.landing / "cms_inpatient" / "arrival=2019")["sha256"]),
            ("-2", "1", "2020", 2, sha),
            ("-2", "2", "2020", 3, sha),
            ("-3", "1", "2020", 4, sha),
        ]
    )

    second = load_all(paths, run_id="r2")
    assert {r.status for r in second} == {"skipped"}
    assert bronze_rows(paths, "cms_inpatient") == [(4,)]
    assert [e["status"] for e in read_load_log(paths)] == ["loaded"] * 3 + ["skipped"] * 3


def test_redelivered_arrival_becomes_a_new_version(paths: Paths, tmp_path: Path):
    raw = raw_inpatient(tmp_path / "raw")
    replay(tmp_path / "raw", paths.landing)
    load_all(paths, run_id="r1")
    old_sha = read_sidecar(paths.landing / "cms_inpatient" / "arrival=2020")["sha256"]

    # CMS re-issues the file with a corrected payment on claim -1, which was processed in 2019.
    corrected = raw.read_text(encoding="utf-8").replace("|8000.00|", "|7500.00|", 1)
    raw.write_text(corrected, encoding="utf-8", newline="")
    replay(tmp_path / "raw", paths.landing)
    results = {(r.arrival, r.status) for r in load_all(paths, run_id="r2", sources=["cms_inpatient"])}
    assert results == {("2019", "loaded"), ("2020", "skipped")}

    new_sha = read_sidecar(paths.landing / "cms_inpatient" / "arrival=2019")["sha256"]
    versions = sorted(p.name for p in (paths.bronze / "cms_inpatient" / "arrival=2019").iterdir())
    assert len(versions) == 2
    assert version_dir(paths, "cms_inpatient", "2019", new_sha).is_dir()
    assert version_dir(paths, "cms_inpatient", "2020", old_sha).is_dir()


def test_contract_failure_blocks_the_arrival(paths: Paths, tmp_path: Path):
    inpatient_file(
        tmp_path / "raw" / "cms_synthetic" / "inpatient.csv",
        [
            {"CLM_ID": f"-{i}", "CLM_FROM_DT": "not a date", "NCH_WKLY_PROC_DT": "14-Jun-2019"}
            for i in range(1, 6)
        ],
    )
    replay(tmp_path / "raw", paths.landing)
    with pytest.raises(ContractViolation) as exc:
        load_all(paths, run_id="r1")
    assert "over budget" in str(exc.value)
    assert not (paths.bronze / "cms_inpatient" / "arrival=2019").exists()
    [entry] = read_load_log(paths)
    assert entry["status"] == "rejected"
    report = json.loads(Path(entry["report"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    # The landed file is kept for inspection.
    assert (paths.landing / "cms_inpatient" / "arrival=2019" / "inpatient_2019.csv").is_file()


def test_landing_file_changed_in_place_is_refused(paths: Paths, tmp_path: Path):
    raw_inpatient(tmp_path / "raw")
    replay(tmp_path / "raw", paths.landing)
    landed = paths.landing / "cms_inpatient" / "arrival=2019" / "inpatient_2019.csv"
    landed.write_text(landed.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match its landing sidecar"):
        load_all(paths, run_id="r1", sources=["cms_inpatient"])
