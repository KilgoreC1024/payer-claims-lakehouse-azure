"""Bronze: an immutable, columnar, lineage-stamped copy of every accepted arrival.

    landing/cms_inpatient/arrival=2019/inpatient_2019.csv
        -> contract check (contracts/cms_inpatient.yml)
        -> bronze/cms_inpatient/arrival=2019/sha256=<first 16>/data.parquet

Bronze is *schema-on-read*: every source column is kept as VARCHAR exactly as published, so silver logic
can change without re-ingesting. Each row gains lineage columns that trace it to the landed file and line.

Guarantees
  * Gated: an arrival that fails its contract is not written; the file stays in landing for inspection.
  * Idempotent: an arrival whose content (SHA-256) is already in bronze is skipped.
  * Versioned: if an arrival is re-delivered with different content (a corrected file), it is written
    as a new version next to the old one. Silver reads the newest version of each arrival.
  * Audited: every attempt (loaded, skipped, rejected) is appended to bronze/_load_log.jsonl, and every
    contract report is kept under bronze/_reports/.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from ingest.contracts import (
    FAIL,
    ContractReport,
    ContractViolation,
    load_contract,
    read_header,
    scan_sql,
    validate,
)
from ingest.landing import landing_file, list_arrivals, read_sidecar, sha256_file, write_json_atomic
from ingest.settings import Paths, posix, sql_str
from ingest.sources import LOAD_ORDER, SOURCES, Source, contract_path

BRONZE_FILE = "data.parquet"
SIDECAR = "_bronze.json"


@dataclass
class LoadResult:
    source: str
    arrival: str
    status: str  # loaded | skipped | rejected
    rows: int | None
    sha256: str
    contract_status: str | None = None
    message: str = ""


def version_dir(paths: Paths, source: str, arrival: str, sha256: str) -> Path:
    return paths.bronze / source / f"arrival={arrival}" / f"sha256={sha256[:16]}"


def report_path(paths: Paths, source: str, arrival: str, sha256: str) -> Path:
    return paths.bronze / "_reports" / source / f"arrival={arrival}-sha256={sha256[:16]}.json"


def read_load_log(paths: Paths) -> list[dict[str, Any]]:
    if not paths.load_log.is_file():
        return []
    return [json.loads(line) for line in paths.load_log.read_text(encoding="utf-8").splitlines() if line]


def _log(paths: Paths, entry: dict[str, Any]) -> None:
    paths.load_log.parent.mkdir(parents=True, exist_ok=True)
    with paths.load_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def previous_header(paths: Paths, source: Source, arrival: str) -> list[str] | None:
    """Header of the most recently loaded *other* arrival of this source, for drift detection."""
    loaded = [e for e in read_load_log(paths) if e["source"] == source.name and e["status"] == "loaded"]
    for entry in reversed(loaded):
        if entry["arrival"] != arrival:
            return entry.get("header")
    return None


def load_arrival(paths: Paths, source: Source, arrival: str, run_id: str) -> LoadResult:
    landed = landing_file(paths.landing, source, arrival)
    if not landed.is_file():
        raise FileNotFoundError(f"{source.name}: no landing file for arrival {arrival} ({landed})")
    digest = sha256_file(landed)
    sidecar = read_sidecar(landed.parent)
    if sidecar and sidecar["sha256"] != digest:
        # The landing file changed after it was landed (edited or corrupted in place): refuse it.
        raise RuntimeError(f"{landed}: checksum {digest[:16]} does not match its landing sidecar")

    target_dir = version_dir(paths, source.name, arrival, digest)
    if (target_dir / SIDECAR).is_file() and (target_dir / BRONZE_FILE).is_file():
        result = LoadResult(
            source.name, arrival, "skipped", None, digest, message="content already in bronze"
        )
        _log(paths, {**result.__dict__, "run_id": run_id, "at": _now()})
        return result

    contract = load_contract(contract_path(paths, source))
    header = read_header(landed, contract)
    report = validate(landed, contract, arrival, previous_header(paths, source, arrival))
    reports = report_path(paths, source.name, arrival, digest)
    reports.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(reports, report.to_dict())

    if report.status == FAIL:
        result = LoadResult(
            source.name, arrival, "rejected", report.row_count, digest, FAIL, report.summary()
        )
        _log(paths, {**result.__dict__, "run_id": run_id, "at": _now(), "report": str(reports)})
        raise ContractViolation(report)

    rows = _write_parquet(paths, source, arrival, landed, contract, digest, report, run_id)
    result = LoadResult(source.name, arrival, "loaded", rows, digest, report.status, report.summary())
    _log(paths, {**result.__dict__, "run_id": run_id, "at": _now(), "header": header, "report": str(reports)})
    return result


def _write_parquet(
    paths: Paths,
    source: Source,
    arrival: str,
    landed: Path,
    contract: dict[str, Any],
    digest: str,
    report: ContractReport,
    run_id: str,
) -> int:
    target_dir = version_dir(paths, source.name, arrival, digest)
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp = target_dir / f".{BRONZE_FILE}.{run_id}.tmp"
    loaded_at = _now()
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            COPY (
                SELECT
                    * EXCLUDE (_source_line),
                    {sql_str(source.name)}            AS _source,
                    {sql_str(arrival)}                AS _arrival,
                    {sql_str(landed.name)}            AS _source_file,
                    {sql_str(digest)}                 AS _source_sha256,
                    _source_line,
                    TIMESTAMP {sql_str(loaded_at)}    AS _loaded_at,
                    {sql_str(report.status)}          AS _contract_status
                FROM {scan_sql(landed, contract)}
            ) TO {sql_str(posix(tmp))} (FORMAT parquet, COMPRESSION zstd)
            """
        )
        rows = con.execute(f"SELECT count(*) FROM read_parquet({sql_str(posix(tmp))})").fetchone()[0]
    finally:
        con.close()
    if rows != report.row_count:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{source.name}[{arrival}]: wrote {rows} rows but the contract counted {report.row_count}"
        )
    os.replace(tmp, target_dir / BRONZE_FILE)
    write_json_atomic(
        target_dir / SIDECAR,
        {
            "source": source.name,
            "arrival": arrival,
            "source_file": landed.name,
            "source_sha256": digest,
            "rows": rows,
            "bytes": (target_dir / BRONZE_FILE).stat().st_size,
            "contract_status": report.status,
            "loaded_at": loaded_at,
            "loaded_by_run": run_id,
        },
    )
    return rows


def load_all(
    paths: Paths, run_id: str, sources: list[str] | None = None, arrival: str | None = None
) -> list[LoadResult]:
    """Load every landed arrival in order: reference data, then members, then claims, oldest year first.

    A contract failure stops the run (ContractViolation): later arrivals of a source must not load on top
    of a gap, and nothing downstream should run on a partially loaded lakehouse.
    """
    results = []
    for name in sources or LOAD_ORDER:
        source = SOURCES[name]
        for found in list_arrivals(paths.landing, name):
            if arrival is None or found == arrival:
                results.append(load_arrival(paths, source, found, run_id))
    return results
