"""Landing: replay raw files into yearly arrivals, byte for byte, each with a checksum sidecar.

    raw/cms_synthetic/beneficiary_2019.csv  -> landing/cms_beneficiary/arrival=2019/beneficiary_2019.csv
    raw/cms_synthetic/inpatient.csv         -> landing/cms_inpatient/arrival=2019/inpatient_2019.csv, ...
    raw/reference/icd10cm/<any>.zip         -> landing/ref_icd10cm/arrival=2023/icd10cm_order_2023.txt

Guarantees
  * Lossless: landing files contain the raw lines unchanged (same header, same bytes per line). The
    inpatient split only decides which file a line goes to, so the union of the arrivals is the raw file.
  * Idempotent: a landing file whose content is unchanged is not rewritten.
  * Atomic: each file is written to a temporary name and renamed into place when complete.
  * Traceable: every landing file has a `_landing.json` sidecar with its SHA-256, size, line count, and
    the raw file (and its SHA-256) it was derived from.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest.sources import SOURCES, UNDATED, Source, arrival_sort_key

SIDECAR = "_landing.json"
CHUNK = 1 << 20
DATE_RE = re.compile(rb"^\s*\d{2}-[A-Za-z]{3}-(\d{4})\s*$")
ORDER_MEMBER = {
    "ref_icd10cm": re.compile(r"icd10cm_order_(\d{4})\.txt$"),
    "ref_icd10pcs": re.compile(r"icd10pcs_order_(\d{4})\.txt$"),
}


class LandingError(RuntimeError):
    pass


@dataclass
class Landed:
    source: str
    arrival: str
    path: Path
    sha256: str
    rows: int
    written: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def landing_dir(landing_root: Path, source: str, arrival: str) -> Path:
    return landing_root / source / f"arrival={arrival}"


def read_sidecar(directory: Path) -> dict[str, Any] | None:
    path = directory / SIDECAR
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def list_arrivals(landing_root: Path, source: str) -> list[str]:
    root = landing_root / source
    if not root.is_dir():
        return []
    found = [d.name.split("=", 1)[1] for d in root.iterdir() if d.is_dir() and d.name.startswith("arrival=")]
    return sorted(found, key=arrival_sort_key)


def landing_file(landing_root: Path, source: Source, arrival: str) -> Path:
    return landing_dir(landing_root, source.name, arrival) / source.landing_file.format(arrival=arrival)


def _land_bytes(
    landing_root: Path, source: Source, arrival: str, data: bytes, derived_from: dict[str, Any]
) -> Landed:
    target_dir = landing_dir(landing_root, source.name, arrival)
    target = target_dir / source.landing_file.format(arrival=arrival)
    digest = sha256_bytes(data)
    rows = max(data.count(b"\n") - (0 if source.name.startswith("ref_") else 1), 0)
    existing = read_sidecar(target_dir)
    if existing and target.is_file() and existing["sha256"] == digest and sha256_file(target) == digest:
        return Landed(source.name, arrival, target, digest, rows, written=False)
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    write_json_atomic(
        target_dir / SIDECAR,
        {
            "source": source.name,
            "arrival": arrival,
            "file": target.name,
            "sha256": digest,
            "bytes": len(data),
            "data_lines": rows,
            "derived_from": derived_from,
            "landed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    return Landed(source.name, arrival, target, digest, rows, written=True)


# ---- per-source replays -----------------------------------------------------------------------------


def replay_beneficiary(raw_root: Path, landing_root: Path) -> list[Landed]:
    source = SOURCES["cms_beneficiary"]
    results = []
    for raw in sorted((raw_root / source.raw_subdir).glob("beneficiary_*.csv")):
        match = re.fullmatch(r"beneficiary_(\d{4})\.csv", raw.name)
        if not match:
            continue
        data = raw.read_bytes()
        derived = {"raw_file": raw.name, "raw_sha256": sha256_bytes(data)}
        results.append(_land_bytes(landing_root, source, match.group(1), data, derived))
    return results


def _arrival_year(fields: list[bytes], *indexes: int) -> str:
    for index in indexes:
        if index < len(fields):
            match = DATE_RE.match(fields[index])
            if match:
                return match.group(1).decode()
    return UNDATED


def replay_inpatient(raw_root: Path, landing_root: Path, delimiter: bytes = b"|") -> list[Landed]:
    """Split inpatient.csv into one arrival per CMS processing year, keeping every line unchanged."""
    source = SOURCES["cms_inpatient"]
    raw = raw_root / source.raw_subdir / "inpatient.csv"
    if not raw.is_file():
        return []
    data = raw.read_bytes()
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if not lines:
        raise LandingError(f"{raw}: empty file")
    header = lines[0]
    columns = [c.decode().strip() for c in header.rstrip(b"\r").split(delimiter)]
    try:
        proc, thru = columns.index("NCH_WKLY_PROC_DT"), columns.index("CLM_THRU_DT")
    except ValueError as exc:
        raise LandingError(f"{raw}: cannot replay without NCH_WKLY_PROC_DT and CLM_THRU_DT") from exc
    batches: dict[str, list[bytes]] = defaultdict(list)
    for line in lines[1:]:
        batches[_arrival_year(line.split(delimiter), proc, thru)].append(line)
    derived = {"raw_file": raw.name, "raw_sha256": sha256_bytes(data), "split_on": "NCH_WKLY_PROC_DT year"}
    return [
        _land_bytes(landing_root, source, arrival, b"\n".join([header, *batches[arrival]]) + b"\n", derived)
        for arrival in sorted(batches, key=arrival_sort_key)
    ]


def replay_reference(raw_root: Path, landing_root: Path, source_name: str) -> list[Landed]:
    """Extract the order file for each fiscal year from a CMS zip (or take a plain .txt as is)."""
    source = SOURCES[source_name]
    pattern = ORDER_MEMBER[source_name]
    results = []
    folder = raw_root / source.raw_subdir
    for raw in sorted(folder.glob("*")) if folder.is_dir() else []:
        if raw.suffix.lower() == ".zip":
            with zipfile.ZipFile(raw) as archive:
                members = [n for n in archive.namelist() if pattern.search(n)]
                if len(members) != 1:
                    raise LandingError(f"{raw.name}: expected one order file, found {members}")
                member, data = members[0], archive.read(members[0])
        elif pattern.search(raw.name):
            member, data = raw.name, raw.read_bytes()
        else:
            continue
        fiscal_year = pattern.search(member).group(1)
        derived = {"raw_file": raw.name, "raw_sha256": sha256_file(raw), "member": member}
        results.append(_land_bytes(landing_root, source, fiscal_year, data, derived))
    return results


def replay(raw_root: Path, landing_root: Path) -> list[Landed]:
    """Replay every raw file under `raw_root` into arrivals under `landing_root`."""
    return [
        *replay_reference(raw_root, landing_root, "ref_icd10cm"),
        *replay_reference(raw_root, landing_root, "ref_icd10pcs"),
        *replay_beneficiary(raw_root, landing_root),
        *replay_inpatient(raw_root, landing_root),
    ]
