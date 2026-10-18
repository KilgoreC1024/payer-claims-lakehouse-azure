"""Run dbt (silver models and their tests) against the bronze Parquet in a data directory.

dbt runs as a subprocess so its dependency tree can live in its own environment (set PCL_DBT_BIN to point
at that environment's dbt). Its target and log directories go under <data-dir>/warehouse/, so runs on
different data directories (tests, fixture, full) never share state or write into the repository.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from ingest.settings import Paths, posix


class TransformError(RuntimeError):
    pass


@dataclass
class DbtResult:
    command: list[str]
    returncode: int
    duration_s: float
    results: list[dict[str, Any]] = field(default_factory=list)
    stdout_tail: str = ""

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] in ("error", "fail")]

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] == "warn"]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        detail = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
        return f"dbt {' '.join(self.command[1:2])}: {detail or 'no results'} in {self.duration_s:.1f}s"


def dbt_executable() -> str:
    configured = os.environ.get("PCL_DBT_BIN")
    if configured:
        return configured
    scripts = Path(sys.executable).parent
    for name in ("dbt.exe", "dbt"):
        if (scripts / name).is_file():
            return str(scripts / name)
    found = shutil.which("dbt")
    if not found:
        raise TransformError("dbt not found: install the 'transform' extra or set PCL_DBT_BIN")
    return found


def run_dbt(paths: Paths, args: list[str]) -> DbtResult:
    target_dir = paths.warehouse / "dbt_target"
    log_dir = paths.warehouse / "dbt_logs"
    paths.warehouse.mkdir(parents=True, exist_ok=True)
    command = [
        dbt_executable(),
        *args,
        "--project-dir",
        str(paths.dbt_project),
        "--profiles-dir",
        str(paths.dbt_project),
        "--target-path",
        str(target_dir),
        "--log-path",
        str(log_dir),
    ]
    env = {
        **os.environ,
        "PCL_DUCKDB_PATH": posix(paths.duckdb_path),
        "PCL_BRONZE_DIR": posix(paths.bronze),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    started = perf_counter()
    proc = subprocess.run(command, cwd=paths.dbt_project, env=env, capture_output=True, text=True)
    results: list[dict[str, Any]] = []
    run_results = target_dir / "run_results.json"
    if run_results.is_file():
        payload = json.loads(run_results.read_text(encoding="utf-8"))
        results = [
            {
                "unique_id": r["unique_id"],
                "status": r["status"],
                "failures": r.get("failures"),
                "message": r.get("message"),
            }
            for r in payload.get("results", [])
        ]
    return DbtResult(
        command=[Path(command[0]).name, *command[1:]],
        returncode=proc.returncode,
        duration_s=perf_counter() - started,
        results=results,
        stdout_tail="\n".join((proc.stdout + proc.stderr).splitlines()[-40:]),
    )


def build(paths: Paths) -> DbtResult:
    """`dbt build`: every model, then the tests that guard it. Raises when anything errors or fails."""
    result = run_dbt(paths, ["build"])
    if result.returncode != 0 or result.failures:
        failed = ", ".join(r["unique_id"].split(".")[-1] for r in result.failures) or "see dbt output"
        raise TransformError(f"dbt build failed ({failed})\n{result.stdout_tail}")
    return result
