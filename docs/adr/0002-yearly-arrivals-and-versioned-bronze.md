# ADR 0002: Replay the source as yearly arrivals into versioned bronze

**Status:** Accepted · **Date:** 2026-10-18

## Context

CMS publishes the synthetic claims as one inpatient file covering 2015-2023 and one enrollment file per
year. Loading everything at once would hide the problems a real feed creates: late claims, corrected
re-deliveries, a bad batch in the middle of a history, and schema drift between years.

## Decision

- `pcl replay` cuts every raw file into **arrivals**: one per enrollment year, one per ICD-10 fiscal
  year, and one per **CMS processing year** for inpatient claims (`NCH_WKLY_PROC_DT`, not the service
  date). Arrival files are byte-for-byte subsets of the raw file, each with a checksum sidecar.
- `pcl load` loads arrivals oldest first, each behind its contract. Bronze is partitioned by arrival and
  by content hash (`arrival=2019/sha256=<prefix>/`). The same content is never loaded twice, and a
  re-delivery with new content becomes a new version beside the old one.
- Staging reads the **newest version of each arrival**. Across arrivals, a later arrival supersedes an
  earlier one for the same claim line.
- A contract failure stops the run. Later arrivals of that source do not load over the gap, and dbt
  does not run.

## Why

- Processing date is when a payer actually receives a claim, so the arrivals carry realistic run-out.
- Content-hash versioning makes loads idempotent and corrections auditable without ever mutating bronze.
- Replacing a whole arrival on correction (instead of upserting rows) also removes rows the
  correction dropped.

## Consequences

- Re-delivering an *older* version of an arrival is skipped, because its content is already in bronze,
  so it does not become the newest version again. Reverting a correction needs a new file. This is
  acceptable for a demo and called out here.
- In this source CMS processes every claim within two weeks, so run-out is short (651 of 58,066 lines
  cross a year boundary). The mechanism is the point; the numbers are the source's.
