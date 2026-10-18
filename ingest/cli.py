"""Command line: `pcl <command>` (or `python -m ingest <command>`).

pcl fetch                         download the CMS files and ICD-10 code sets into <data-dir>/raw
pcl replay --profile fixture      cut raw files into yearly arrivals under <data-dir>/landing
pcl load [--through 2019]         contract-check and load pending arrivals into bronze, oldest first
pcl transform                     build the silver models with dbt and run their tests
pcl run --profile fixture         replay + load + transform, end to end
pcl status                        show the load log
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ingest.bronze import load_all, read_load_log
from ingest.contracts import ContractViolation
from ingest.landing import replay
from ingest.settings import Paths
from ingest.sources import LOAD_ORDER, arrival_sort_key
from ingest.transform import TransformError, build


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def raw_root(paths: Paths, profile: str) -> Path:
    """`fixture` reads the committed subset in tests/fixtures/raw; `full` reads what `pcl fetch` downloaded."""
    return paths.fixture if profile == "fixture" else paths.raw


def cmd_fetch(paths: Paths, args: argparse.Namespace) -> int:
    from ingest.fetch import fetch

    for entry in fetch(paths, only=args.only):
        state = "cached" if entry["skipped"] else "downloaded"
        print(f"{state:10} {entry['file']:45} {entry['bytes']:>12,} bytes  sha256 {entry['sha256'][:16]}")
    return 0


def cmd_replay(paths: Paths, args: argparse.Namespace) -> int:
    root = raw_root(paths, args.profile)
    landed = replay(root, paths.landing)
    if not landed:
        print(f"no raw files found under {root}", file=sys.stderr)
        return 1
    for item in landed:
        state = "landed" if item.written else "unchanged"
        print(
            f"{state:10} {item.source:16} arrival={item.arrival:8} {item.rows:>8,} rows  sha256 {item.sha256[:16]}"
        )
    return 0


def cmd_load(paths: Paths, args: argparse.Namespace) -> int:
    run_id = new_run_id()
    try:
        results = load_all(
            paths,
            run_id,
            sources=[args.source] if args.source else None,
            arrival=args.arrival,
            through=args.through,
        )
    except ContractViolation as exc:
        print(f"REJECTED  {exc}", file=sys.stderr)
        return 2
    for r in results:
        rows = f"{r.rows:>8,} rows" if r.rows is not None else " " * 13
        print(f"{r.status:8} {r.source:16} arrival={r.arrival:8} {rows}  contract={r.contract_status or '-'}")
    return 0


def cmd_transform(paths: Paths, args: argparse.Namespace) -> int:
    try:
        result = build(paths)
    except TransformError as exc:
        print(f"FAILED  {exc}", file=sys.stderr)
        return 3
    print(result.summary())
    for warning in result.warnings:
        print(f"  warn: {warning['unique_id'].split('.')[-1]} ({warning['failures']} rows)")
    print(f"warehouse: {paths.duckdb_path}")
    return 0


def cmd_run(paths: Paths, args: argparse.Namespace) -> int:
    for step in (cmd_replay, cmd_load, cmd_transform):
        print(f"== {step.__name__.removeprefix('cmd_')}")
        code = step(paths, args)
        if code:
            return code
    return 0


def cmd_status(paths: Paths, args: argparse.Namespace) -> int:
    log = read_load_log(paths)
    if not log:
        print("nothing loaded yet")
        return 0
    # Per arrival: the version currently in bronze (last loaded) and what the last attempt did.
    loaded, last = {}, {}
    for entry in log:
        key = (entry["source"], entry["arrival"])
        last[key] = entry
        if entry["status"] == "loaded":
            loaded[key] = entry
    for key in sorted(last, key=lambda k: (LOAD_ORDER.index(k[0]), arrival_sort_key(k[1]))):
        current = loaded.get(key)
        version = (
            f"{current['rows']:>8,} rows sha256 {current['sha256'][:12]} contract={current['contract_status']}"
            if current
            else "not in bronze".ljust(48)
        )
        print(f"{key[0]:16} arrival={key[1]:8} {version}  last attempt: {last[key]['status']}")
    print("attempts:", dict(Counter(e["status"] for e in log)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcl", description="Payer claims lakehouse pipeline")
    parser.add_argument("--data-dir", help="where the pipeline writes (default: ./data or $PCL_DATA_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download raw files from data.cms.gov and cms.gov")
    p.add_argument("--only", choices=["cms", "reference"])
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("replay", help="cut raw files into yearly arrivals in landing/")
    p.add_argument("--profile", choices=["fixture", "full"], default="full")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("load", help="contract-check and load landed arrivals into bronze")
    p.add_argument("--source", choices=LOAD_ORDER)
    p.add_argument("--arrival", help="load only this arrival (e.g. 2019)")
    p.add_argument("--through", help="load member and claim arrivals up to this year (simulates time)")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("transform", help="build silver with dbt and run its tests")
    p.set_defaults(func=cmd_transform)

    p = sub.add_parser("run", help="replay, load, and transform end to end")
    p.add_argument("--profile", choices=["fixture", "full"], default="full")
    p.add_argument("--through", help="load member and claim arrivals up to this year (simulates time)")
    p.set_defaults(func=cmd_run, source=None, arrival=None)

    p = sub.add_parser("status", help="show what has been loaded")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    paths = Paths.discover(args.data_dir)
    paths.ensure()
    return args.func(paths, args)


if __name__ == "__main__":
    raise SystemExit(main())
