# Test fixture: a small, real subset of the CMS files

`raw/` holds a subset of the published files in their published formats, laid out exactly like
`<data-dir>/raw/` after `pcl fetch`. That way `pcl run --profile fixture` and the tests exercise the same
code paths as a full run, offline.

| Path | Contents |
|---|---|
| `raw/cms_synthetic/beneficiary_<year>.csv` | Every yearly row (2015-2025) for 31 members |
| `raw/cms_synthetic/inpatient.csv` | Every claim line for those members: 66 lines, 39 claims, processed 2015-2023 |
| `raw/reference/icd10cm/icd10cm_order_<fy>.txt` | ICD-10-CM order-file rows (FY2016-FY2023) for every code on those claims, plus ancestors and descendants |
| `raw/reference/icd10pcs/icd10pcs_order_<fy>.txt` | The same for ICD-10-PCS |

The order-file subsets keep the CMS fixed-width layout (order number, code, flag). Descriptions are
removed because the pipeline reads only the code and its flag. Line endings are LF.

## How it was made

```bash
python scripts/make_fixture.py --raw-dir data/raw --out tests/fixtures/raw
```

The script picks one member for each edge case the pipeline must handle. Each pick is the qualifying
member with the fewest claim lines, ties broken by `BENE_ID`:

- an injury code missing its 7th character
- a December stay processed in January (it lands in the next year's arrival)
- a claim with five or more lines
- a readmission within 30 days
- an ICD-10 code on a claim before 2015-10-01
- a HCPCS field holding a modifier
- a valid ICD-10-PCS procedure
- a diagnosis code in a procedure field
- a claim with no DRG
- a Part C member with fee-for-service claims, and a fee-for-service-only member
- a member first enrolled after 2015
- a member with the invalid dual status code `AA`

It then adds four members with no inpatient claims and 14 filler members ranked by a SHA-256 hash of
`BENE_ID`, so the filler choice is stable and not cherry-picked.

## Source files (downloaded 2026-10-18)

CMS synthetic files come from the
[data.cms.gov collection](https://data.cms.gov/collection/synthetic-medicare-enrollment-fee-for-service-claims-and-prescription-drug-event).
The beneficiary files came from its `All Beneficiary Years.zip` and are byte-identical to the
individual yearly downloads. ICD-10 files come from the cms.gov ICD-10 pages; the URLs are in
[`config/sources.yml`](../../config/sources.yml). CMS states the synthetic files "can be released publicly without
restrictions".

| File | SHA-256 |
|---|---|
| `cms_synthetic/beneficiary_2015.csv` | `d89416e431c60ee8d2d3188738f43e596ef1fb9a7f87a222f408fc275b4707f0` |
| `cms_synthetic/beneficiary_2016.csv` | `b4460734ff7bf7a61f208174ac70d0b4b017612a4edb653841e71a138719b929` |
| `cms_synthetic/beneficiary_2017.csv` | `7342a58a1360df6f975a05fba9c907e9dc28a21ea85d557e9d2c7e60743100a9` |
| `cms_synthetic/beneficiary_2018.csv` | `e9f6e68cf43a185a340775fbc2c87a2593872ed5d8c8279f4511bdbec12803d2` |
| `cms_synthetic/beneficiary_2019.csv` | `182984d1580051a69af5d23b5c782798f5f5c8c1c3c1fe6b1e5c572664b0bc67` |
| `cms_synthetic/beneficiary_2020.csv` | `535c4ef5ade8426472a20422caac1e991667c1661519f31e1f44ec09d41ee093` |
| `cms_synthetic/beneficiary_2021.csv` | `de16777d592f0b346af83345f61af30806f0be0122a377c66c3ff6b17f4e5a6d` |
| `cms_synthetic/beneficiary_2022.csv` | `6cc91be7b779c92888fb10b8a593ca59818405091137e171ce3af4ebbd1d6132` |
| `cms_synthetic/beneficiary_2023.csv` | `9c723be45f6da91be334b9dacb8bf5a17d57238bcc60b38ca9744c4e0bd83fc4` |
| `cms_synthetic/beneficiary_2024.csv` | `7b32aaca2defa1b6d0ff5174a4aef12b8ebb41eeb3b4e062421c6af0120cab2a` |
| `cms_synthetic/beneficiary_2025.csv` | `1c6cda2a3ced893c70685b69926e2c6be210356fa703d4bb9bae3d180a8ad9fe` |
| `cms_synthetic/inpatient.csv` | `4085f4ee4519c9dcee8c124d127f9feb7062053a103a94f72d7a576a4b3031e6` |
| `reference/icd10cm/icd10cm_2016.zip` | `fb7f9b15834da6a74c4dd649df009cdef5bd0fae607feaa2977d2c23077556a2` |
| `reference/icd10cm/icd10cm_2017.zip` | `b406779f090a79debf39f828601da6c6d0c709f9ba317e299ff679f183207f5b` |
| `reference/icd10cm/icd10cm_2018.zip` | `d4954a3fa02e0bfbecb10b20864198dfa50862ded93f2d9d7313e9fe07ef0dfc` |
| `reference/icd10cm/icd10cm_2019.zip` | `73a4b41698bb9ae5e3e557edab4c76cf194faee3cf2dc594df25a9a343a70fa1` |
| `reference/icd10cm/icd10cm_2020.zip` | `2e53056c702a600a7e2abb2e199322fac2c8546f2583ccda95f5aeeb2f63c4c4` |
| `reference/icd10cm/icd10cm_2021.zip` | `479be4d5773fb2e8357c5edb8a56bbd48da4d2bca443a7c42527ad102e3b388c` |
| `reference/icd10cm/icd10cm_2022.zip` | `423206a194dd6db8384c3b39699ad3c1efa19718677fa81690557732a31bf560` |
| `reference/icd10cm/icd10cm_2023.zip` | `cc7158228f6de01aa08650cc3afe446973a879dd78d44f1fec7150d45cfe1e06` |
| `reference/icd10pcs/icd10pcs_2016.zip` | `9f3af070b4428b5d9496bc893e6cf31da6a65f95c07c54924588670789380179` |
| `reference/icd10pcs/icd10pcs_2017.zip` | `ad90bc25cb49d08adf5f67d5cc5cff4b033db810f306d7338703bc4bf32ba1d2` |
| `reference/icd10pcs/icd10pcs_2018.zip` | `210935ee3ebaa396a1ebfee22b0b010dbb38fda7cdea4754040ca49a040cd3a0` |
| `reference/icd10pcs/icd10pcs_2019.zip` | `9d51328f1b0176edeb2a90701c2e5d5fc984637b4480d83fc0a4cc8a569d5845` |
| `reference/icd10pcs/icd10pcs_2020.zip` | `55cc9555df3f95c1a4928c6389dda1646e477c1d9f1b33e4b740960c169db9de` |
| `reference/icd10pcs/icd10pcs_2021.zip` | `59c5ba3c6601a6f759b0110bfd9b4fdee8eb556462fcf661a823518ff90f2a93` |
| `reference/icd10pcs/icd10pcs_2022.zip` | `0a98d9bd017a04c9ec09d2e6f85924d6a5c54b091bcf7fa4a8ed8793a81244ca` |
| `reference/icd10pcs/icd10pcs_2023.zip` | `069062ca36cb8ea0b4803f4ed4668df653f07fd0843f5f28ef81b6a4dce2f653` |
