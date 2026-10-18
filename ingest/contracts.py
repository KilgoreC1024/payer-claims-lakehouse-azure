"""Data contracts: validate a landed arrival before it is allowed into bronze.

The contract (contracts/*.yml) is the agreement about what a source must look like. Validation runs in
DuckDB directly against the landed file and produces a `ContractReport`: a list of named checks, each
with a status of pass / warn / fail and enough evidence (counts, sample values) to act on.

Severity model
  * fail  -> the arrival is blocked. Nothing is written to bronze; the file stays in landing.
  * warn  -> the arrival is loaded and the warning is recorded. Downstream, a row that breaks a rule is
             quarantined in silver, unless the column says `on_violation: flag`, in which case the row is
             kept, the bad value is nulled, and the problem is flagged (a member with an unknown dual
             status code is still enrolled, so dropping the row would undercount member-months).

The pattern (and most of this module) is shared with the sibling healthcare-cost-utilization-platform
project. Differences here: pipe-delimited RIF files, `dd-Mon-yyyy` dates, whitespace-only fields treated
as empty, repeating column groups (ICD_DGNS_CD1..25, MDCR_ENTLMT_BUYIN_IND_01..12), a fixed-width layout
for the CMS ICD-10 order files, and a configurable severity for duplicate grain.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import yaml

from ingest.settings import posix, sql_str

PASS, WARN, FAIL = "pass", "warn", "fail"
SAMPLE_LIMIT = 5
ORDER_FILE_COLUMNS = ["order_number", "code", "valid_flag"]


@dataclass
class Check:
    name: str
    status: str
    observed: Any = None
    expected: Any = None
    message: str = ""
    samples: list[Any] = field(default_factory=list)


@dataclass
class ContractReport:
    source: str
    arrival: str
    contract_version: int
    row_count: int | None
    checks: list[Check]

    @property
    def status(self) -> str:
        statuses = {c.status for c in self.checks}
        return FAIL if FAIL in statuses else WARN if WARN in statuses else PASS

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status
        return data

    def summary(self) -> str:
        bad = [c for c in self.checks if c.status != PASS]
        if not bad:
            return f"{self.source}[{self.arrival}]: contract PASS ({len(self.checks)} checks, {self.row_count} rows)"
        detail = "; ".join(f"{c.name}={c.status}: {c.message}" for c in bad)
        return f"{self.source}[{self.arrival}]: contract {self.status.upper()} - {detail}"


class ContractViolation(RuntimeError):
    def __init__(self, report: ContractReport):
        super().__init__(report.summary())
        self.report = report


# ---- loading ----------------------------------------------------------------------------------------


def load_contract(path: Path) -> dict[str, Any]:
    """Read a contract and expand its repeating column groups into ordinary column entries."""
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    columns: dict[str, dict[str, Any]] = dict(contract.get("columns") or {})
    for group in contract.get("column_groups") or []:
        pad = int(group.get("pad", 0))
        for i in range(int(group["from"]), int(group["to"]) + 1):
            columns[f"{group['prefix']}{str(i).zfill(pad)}"] = dict(group["spec"])
    contract["columns"] = columns
    return contract


# ---- scanning ---------------------------------------------------------------------------------------


def is_order_file(contract: dict[str, Any]) -> bool:
    return (contract.get("format") or {}).get("layout") == "cms_order_file"


def delimiter(contract: dict[str, Any]) -> str:
    return (contract.get("format") or {}).get("delimiter", ",")


def scan_sql(path: Path, contract: dict[str, Any]) -> str:
    """A DuckDB relation over the landed file, every column VARCHAR, plus `_source_line`.

    The dialect is pinned, never sniffed: a governed ingest must parse the file exactly as declared or
    fail loudly. RIF files have no text qualifier, so quoting is disabled. `_source_line` is the 1-based
    line number in the landed file (the header is line 1), so any bronze row can be traced to its line.
    """
    if is_order_file(contract):
        # Fixed width: order number (1-5), code (7-13), valid flag (15), then descriptions (not read).
        return f"""(
            select
                nullif(trim(substr(line, 1, 5)), '')  as order_number,
                nullif(trim(substr(line, 7, 7)), '')  as code,
                nullif(trim(substr(line, 15, 1)), '') as valid_flag,
                idx                                   as _source_line
            from (
                select rtrim(unnest(lines), chr(13)) as line, generate_subscripts(lines, 1) as idx
                from (select string_split(content, chr(10)) as lines from read_text({sql_str(posix(path))}))
            )
            where trim(line) <> ''
        )"""
    return f"""(
        select *, row_number() over () + 1 as _source_line
        from read_csv({sql_str(posix(path))}, header = true, all_varchar = true, strict_mode = true,
                      delim = {sql_str(delimiter(contract))}, quote = '', escape = '', skip = 0,
                      null_padding = false, hive_partitioning = false, auto_detect = true)
    )"""


def read_header(path: Path, contract: dict[str, Any]) -> list[str]:
    if is_order_file(contract):
        return list(ORDER_FILE_COLUMNS)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        first = handle.readline().rstrip("\r\n")
    return first.split(delimiter(contract)) if first else []


# ---- rules ------------------------------------------------------------------------------------------


def ident(column: str) -> str:
    return '"' + column.replace('"', '""') + '"'


def value_sql(column: str) -> str:
    """Whitespace-only fields mean "no value" (CMS writes most empty fields as a single space)."""
    return f"nullif(trim({ident(column)}), '')"


def _rules(column: str, spec: dict[str, Any], date_format: str) -> list[tuple[str, str]]:
    """Return (rule_name, SQL predicate that is TRUE when the value violates the rule)."""
    v = value_sql(column)
    present = f"{v} IS NOT NULL"
    rules: list[tuple[str, str]] = []
    if spec.get("nullable", True) is False:
        rules.append(("not_null", f"{v} IS NULL"))
    kind = spec.get("type", "string")
    if kind == "integer":
        rules.append(("type_integer", f"{present} AND TRY_CAST({v} AS BIGINT) IS NULL"))
    elif kind == "decimal":
        rules.append(
            (
                "type_decimal",
                f"{present} AND (TRY_CAST({v} AS DOUBLE) IS NULL OR NOT isfinite(TRY_CAST({v} AS DOUBLE)))",
            )
        )
    elif kind == "date":
        rules.append(("type_date", f"{present} AND try_strptime({v}, {sql_str(date_format)}) IS NULL"))
    if "min" in spec:
        rules.append(("min", f"{present} AND TRY_CAST({v} AS DOUBLE) < {float(spec['min'])}"))
    if "max" in spec:
        rules.append(("max", f"{present} AND TRY_CAST({v} AS DOUBLE) > {float(spec['max'])}"))
    if "accepted_values" in spec:
        allowed = ", ".join(sql_str(str(a)) for a in spec["accepted_values"])
        rules.append(("accepted_values", f"{present} AND {v} NOT IN ({allowed})"))
    if "pattern" in spec:
        rules.append(("pattern", f"{present} AND NOT regexp_full_match({v}, {sql_str(spec['pattern'])})"))
    return rules


def row_rules(contract: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every (column, rule, violation predicate) the contract defines."""
    date_format = (contract.get("format") or {}).get("date_format", "%Y-%m-%d")
    return [
        (column, rule, predicate)
        for column, spec in contract["columns"].items()
        for rule, predicate in _rules(column, spec, date_format)
    ]


# ---- validation -------------------------------------------------------------------------------------


def validate(
    path: Path,
    contract: dict[str, Any],
    arrival: str,
    previous_header: list[str] | None = None,
) -> ContractReport:
    source = contract["source"]
    policy = contract.get("policy") or {}
    columns: dict[str, dict[str, Any]] = contract["columns"]
    checks: list[Check] = []

    def report(row_count: int | None = None) -> ContractReport:
        return ContractReport(source, arrival, contract.get("contract_version", 1), row_count, checks)

    # ---- structure: header ------------------------------------------------------------------------
    header = read_header(path, contract)
    dupes = sorted({c for c in header if header.count(c) > 1})
    checks.append(
        Check("header.duplicate_columns", FAIL if dupes else PASS, observed=dupes, message=", ".join(dupes))
    )
    missing = [c for c in columns if c not in header]
    checks.append(
        Check(
            "header.required_columns",
            FAIL if missing else PASS,
            observed=missing,
            expected=f"{len(columns)} contracted columns",
            message=("missing: " + ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else ""))
            if missing
            else "",
        )
    )
    # Uncontracted columns are expected (the RIF has ~200; we consume a few dozen). What matters is
    # *change*: columns that appeared or disappeared since the last accepted arrival of this source.
    changes = drift(previous_header, header)
    drifted = bool(changes["added"] or changes["removed"])
    drift_status = PASS
    if drifted:
        drift_status = WARN if policy.get("unknown_columns", "warn") == "warn" else FAIL
    checks.append(
        Check(
            "header.drift_vs_previous_arrival",
            drift_status,
            observed=changes,
            message=(
                f"added: {', '.join(changes['added']) or '-'}; removed: {', '.join(changes['removed']) or '-'}"
                if drifted
                else ""
            ),
        )
    )
    if missing or dupes:
        return report()

    # ---- parse (schema-on-read, everything VARCHAR) ----------------------------------------------
    con = duckdb.connect()
    try:
        try:
            con.execute(f"CREATE TABLE src AS SELECT * FROM {scan_sql(path, contract)}")
        except duckdb.Error as exc:
            checks.append(Check("file.parse", FAIL, message=str(exc).splitlines()[0]))
            return report()
        parsed = [r[0] for r in con.execute("DESCRIBE src").fetchall() if r[0] != "_source_line"]
        if parsed != header:
            checks.append(
                Check(
                    "file.parse",
                    FAIL,
                    observed=parsed[:10],
                    expected=header[:10],
                    message="parsed columns do not match the header row",
                )
            )
            return report()
        checks.append(Check("file.parse", PASS))
        row_count = con.execute("SELECT count(*) FROM src").fetchone()[0]

        # ---- volume -------------------------------------------------------------------------------
        bounds = policy.get("expected_rows") or {}
        low, high = bounds.get("min", 1), bounds.get("max")
        in_range = row_count >= low and (high is None or row_count <= high)
        checks.append(
            Check(
                "volume.row_count",
                PASS if in_range else FAIL,
                observed=row_count,
                expected={"min": low, "max": high},
                message="" if in_range else f"{row_count} rows outside expected range",
            )
        )

        # ---- grain --------------------------------------------------------------------------------
        grain = ", ".join(value_sql(c) for c in contract["grain"])
        dup_rows = con.execute(
            f"SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) n FROM src GROUP BY {grain} HAVING n > 1)"
        ).fetchone()[0]
        dup_samples = con.execute(
            f"SELECT {grain} FROM src GROUP BY {grain} HAVING count(*) > 1 LIMIT {SAMPLE_LIMIT}"
        ).fetchall()
        severity = FAIL if policy.get("duplicate_grain", "fail") == "fail" else WARN
        checks.append(
            Check(
                "grain.unique",
                severity if dup_rows else PASS,
                observed=int(dup_rows),
                expected=f"unique on ({', '.join(contract['grain'])})",
                message=f"{dup_rows} repeated rows" if dup_rows else "",
                samples=[list(map(str, r)) for r in dup_samples],
            )
        )

        # ---- row-level value rules (error budget) ------------------------------------------------
        rules = row_rules(contract)
        if rules:
            counts_sql = ", ".join(f"count(*) FILTER (WHERE {p})" for _, _, p in rules)
            any_sql = " OR ".join(f"coalesce({p}, FALSE)" for _, _, p in rules)
            counts = con.execute(
                f"SELECT {counts_sql}, count(*) FILTER (WHERE {any_sql}) FROM src"
            ).fetchone()
            bad_rows = counts[-1]
            violations = []
            for (column, rule, predicate), n in zip(rules, counts[:-1], strict=True):
                if n:
                    samples = [
                        r[0]
                        for r in con.execute(
                            f"SELECT DISTINCT {ident(column)} FROM src WHERE {predicate} LIMIT {SAMPLE_LIMIT}"
                        ).fetchall()
                    ]
                    action = columns[column].get("on_violation", "quarantine")
                    violations.append(
                        {"column": column, "rule": rule, "rows": n, "action": action, "samples": samples}
                    )
            allowed, budget, floor = error_allowance(policy, row_count)
            rate = bad_rows / row_count if row_count else 0.0
            status = PASS if bad_rows == 0 else (WARN if bad_rows <= allowed else FAIL)
            checks.append(
                Check(
                    "values.row_error_budget",
                    status,
                    observed={"rows": bad_rows, "rate": round(rate, 6)},
                    expected=f"<= {allowed:g} rows (the larger of {budget:.2%} of rows and {floor} rows)",
                    message=(
                        ""
                        if status == PASS
                        else f"{bad_rows} rows ({rate:.3%}) violate value rules"
                        + (" - quarantined or flagged downstream" if status == WARN else " - over budget")
                    ),
                    samples=violations,
                )
            )
    finally:
        con.close()
    return report(row_count)


def error_allowance(policy: dict[str, Any], row_count: int) -> tuple[float, float, int]:
    """How many rows may break value rules: the larger of a rate and an absolute floor.

    A pure rate is too strict for small arrivals (one bad row in a 30-row file is 3%), and a pure count
    is too loose for large ones. `row_error_floor` is the small-batch tolerance; `row_error_budget`
    governs everything else.
    """
    budget = float(policy.get("row_error_budget", 0))
    floor = int(policy.get("row_error_floor", 0))
    return max(float(floor), budget * row_count), budget, floor


def drift(previous_header: list[str] | None, current_header: list[str]) -> dict[str, list[str]]:
    """Columns added or removed relative to the previously accepted arrival."""
    if previous_header is None:
        return {"added": [], "removed": []}
    prev, cur = set(previous_header), set(current_header)
    return {"added": sorted(cur - prev), "removed": sorted(prev - cur)}
