# Datus plugin E2E harness

This pytest-owned harness runs a pinned datus-agent against plugin code in the
current checkout. It creates an isolated environment, packs each plugin with
`datus plugin pack --with-deps`, installs the exact zip into a run-scoped Datus
home, invokes `datus -p`, captures the session and generated files, and evaluates
independent deterministic oracles.

Claude Code and Codex should use the bundled `build-datus-plugin-e2e` and
`optimize-datus-plugin` skills. There is intentionally no separate test CLI.

## Offline validation

```bash
uv run --group e2e pytest tests/e2e/test_workflow_contracts.py tests/e2e/test_harness_unit.py -q
```

Normal pytest never provisions infrastructure or calls an LLM.

## Live run

Copy [`run-config.example.yml`](run-config.example.yml) to a temporary ignored
location, fill in an existing agent config and desired agent branch/tag/SHA,
then run:

```bash
uv run --group e2e pytest tests/e2e/test_workflows.py \
  --run-live --workflow flink2paimon-datagen \
  --run-config /tmp/datus-e2e-run.yml -q -s
```

Runnable workflows currently include `flink2paimon-datagen` and
`s3-minio-roundtrip`. Workflows tagged `reference` document additional targets
but are skipped until their environment fixture and oracle are implemented.

Each attempt writes `summary.json`, `oracle.json`, `process.json`, redacted
`session/session.jsonl`, the generated file manifest and patch, bundle hashes,
and subprocess logs under the configured artifact root. `oracle.json` owns the
correctness verdict; `process.json` records efficiency separately.

`process.json` preserves the provider-reported cumulative `total_tokens` and
also records cache-aware `effective_tokens` (`input_tokens -
cached_input_tokens + output_tokens`). Workflow `maxTokens` gates the latter;
`maxLlmTurns` independently limits repeated model/tool round trips. This avoids
counting the same cached prompt prefix as fresh work on every tool response.
