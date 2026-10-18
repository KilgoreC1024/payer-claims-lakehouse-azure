"""Static dashboard: one self-contained HTML page generated from the gold marts and the pipeline's records.

The page embeds its data (the yearly marts, code conformance, and a data-health summary from the load log
and dbt's run results), so it can be hosted anywhere static (GitHub Pages) or opened from disk. Chart code
lives in `templates/dashboard.html`; this module only gathers and serializes data. Nothing that names a
local path is embedded.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

import duckdb

from ingest.bronze import read_load_log
from ingest.settings import Paths
from ingest.sources import LOAD_ORDER, arrival_sort_key

REPO_URL = "https://github.com/KilgoreC1024/payer-claims-lakehouse-azure"
PLACEHOLDER = "/*__PCL_DATA__*/"

# Statuses that count as conforming: a billable code for the claim's fiscal year, or a well-formed HCPCS
# code (CPT Category I shape or HCPCS Level II; no CPT descriptions are stored).
CONFORMING = {"valid", "cpt_category_i", "hcpcs_level_ii"}


def build(paths: Paths, out_dir: Path | None = None) -> Path:
    site = out_dir or paths.site
    con = duckdb.connect(str(paths.duckdb_path), read_only=True)
    try:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repo_url": REPO_URL,
            # Published next to the page by the Pages workflow, which generates dbt docs first.
            "dbt_docs": (site / "dbt-docs" / "index.html").is_file(),
            "totals": _totals(con),
            "years": _years(con),
            "exclusions": _exclusions(con),
            "conformance": _conformance(con),
            "quarantine": dict(
                con.sql("select source, count(*) from silver.silver_quarantine group by source").fetchall()
            ),
        }
    finally:
        con.close()
    payload["health"] = _health(paths)

    template = resources.files("ingest").joinpath("templates/dashboard.html").read_text(encoding="utf-8")
    # Embedded as a JSON data block; "</" is escaped so no value can close the <script> element.
    data = json.dumps(payload, separators=(",", ":"), default=_json_default).replace("</", "<\\/")
    site.mkdir(parents=True, exist_ok=True)
    out = site / "index.html"
    out.write_text(template.replace(PLACEHOLDER, data), encoding="utf-8")
    return out


def _json_default(value: Any) -> Any:
    # DuckDB returns DECIMAL as Decimal and dates as date; the page only needs numbers and ISO strings.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return float(value)


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    rel = con.sql(sql)
    return [dict(zip(rel.columns, row, strict=True)) for row in rel.fetchall()]


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    return con.sql(sql).fetchone()[0]


def _totals(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return {
        "members": _scalar(con, "select count(*) from silver.silver_members"),
        "member_months": _scalar(con, "select count(*) from silver.silver_member_months"),
        "denominator_member_months": _scalar(
            con, "select coalesce(sum(member_months), 0) from gold.gold_inpatient_pmpm_by_year"
        ),
        "stays": _scalar(con, "select count(*) from silver.silver_inpatient_claims"),
        "claim_lines": _scalar(con, "select count(*) from silver.silver_inpatient_claim_lines"),
        "diagnoses": _scalar(con, "select count(*) from silver.silver_inpatient_diagnoses"),
        "procedures": _scalar(con, "select count(*) from silver.silver_inpatient_procedures"),
        "claims_window": con.sql(
            "select min(month_start), max(month_start) from silver.silver_member_months where in_claims_window"
        ).fetchone(),
    }


def _years(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return _rows(
        con,
        """
        select
            p.year,
            p.member_months,
            p.member_years,
            p.inpatient_paid_amount,
            p.inpatient_pmpm,
            p.stays_outside_denominator,
            p.paid_amount_outside_denominator,
            u.inpatient_admits,
            u.inpatient_days,
            u.admits_per_1000,
            u.days_per_1000,
            u.average_length_of_stay,
            r.index_stays,
            r.readmissions,
            r.readmission_rate
        from gold.gold_inpatient_pmpm_by_year as p
        join gold.gold_inpatient_utilization_by_year as u using (year)
        left join gold.gold_readmission_30d_by_year as r using (year)
        order by p.year
        """,
    )


def _exclusions(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rel = con.sql("select * from gold.gold_readmission_30d_by_year")
    columns = [c for c in rel.columns if c.startswith("excluded_")]
    totals = con.sql(
        "select "
        + ", ".join(f"coalesce(sum({c}), 0)" for c in columns)
        + " from gold.gold_readmission_30d_by_year"
    ).fetchone()
    return [{"reason": c.removeprefix("excluded_"), "stays": n} for c, n in zip(columns, totals, strict=True)]


def _conformance(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = _rows(
        con,
        """
        select code_field, role, status, sum(code_count) as codes,
               list_distinct(flatten(list(example_codes)))[1:5] as examples
        from silver.silver_dq_code_conformance
        group by all
        order by code_field, role, codes desc
        """,
    )
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        g = groups.setdefault(
            (r["code_field"], r["role"]), {"field": r["code_field"], "role": r["role"], "statuses": []}
        )
        g["statuses"].append({"status": r["status"], "codes": r["codes"], "examples": r["examples"]})
    for g in groups.values():
        total = sum(s["codes"] for s in g["statuses"])
        g["codes"] = total
        g["conforming_share"] = sum(s["codes"] for s in g["statuses"] if s["status"] in CONFORMING) / total
        for s in g["statuses"]:
            s["share"] = s["codes"] / total
    return list(groups.values())


def _health(paths: Paths) -> dict[str, Any]:
    log = read_load_log(paths)
    # Per arrival: the version currently in bronze (the last one loaded), as `pcl status` reports it.
    current: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in log:
        if entry["status"] == "loaded":
            current[(entry["source"], entry["arrival"])] = entry
    arrivals = [
        {
            "source": source,
            "arrival": arrival,
            "rows": e.get("rows"),
            "contract_status": e.get("contract_status"),
            # The contract summary, without the "source[arrival]: " prefix the table already shows.
            "message": (e.get("message") or "").split(": ", 1)[-1],
            "sha256": (e.get("sha256") or "")[:12],
            "run_id": e.get("run_id"),
        }
        for (source, arrival), e in sorted(
            current.items(), key=lambda kv: (LOAD_ORDER.index(kv[0][0]), arrival_sort_key(kv[0][1]))
        )
    ]
    return {
        "arrivals": arrivals,
        "attempts": dict(Counter(e["status"] for e in log)),
        "last_run_id": log[-1].get("run_id") if log else None,
        "dbt": _dbt(paths),
    }


def _dbt(paths: Paths) -> dict[str, Any]:
    target = paths.warehouse / "dbt_target"
    run_results = target / "run_results.json"
    if not run_results.is_file():
        return {}
    payload = json.loads(run_results.read_text(encoding="utf-8"))
    manifest_path = target / "manifest.json"
    nodes = (
        json.loads(manifest_path.read_text(encoding="utf-8")).get("nodes", {})
        if manifest_path.is_file()
        else {}
    )
    counts: Counter[tuple[str, str]] = Counter()
    flagged = []
    for r in payload.get("results", []):
        kind = r["unique_id"].split(".")[0]
        counts[(kind, r["status"])] += 1
        if r["status"] in ("warn", "fail", "error"):
            flagged.append(
                {
                    "name": r["unique_id"].split(".")[-1],
                    "status": r["status"],
                    "rows": r.get("failures"),
                    "about": _leading_comment(nodes.get(r["unique_id"], {}).get("raw_code", "")),
                }
            )
    return {
        "generated_at": payload.get("metadata", {}).get("generated_at"),
        "elapsed_s": payload.get("elapsed_time"),
        "counts": [{"kind": k, "status": s, "n": n} for (k, s), n in sorted(counts.items())],
        "flagged": flagged,
    }


def _leading_comment(sql: str) -> str:
    """First sentence of a test's leading `--` comment block: what the test looks for."""
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped.startswith("--"):
            break
        lines.append(stripped.removeprefix("--").strip())
    text = " ".join(lines)
    match = re.match(r"(.+?\.)(\s|$)", text)
    return match.group(1) if match else text
