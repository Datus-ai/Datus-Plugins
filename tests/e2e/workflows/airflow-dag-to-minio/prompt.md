Create `dags/e2e_{{RUN_ID}}.py`, upload it to the configured Airflow DAG root
with `datus s3 sync` (the Airflow plugin must not transfer the file), verify the
DAG appears through `datus airflow dags list`, trigger it, and wait for
completion. The DAG must write exactly ten deterministic JSON rows to the
configured MinIO result location. Save the final DAG run id and state in
`results/airflow-run.json`.
