# orchestration/

Placeholder. In milestone 1 the pipeline is driven by the `pcl` CLI (`pcl run`), which replays arrivals
year by year and then runs dbt. A scheduler (Airflow, matching the sibling platform project, or Azure Data
Factory once the Azure milestone lands) will call the same CLI entry points.
