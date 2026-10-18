# infra/

Placeholder for the Azure infrastructure-as-code milestone (Terraform). Nothing here is provisioned yet;
milestone 1 runs entirely on a laptop with DuckDB so cloud cost stays at zero.

Planned scope (later milestone):

- ADLS Gen2 storage account with `landing/`, `bronze/`, `silver/`, `gold/` containers, mirroring the
  local `data/` layout so the ingest code only changes its root path.
- Remote Terraform state, a resource group per environment, and least-privilege managed identities.
- A compute target for the Spark milestone (Databricks or Synapse Spark), sized and torn down by Terraform.
