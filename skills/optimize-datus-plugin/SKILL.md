---
name: optimize-datus-plugin
description: Analyze Datus plugin E2E run artifacts and iteratively improve the current plugin until deterministic correctness and process-efficiency gates pass. Use when a datus-agent session failed or was slow/wasteful, when reviewing generated files and session/tool traces, when automating a plugin development optimization loop, or when comparing repeated workflow runs. Invoke directly from Claude Code or Codex; no separate optimization CLI is required.
---

# Optimize Datus Plugin

Use checked-in workflow contracts and run artifacts to make evidence-driven plugin changes. Preserve the independent oracle as the correctness authority and use LLM/session analysis only to explain behavior and improve efficiency.

## Inputs and safety

Require the repository path plus either a run directory/`summary.json` or a workflow name and ephemeral run-config path. If only the workflow is given, establish a baseline run using the command in `$build-datus-plugin-e2e`.

- Inspect `git status` and preserve unrelated user changes.
- Prefer `session/session.jsonl`; it is redacted. Do not quote secrets from the raw session database, agent config, environment, or logs.
- Modify the plugin and its tests by default when this skill is explicitly invoked. Do not modify external production resources.
- Do not edit the oracle, prompt target, fixture, or budget to conceal a plugin regression. Change a workflow contract only when evidence proves the contract itself is incorrect, and report that separately.
- Keep each iteration small enough to attribute its effect to one hypothesis.

## Diagnose the baseline

Read artifacts in this order:

1. `summary.json` for classification, run id, agent SHA, installed bundle hashes, exit code, and quality gates.
2. `oracle.json` for the authoritative failed assertion and observed external state.
3. `process.json` for tool sequence/counts, repeated commands, unexpected failures, model turns, tokens, and missing/forbidden commands.
4. `generated.patch`, `workspace-manifest.json`, and `generated-files/` for what Datus produced.
5. `session/session.jsonl` for decisions, plugin guidance used, tool arguments/results, recovery loops, and where the agent became confused.
6. Relevant pack/install/Datus/environment logs for details not preserved in normalized artifacts.

Create a concise diagnosis with:

- correctness: passed/failed and exact oracle evidence;
- execution: first causal failure, not merely the final symptom;
- guidance: missing or misleading manifest commands, prompt template, bundled skill, config schema, permissions, output/error messages, or CLI behavior;
- efficiency: unnecessary discovery, retries, duplicate calls, overly large outputs, avoidable LLM turns, and token hotspots;
- classification: harness defect, plugin defect, agent defect, flaky infrastructure, or invalid test contract, with evidence.

If the failure is `HARNESS_FAIL`, repair only a reproducible harness/environment defect or stop with the missing prerequisite. Do not compensate by changing plugin behavior. If it is `PRODUCT_FAIL`, trace the oracle mismatch back through generated output, session decisions, and plugin behavior.

## Iterate

For each iteration:

1. Record one falsifiable hypothesis and the artifact evidence supporting it.
2. Apply the smallest plugin change that addresses the cause. Typical high-leverage surfaces are:
   - command catalogue descriptions and argument hints;
   - system-prompt context and bundled skill instructions;
   - permission patterns required for legitimate automatic execution;
   - stable structured output, actionable errors, validation, idempotency, and polling/wait behavior;
   - config schema fields needed by the prompt and safe handling of secret fields.
3. Add or update unit/contract tests that reproduce the plugin defect without an LLM.
4. Run the target plugin's tests and the offline E2E harness tests.
5. Repack/reinstall through a fresh workflow attempt; never reuse an old bundle as evidence for new code.
6. Compare the new `oracle.json` and `process.json` with the baseline. Keep a change only when evidence improves correctness or efficiency without regression.

Use the live command directly—do not introduce an optimizer CLI:

```bash
uv run --group e2e pytest tests/e2e/test_workflows.py \
  --run-live --workflow <name> --run-config <run-config.yml> -q -s
```

When validating stability, set `repeats` in the ephemeral run config to at least 3. The loop succeeds only when every repeat has `status: PASS`, `quality_passed: true`, deterministic oracles pass, and plugin/offline tests pass. One lucky run is not convergence.

## Efficiency review

Treat correctness as a hard gate. After it passes, reduce process waste in this order:

1. Remove misleading guidance and make the intended plugin command discoverable.
2. Make errors specific enough to avoid blind retries.
3. Replace repeated status polling or large unfiltered reads with bounded wait/get operations.
4. Reduce redundant skill/prompt text and tool output without removing necessary safety context.
5. Tighten efficiency budgets only after repeated evidence establishes a stable lower baseline.

Never optimize by bypassing the plugin with `kubectl`, direct SDKs, `curl`, or another client when the workflow requires plugin use.

## Stop and report

Stop when the repeated gates pass, a missing credential/tool/network permission requires the user, or evidence points to a datus-agent defect outside the authorized repository. Report:

- baseline versus final correctness and quality metrics;
- root cause and each retained plugin change;
- unit/offline/live commands and results;
- workflow, run ids, pinned agent SHA, and final bundle hashes;
- final artifact directories;
- unresolved failures or external changes that would be required.
