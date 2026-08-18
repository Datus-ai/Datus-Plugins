---
name: airflow-setup
description: Configure an environment profile for the `datus airflow` plugin (API endpoint, credentials, optional DAG deployment URI)
requires_mutable_config: true
---

# Airflow Setup

Use this skill when `datus airflow` is installed but has no configured
environment, or when the user wants to add another environment.

## Config structure

Profiles live under `agent.plugins.airflow.<profile>` in the config file named
by the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    airflow:
      prod:
        default: true                        # mark exactly one profile as default
        api_base_url: https://airflow.example.com/api/v1  # v1 suffix selects Airflow 2
        api_version: auto                           # auto | v1 | v2

        # auth — EITHER a static JWT token:
        token: ${AIRFLOW_API_TOKEN}          # secret — env var reference, never a literal
        # OR username + password (exchanged for a JWT at POST /auth/token):
        username: admin
        password: ${AIRFLOW_PASSWORD}        # secret — env var reference, never a literal

        # optional:
        verify_ssl: true                     # false or a CA bundle path for self-signed TLS
        timeout: 30                          # request timeout in seconds
        dags_folder: s3://my-bucket/dags/    # optional deployment URI used by
                                             # the airflow-dag-export skill

        # optional scope guardrails (see "Scoping a profile" below):
        dag_id_prefix: team_a_               # only team_a_* DAGs; comma-separate several
        allow_commands: dags,tasks,version   # only these top-level groups

```

## Steps

1. Ask the user for:
   - `api_base_url` — the Airflow web server root. An `/api/v1` suffix selects
     Airflow 2 with Basic Auth; `/api/v2` selects Airflow 3 with JWT. Without a
     suffix, `api_version` defaults to v2.
   - Auth method: a ready-made API token, **or** username + password. For the
     secret, have the user export an environment variable (e.g.
     `export AIRFLOW_PASSWORD=...`) and write `${VAR}` into the YAML — never a
     literal secret.
   - Optional `dags_folder` deployment URI. Configure credentials separately
     in the matching storage plugin (`s3`, `gcs`, or `adls`); the Airflow
     plugin never receives or uses object-storage credentials.
2. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
3. Verify with a cheap read-only call: `datus airflow version` (checks
   connectivity + auth), then `datus airflow dags list --limit 5`.
4. If deployment is configured, load the `airflow-dag-export` skill and verify
   that it resolves the URI scheme to the expected storage plugin. Do not
   upload a file merely to test configuration.

If this environment cannot edit the config file (API / web deployment), tell
the user to edit `agent.yml` on the server instead.

## Scoping a profile

Offer these when one environment should only serve one team or project:

- `dag_id_prefix` — commands taking a `dag_id` refuse ids outside the prefix
  before any request; DAG listings are filtered to it. `assets materialize` and
  `backfill pause|unpause|cancel` become unavailable (no `dag_id` to check).
- `allow_commands` — allowlist of top-level groups, e.g. `dags,tasks,version`.
  Group level only; `dags list` is a config error, write `dags`.

Both appear in the system prompt per environment, so the agent knows the
boundary without probing for it.

Be explicit with the user that this is a **guardrail against mistakes, not a
security boundary** — anyone who can edit `agent.yml` or reach the Airflow API
bypasses it. Real multi-tenancy needs server-side enforcement (DAG-level RBAC
via FabAuthManager, or Airflow 3.2+ `[core] multi_team`), plus a separate
Airflow user per profile so the server also limits what the token can do.
Note that `variables`, `connections` and `pools` are instance-wide and cannot
be prefix-scoped at all — leave them out of `allow_commands` if that matters.

## Troubleshooting

- `login failed at .../auth/token` — username/password wrong, or the server's
  auth manager does not expose `POST /auth/token` (set `auth_token_url` if it
  lives elsewhere).
- `TLS verification failed` — set `verify_ssl` to the CA bundle path, or
  `false` as a last resort.
- 403 on `config` commands — server needs `AIRFLOW__API__EXPOSE_CONFIG=True`.
- 403/error on `connections test` — server needs
  `AIRFLOW__CORE__TEST_CONNECTION=Enabled`.
