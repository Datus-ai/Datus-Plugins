---
name: build-datus-plugin-e2e
description: Build and run deterministic, LLM-driven end-to-end workflows for Datus plugins using the repository's pytest harness. Use when creating plugin E2E coverage, testing a plugin through a pinned datus-agent branch, provisioning isolated minikube/MinIO/Flink fixtures, defining programmatic pass/fail oracles, or diagnosing an E2E harness run. Invoke directly from Claude Code or Codex; do not create a standalone test CLI.
---

# Build Datus Plugin E2E

Create reproducible workflows under `tests/e2e/` and execute them through pytest. Let Datus use the LLM to perform the task, but let only independent programmatic oracles decide correctness.

## Guardrails

- Work from the Datus-Plugins repository root. Reuse `tests/e2e/harness`; do not add a wrapper CLI or console script.
- Keep normal pytest offline. Provision minikube and call an LLM only when the invocation explicitly requests a live run; live execution requires `--run-live`.
- Pin environment images/charts in `environment.lock.yml`. Resolve the requested datus-agent ref to a full commit SHA; the harness records that SHA.
- Pack the current plugin checkout with the tested agent's `datus plugin pack --with-deps`, then install that exact zip through the run-scoped agent config.
- Scope every run to its own minikube profile, namespace, workspace, Datus home, session scope, kubeconfig, bucket prefix, and artifact directory.
- Never treat an LLM statement, generated report, exit code alone, or log text alone as proof of success. The oracle must inspect the real external state independently of the plugin under test.
- Never weaken the target, oracle, or efficiency budget merely to make a failure pass.

## Build the workflow

1. Inspect the target plugin's manifest, commands, permissions, bundled skills, unit tests, and configuration schema. Identify support plugins needed to observe or deploy the result.
2. State one deterministic target before editing. Define exact inputs, final state, and failure conditions. Prefer bounded data, fixed IDs/ranges, exact schemas, exact counts/checksums, and named Kubernetes resources.
3. Choose the closest checked-in example:
   - `flink2paimon-datagen`: runnable Flink Operator + Paimon golden workflow.
   - `s3-minio-roundtrip`: runnable object checksum smoke workflow.
   - `airflow-dag-to-minio`, `flink-paimon-recovery`, `kafka-flink-paimon-upsert`, and `k8s-namespace-guard`: reference designs whose extra fixtures/oracles must be implemented before removing the `reference` tag.
4. Add `tests/e2e/workflows/<name>/workflow.yml`, `prompt.md`, and `environment.lock.yml`; add deterministic seed/golden files only when required.
5. Use `apiVersion: datus.ai/v1alpha1` and `kind: PluginE2EWorkflow`. Keep all paths relative and inside the workflow/workspace. Declare:
   - target and support plugin distribution/path/name/profile;
   - prompt timeout and pinned environment components;
   - every generated output pattern;
   - only the `datus <plugin>` command prefixes the agent needs;
   - at least one deterministic oracle;
   - explicit tool-call, LLM-turn, token, failed-call, expected-command, and forbidden-command budgets when applicable;
   - namespace and bucket-prefix cleanup policy.
6. Reuse an implemented oracle from `tests/e2e/harness/oracles.py`: `files`, `kubernetes_resource`, `minio_object`, or `flink_paimon`. Add a trusted harness oracle when the external state cannot be proven by these. Do not implement an oracle by calling the plugin being tested.
7. Add or update offline contract/unit tests for every schema, parser, artifact, or oracle behavior changed.

## Validate offline

Run these before any live test:

```bash
uv run --group e2e pytest tests/e2e/test_workflow_contracts.py tests/e2e/test_harness_unit.py -q
python3 -m compileall -q tests/e2e
```

Also run the target plugin's existing unit and manifest-contract tests. Fix offline failures before provisioning infrastructure.

## Run live

Create an ephemeral YAML file outside source control with this contract:

```yaml
agent:
  repo: https://github.com/Datus-ai/Datus-agent.git
  ref: <branch-tag-or-full-sha>
  config: <absolute-path-to-existing-agent.yml>
pluginRoot: <absolute-path-to-Datus-Plugins>
modelTarget: <optional-model-target>
repeats: 1
keepSuite: false
artifactsRoot: <absolute-or-config-relative-artifact-directory>
cacheRoot: <absolute-or-config-relative-agent-cache-directory>
```

Do not commit this file because the referenced agent config can contain credential references. Run exactly through pytest:

```bash
uv run --group e2e pytest tests/e2e/test_workflows.py \
  --run-live --workflow <name> --run-config <run-config.yml> -q -s
```

The harness performs environment initialization, agent installation/configuration, plugin packing/install from `zip:`, `datus -p`, artifact capture, session export, deterministic oracles, process scoring, and cleanup.

## Interpret results

Read each run directory in this order:

1. `summary.json`: `PASS`, `PRODUCT_FAIL`, or `HARNESS_FAIL`; pinned agent SHA; quality result.
2. `oracle.json`: authoritative correctness verdict and observed evidence.
3. `process.json`: tool sequence, duplicate commands, unexpected failures, LLM turns, tokens, and budget violations.
4. `session/session.jsonl`: redacted conversation/tool details; prefer this over the raw copied session database.
5. `generated.patch`, `workspace-manifest.json`, `generated-files/`, and command/environment logs.

Classify a failed environment/install/parser/cleanup as `HARNESS_FAIL`. Classify a completed run that misses the external target as `PRODUCT_FAIL`. A run can be correct but fail the quality budget; report both dimensions without allowing process analysis to override the oracle.

## Report

Return the deterministic target, files added/changed, agent SHA, plugin bundle hashes, exact test commands, correctness evidence, process-budget result, artifact path, and any live prerequisites not exercised. If the plugin needs iterative improvement, hand the artifacts to `$optimize-datus-plugin`.
