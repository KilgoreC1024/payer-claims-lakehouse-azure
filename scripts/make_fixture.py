"""Cut the committed test fixture from the full CMS files.

    python scripts/make_fixture.py --raw-dir data/raw --out tests/fixtures/raw

Reads the raw layout that `pcl fetch` produces and writes a small subset in the *same published formats*,
so the fixture exercises exactly the code paths the full data does:

  cms_synthetic/beneficiary_<year>.csv  every yearly row for the selected members (pipe-delimited, as published)
  cms_synthetic/inpatient.csv           every claim line for the selected members, in file order
  reference/icd10cm|icd10pcs/*_order_<fy>.txt
                                        the order-file rows for every code that appears on the selected
                                        claims, plus its ancestors and descendants (so "billable",
                                        "header", and "truncated" can all be told apart). Descriptions are
                                        removed: the pipeline only reads the code and the flag.

Members are chosen deterministically: first one member for each edge case the pipeline must handle,
then filler members ranked by a hash of BENE_ID (so the choice is stable and not cherry-picked).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

FILLER_MEMBERS = 14
MAX_LINES_PER_MEMBER = 40
DX_FIELDS = ["ADMTG_DGNS_CD", "PRNCPAL_DGNS_CD", "FST_DGNS_E_CD"] + [f"ICD_DGNS_CD{i}" for i in range(1, 26)]
DX_FIELDS += [f"ICD_DGNS_E_CD{i}" for i in range(1, 13)]
PX_FIELDS = [f"ICD_PRCDR_CD{i}" for i in range(1, 26)]


def parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def read_pipe(path: Path) -> tuple[str, list[str], list[dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").split("\n")
    header_line = lines[0]
    header = header_line.split("|")
    rows = []
    for line in lines[1:]:
        if line:
            rows.append({"_line": line, **dict(zip(header, line.split("|"), strict=True))})
    return header_line, header, rows


def stable_rank(bene_id: str) -> str:
    return hashlib.sha256(bene_id.encode()).hexdigest()


def select_members(
    claims: list[dict[str, str]], members_by_year: dict[str, list[dict[str, str]]]
) -> list[str]:
    lines_by_bene: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in claims:
        lines_by_bene[row["BENE_ID"]].append(row)
    small = {b for b, rows in lines_by_bene.items() if len(rows) <= MAX_LINES_PER_MEMBER}

    def first(predicate) -> str | None:
        candidates = sorted(
            (b for b in small if predicate(lines_by_bene[b])), key=lambda b: (len(lines_by_bene[b]), b)
        )
        return candidates[0] if candidates else None

    def headers(rows):  # one row per claim (line 1 carries the same header fields as every other line)
        return [r for r in rows if r["CLM_LINE_NUM"] == "1"]

    def has_readmission(rows) -> bool:
        stays = sorted(
            (
                parse_date(r["CLM_ADMSN_DT"]) or parse_date(r["CLM_FROM_DT"]),
                parse_date(r["NCH_BENE_DSCHRG_DT"]),
            )
            for r in headers(rows)
        )
        return any(
            b[0] and a[1] and 0 <= (b[0] - a[1]).days <= 30 for a, b in zip(stays, stays[1:], strict=False)
        )

    ptc_members = {r["BENE_ID"] for r in members_by_year["2019"] if r["PTC_CNTRCT_ID_06"].strip()}
    ffs_members = {r["BENE_ID"] for r in members_by_year["2019"] if not r["PTC_CNTRCT_ID_06"].strip()}
    year_of_first_file = {}
    for year in sorted(members_by_year):
        for r in members_by_year[year]:
            year_of_first_file.setdefault(r["BENE_ID"], year)

    cases = {
        "injury code missing its 7th character": lambda rows: any(
            re.fullmatch(r"[ST]\w{3,5}", r["PRNCPAL_DGNS_CD"].strip())
            and r["PRNCPAL_DGNS_CD"].strip().endswith("X")
            for r in rows
        ),
        "December stay processed in January": lambda rows: any(
            r["CLM_THRU_DT"][-4:] != r["NCH_WKLY_PROC_DT"][-4:] for r in rows
        ),
        "claim with five or more lines": lambda rows: any(int(r["CLM_LINE_NUM"]) >= 5 for r in rows),
        "readmission within 30 days": has_readmission,
        "ICD-10 code on a claim before 2015-10-01": lambda rows: any(
            (parse_date(r["CLM_THRU_DT"]) or date.max) < date(2015, 10, 1) for r in rows
        ),
        "HCPCS field holding a modifier": lambda rows: any(
            r["HCPCS_CD"].strip() in {"QS", "Q3", "I3C"} for r in rows
        ),
        "valid 7-character ICD-10-PCS procedure": lambda rows: any(
            re.fullmatch(r"[0-9A-HJ-NP-Z]{7}", r["ICD_PRCDR_CD1"].strip()) for r in rows
        ),
        "diagnosis code in a procedure field": lambda rows: any(
            re.fullmatch(r"[A-Z][0-9]{2}[0-9A-Z]{0,4}", r["ICD_PRCDR_CD1"].strip())
            and not re.fullmatch(r"[0-9A-HJ-NP-Z]{7}", r["ICD_PRCDR_CD1"].strip())
            for r in rows
        ),
        "claim without a DRG": lambda rows: any(not r["CLM_DRG_CD"].strip() for r in rows),
        "Part C contract member with FFS claims": lambda rows: rows[0]["BENE_ID"] in ptc_members,
        "FFS-only member": lambda rows: rows[0]["BENE_ID"] in ffs_members,
        "member first enrolled after 2015": lambda rows: (
            year_of_first_file.get(rows[0]["BENE_ID"], "0") >= "2018"
        ),
    }
    chosen: list[str] = []
    for name, predicate in cases.items():
        bene = first(lambda rows, p=predicate: rows[0]["BENE_ID"] not in chosen and p(rows))
        if bene is None:
            raise SystemExit(f"no member found for edge case: {name}")
        chosen.append(bene)
        print(f"  {name:45} -> {bene} ({len(lines_by_bene[bene])} claim lines)")

    # Members without inpatient claims (most members have none) and one with an invalid dual status code.
    all_members = sorted({r["BENE_ID"] for rows in members_by_year.values() for r in rows}, key=stable_rank)
    bad_dual = sorted(
        {r["BENE_ID"] for rows in members_by_year.values() for r in rows if r["DUAL_STUS_CD_01"] == "AA"}
    )
    for bene in bad_dual[:1]:
        chosen.append(bene)
        print(
            f"  {'dual status code AA (not a CCW value)':45} -> {bene} ({len(lines_by_bene.get(bene, []))} claim lines)"
        )
    no_claims = [b for b in all_members if b not in lines_by_bene and b not in chosen][:4]
    chosen += no_claims
    print(f"  {'members without inpatient claims':45} -> {len(no_claims)}")
    filler = [b for b in sorted(small, key=stable_rank) if b not in chosen][:FILLER_MEMBERS]
    chosen += filler
    print(f"  {'filler members (hash-ranked)':45} -> {len(filler)}")
    return chosen


def code_subset(order_text: str, codes: set[str]) -> list[str]:
    """Order-file rows for each code's ancestors and descendants, with descriptions removed."""
    out = []
    for line in order_text.splitlines():
        if not line.strip():
            continue
        code = line[6:13].strip()
        if any(c.startswith(code) or code.startswith(c) for c in codes):
            out.append(f"{line[:5]} {code:<7} {line[14]} ")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    cms = args.raw_dir / "cms_synthetic"
    claim_header, _, claims = read_pipe(cms / "inpatient.csv")
    members_by_year = {}
    member_headers = {}
    for path in sorted(cms.glob("beneficiary_*.csv")):
        year = path.stem.split("_")[1]
        member_headers[year], _, members_by_year[year] = read_pipe(path)

    print("selecting members:")
    chosen = set(select_members(claims, members_by_year))

    out = args.out / "cms_synthetic"
    out.mkdir(parents=True, exist_ok=True)
    kept = [r for r in claims if r["BENE_ID"] in chosen]
    (out / "inpatient.csv").write_text(
        "\n".join([claim_header, *(r["_line"] for r in kept)]) + "\n", encoding="utf-8", newline="\n"
    )
    for year, rows in members_by_year.items():
        lines = [r["_line"] for r in rows if r["BENE_ID"] in chosen]
        (out / f"beneficiary_{year}.csv").write_text(
            "\n".join([member_headers[year], *lines]) + "\n", encoding="utf-8", newline="\n"
        )
    print(
        f"wrote {len(kept)} claim lines ({len({r['CLM_ID'] for r in kept})} claims) for {len(chosen)} members"
    )

    codes = {r[f].strip() for r in kept for f in DX_FIELDS + PX_FIELDS if r.get(f, "").strip()}
    pattern = re.compile(r"icd10(cm|pcs)_order_(\d{4})\.txt$")
    for kind in ("icd10cm", "icd10pcs"):
        folder = args.out / "reference" / kind
        folder.mkdir(parents=True, exist_ok=True)
        for archive_path in sorted((args.raw_dir / "reference" / kind).glob("*.zip")):
            with zipfile.ZipFile(archive_path) as archive:
                [member] = [n for n in archive.namelist() if pattern.search(n)]
                text = archive.read(member).decode("ascii")
            fiscal_year = pattern.search(member).group(2)
            rows = code_subset(text, codes)
            (folder / f"{kind}_order_{fiscal_year}.txt").write_text(
                "\n".join(rows) + "\n", encoding="ascii", newline="\n"
            )
            print(f"  {kind} FY{fiscal_year}: {len(rows)} rows")


if __name__ == "__main__":
    main()
