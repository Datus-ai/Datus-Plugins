---
name: superset-setup
description: Configure an Apache Superset environment profile for the `datus superset` plugin
requires_mutable_config: true
---

# Superset Setup

Use when the plugin is unconfigured or another environment is needed. If this deployment cannot edit agent configuration, stop and ask the administrator to edit it server-side.

## `agent.yml`

```yaml
agent:
  plugins:
    superset:
      prod:
        default: true
        api_base_url: https://superset.example.com
        auth_mode: login          # login or token
        username: analyst         # login auth
        password: ${SUPERSET_PASSWORD}
        # access_token: ${SUPERSET_ACCESS_TOKEN}  # token auth instead
        provider: db
        verify_ssl: "true"
        timeout: "30"
```

Ask for the endpoint, auth mode, non-secret username/provider, and environment-variable name holding the password or token. Have the user export that variable. Write only `${VAR}` to YAML, never a literal secret. Mark only the first profile `default: true`.

Verify with `datus superset status health`, then `datus superset dashboards list` to confirm the credentials themselves work.
