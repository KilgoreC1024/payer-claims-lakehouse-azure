"""Download the raw files listed in config/sources.yml into <data-dir>/raw/ (the only network step).

Each file is streamed to a `.part` file, checked against the server's Content-Length, and renamed into
place; its URL, SHA-256, size, and HTTP validators are recorded in raw/_manifest.json. A file that is
already present with the recorded checksum is not downloaded again.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ingest.landing import sha256_file, write_json_atomic
from ingest.settings import Paths
from ingest.sources import SOURCES, load_config

MANIFEST = "_manifest.json"
CHUNK = 1 << 20


class FetchError(RuntimeError):
    pass


def planned_downloads(paths: Paths, only: str | None = None) -> list[tuple[str, Path]]:
    """(url, destination) for every file in config/sources.yml."""
    config = load_config(paths)
    plan: list[tuple[str, Path]] = []
    if only in (None, "cms"):
        cms = config["cms_synthetic"]
        folder = paths.raw / SOURCES["cms_beneficiary"].raw_subdir
        plan += [(f"{cms['base_url']}/{rel}", folder / name) for name, rel in cms["files"].items()]
    if only in (None, "reference"):
        for key, source in (("icd10cm", "ref_icd10cm"), ("icd10pcs", "ref_icd10pcs")):
            folder = paths.raw / SOURCES[source].raw_subdir
            for fiscal_year, url in config["reference"][key]["files"].items():
                plan.append((url, folder / f"{key}_{fiscal_year}.zip"))
    return plan


def fetch(
    paths: Paths, only: str | None = None, session: requests.Session | None = None
) -> list[dict[str, Any]]:
    manifest_path = paths.raw / MANIFEST
    manifest: dict[str, Any] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    )
    session = session or requests.Session()
    results = []
    for url, target in planned_downloads(paths, only):
        key = target.relative_to(paths.raw).as_posix()
        known = manifest.get(key)
        if known and target.is_file() and sha256_file(target) == known["sha256"]:
            results.append({**known, "skipped": True})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        try:
            http = _download(url, part, session)
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
        entry = {
            "file": key,
            "url": url,
            "sha256": sha256_file(target),
            "bytes": target.stat().st_size,
            "http": http,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        manifest[key] = entry
        write_json_atomic(manifest_path, manifest)
        results.append({**entry, "skipped": False})
    return results


def _download(url: str, part: Path, session: requests.Session) -> dict[str, Any]:
    with session.get(url, stream=True, timeout=(30, 300)) as response:
        if response.status_code != 200:
            raise FetchError(f"GET {url} returned HTTP {response.status_code}")
        expected = response.headers.get("Content-Length")
        written = 0
        with part.open("wb") as handle:
            for block in response.iter_content(CHUNK):
                handle.write(block)
                written += len(block)
        # Only enforce Content-Length when the body was not transparently decompressed.
        if expected and not response.headers.get("Content-Encoding") and int(expected) != written:
            raise FetchError(f"GET {url}: expected {expected} bytes, received {written} (truncated)")
        return {
            "status": response.status_code,
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "content_length": expected,
        }
