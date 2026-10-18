"""Filesystem layout and environment-driven configuration.

Two roots are kept apart on purpose:
  * the *project root* holds code and configuration (config/, contracts/, dbt/, tests/fixtures/), and
  * the *data root* holds everything the pipeline writes (raw/, landing/, bronze/, warehouse/).

Tests point the data root at a temporary directory, so they can never touch a developer's real data.
The layout under the data root mirrors the ADLS Gen2 containers planned for the Azure milestone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _find_project_root() -> Path:
    env = os.environ.get("PCL_HOME")
    if env:
        return Path(env).resolve()
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "config" / "sources.yml").is_file():
            return candidate
    # Fall back to the repository layout: ingest/settings.py -> repo root.
    return Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Paths:
    project_root: Path
    data_root: Path

    @classmethod
    def discover(cls, data_root: str | Path | None = None) -> Paths:
        project = _find_project_root()
        data = Path(data_root or os.environ.get("PCL_DATA_DIR") or project / "data").resolve()
        return cls(project_root=project, data_root=data)

    # --- code and configuration ---
    @property
    def sources_config(self) -> Path:
        return self.project_root / "config" / "sources.yml"

    @property
    def contracts(self) -> Path:
        return self.project_root / "contracts"

    @property
    def dbt_project(self) -> Path:
        return self.project_root / "dbt"

    @property
    def fixture(self) -> Path:
        return self.project_root / "tests" / "fixtures" / "raw"

    # --- data (written by the pipeline) ---
    @property
    def raw(self) -> Path:
        """Files exactly as published (downloaded by `pcl fetch` or copied from the fixture)."""
        return self.data_root / "raw"

    @property
    def landing(self) -> Path:
        """Raw files replayed into yearly arrivals: landing/<source>/arrival=<year>/<file>."""
        return self.data_root / "landing"

    @property
    def bronze(self) -> Path:
        """Immutable Parquet per accepted arrival: bronze/<source>/arrival=<year>/sha256=<prefix>/."""
        return self.data_root / "bronze"

    @property
    def load_log(self) -> Path:
        """Append-only record of every load attempt (loaded, skipped, rejected)."""
        return self.bronze / "_load_log.jsonl"

    @property
    def warehouse(self) -> Path:
        return self.data_root / "warehouse"

    @property
    def duckdb_path(self) -> Path:
        return self.warehouse / "lakehouse.duckdb"

    @property
    def site(self) -> Path:
        """The static dashboard generated from the warehouse (`pcl dashboard`), published to GitHub Pages."""
        return self.data_root / "site"

    def ensure(self) -> None:
        for path in (self.raw, self.landing, self.bronze, self.warehouse):
            path.mkdir(parents=True, exist_ok=True)


def posix(path: Path) -> str:
    """Forward-slash path for SQL literals and dbt env vars (DuckDB accepts these on Windows too)."""
    return path.resolve().as_posix()


def sql_str(value: str) -> str:
    """Quote a value as a SQL string literal."""
    return "'" + value.replace("'", "''") + "'"
