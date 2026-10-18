from __future__ import annotations

import json

import pytest

from ingest.fetch import MANIFEST, FetchError, fetch, planned_downloads
from ingest.settings import Paths


class FakeResponse:
    def __init__(self, body: bytes, length: int | None = None, status: int = 200):
        self.status_code = status
        self.body = body
        self.headers = {"Content-Length": str(len(body) if length is None else length), "ETag": '"abc"'}

    def iter_content(self, chunk: int):
        yield from (self.body[i : i + chunk] for i in range(0, len(self.body), chunk))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Serves a small body for every URL; can be told to truncate one."""

    def __init__(self, truncate: str | None = None):
        self.truncate = truncate
        self.calls: list[str] = []

    def get(self, url: str, stream: bool, timeout):
        self.calls.append(url)
        body = f"content of {url}".encode()
        return FakeResponse(body, length=len(body) + 10 if url == self.truncate else None)


def test_plan_covers_every_configured_file(paths: Paths):
    plan = planned_downloads(paths)
    names = {dest.relative_to(paths.raw).as_posix() for _, dest in plan}
    assert len(plan) == 28
    assert "cms_synthetic/inpatient.csv" in names
    assert "cms_synthetic/beneficiary_2025.csv" in names
    assert "reference/icd10cm/icd10cm_2016.zip" in names
    assert "reference/icd10pcs/icd10pcs_2023.zip" in names


def test_fetch_records_checksums_and_skips_unchanged_files(paths: Paths):
    session = FakeSession()
    first = fetch(paths, only="cms", session=session)
    assert len(first) == 12 and not any(e["skipped"] for e in first)
    manifest = json.loads((paths.raw / MANIFEST).read_text(encoding="utf-8"))
    assert manifest["cms_synthetic/inpatient.csv"]["sha256"] == first[-1]["sha256"]

    second = fetch(paths, only="cms", session=session)
    assert all(e["skipped"] for e in second)
    assert len(session.calls) == 12  # nothing downloaded twice


def test_truncated_download_is_rejected_and_leaves_nothing(paths: Paths):
    url, dest = next(p for p in planned_downloads(paths, only="cms") if p[1].name == "inpatient.csv")
    with pytest.raises(FetchError, match="truncated"):
        fetch(paths, only="cms", session=FakeSession(truncate=url))
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()
