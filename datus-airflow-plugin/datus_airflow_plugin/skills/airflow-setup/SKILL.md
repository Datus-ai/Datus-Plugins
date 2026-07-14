---
name: airflow-setup
description: Configure an environment profile for the `datus airflow` plugin (API endpoint, credentials, optional S3/dags-folder deployment target)
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
        api_base_url: https://airflow.example.com   # required — server root, without /api/v2

        # auth — EITHER a static JWT token:
        token: ${AIRFLOW_API_TOKEN}          # secret — env var reference, never a literal
        # OR username + password (exchanged for a JWT at POST /auth/token):
        username: admin
        password: ${AIRFLOW_PASSWORD}        # secret — env var reference, never a literal

        # optional:
        verify_ssl: true                     # false or a CA bundle path for self-signed TLS
        timeout: 30                          # request timeout in seconds
        dags_folder: s3://my-bucket/dags/    # default target for `dags deploy`
                                             # (or a mounted path like /opt/airflow/dags)
        s3:                                  # only for s3:// dags_folder, all optional
          region: us-east-1
          profile: my-aws-profile            # named AWS profile
          endpoint_url: http://minio:9000    # for MinIO/custom endpoints
          access_key_id: ${AWS_ACCESS_KEY_ID}         # secret — env var reference
          secret_access_key: ${AWS_SECRET_ACCESS_KEY} # secret — env var reference
          role_arn: arn:aws:iam::123456789012:role/dags-deployer  # assume this IAM role
          role_session_name: datus-deploy    # optional, default datus-airflow-plugin
          external_id: team-a                # optional, if the role trust policy requires it
```

## Steps

1. Ask the user for:
   - `api_base_url` — the Airflow web server root (Airflow 3.x; the plugin
     calls `<api_base_url>/api/v2/...`).
   - Auth method: a ready-made API token, **or** username + password. For the
     secret, have the user export an environment variable (e.g.
     `export AIRFLOW_PASSWORD=...`) and write `${VAR}` into the YAML — never a
     literal secret.
   - Whether they deploy DAGs through this plugin; if yes, the `dags_folder`
     target (`s3://bucket/prefix/` or a local/mounted path) and any S3
     specifics (region, named profile, custom endpoint). boto3 ships with the
     plugin, so S3 deployment works out of the box.
2. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
3. Verify with a cheap read-only call: `datus airflow version` (checks
   connectivity + auth), then `datus airflow dags list --limit 5`.
4. If deployment is configured, optionally verify with
   `datus airflow dags deploy <some-dag>.py --dry-run`.

If this environment cannot edit the config file (API / web deployment), tell
the user to edit `agent.yml` on the server instead.

## Troubleshooting

- `login failed at .../auth/token` — username/password wrong, or the server's
  auth manager does not expose `POST /auth/token` (set `auth_token_url` if it
  lives elsewhere).
- `TLS verification failed` — set `verify_ssl` to the CA bundle path, or
  `false` as a last resort.
- 403 on `config` commands — server needs `AIRFLOW__API__EXPOSE_CONFIG=True`.
- 403/error on `connections test` — server needs
  `AIRFLOW__CORE__TEST_CONNECTION=Enabled`.
