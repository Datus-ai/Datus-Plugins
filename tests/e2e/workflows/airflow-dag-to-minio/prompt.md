Create `dags/e2e_{{RUN_ID}}.py`, deploy it with the Airflow plugin, trigger it,
and wait for completion. The DAG must write exactly ten deterministic JSON rows
to the configured MinIO result location. Save the final DAG run id and state in
`results/airflow-run.json`.
