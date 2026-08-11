The seeded FlinkDeployment `recovery-{{RUN_ID}}` is failing. Diagnose it using
`datus k8s` events, status, and logs, then produce a minimal corrected manifest
under `recovery/` and apply it. Record the observed root cause and evidence in
`recovery/diagnosis.md`. The corrected bounded job must write 100 rows to the
configured Paimon table.
