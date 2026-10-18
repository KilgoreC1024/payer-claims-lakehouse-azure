# Payer Claims Lakehouse (Azure-ready, local-first)

Member-level payer claims modeling on the CMS synthetic Medicare claims: eligibility member-months,
inpatient claims, and (in later milestones) PMPM, utilization per 1,000, and 30-day readmissions.

Milestone 1 runs locally on DuckDB and dbt. Azure infrastructure (Terraform) and Spark come later.

Work in progress. See [docs/adr/0001](docs/adr/0001-data-source-cms-2023-synthetic-claims.md) for the
data-source decision.
