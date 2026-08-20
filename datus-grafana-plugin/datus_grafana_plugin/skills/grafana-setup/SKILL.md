---
name: grafana-setup
description: Configure a Grafana environment profile for the `datus grafana` plugin
requires_mutable_config: true
---

# Grafana Setup

If this deployment cannot edit agent configuration, stop and ask the administrator to configure it server-side.

## `agent.yml`

```yaml
agent:
  plugins:
    grafana:
      prod:
        default: true
        api_base_url: https://grafana.example.com
        auth_mode: token
        token: ${GRAFANA_SERVICE_ACCOUNT_TOKEN}
        # auth_mode: basic
        # username: analyst
        # password: ${GRAFANA_PASSWORD}
        org_id: "1"                    # optional
        api_mode: auto                 # auto, new, or legacy
        namespace: default
        verify_ssl: "true"
        timeout: "30"
        default_datasource_uid: metrics-prod  # optional
```

Ask for the endpoint, auth mode, optional organization, and the environment-variable name holding the token/password. Have the user export it and write only `${VAR}`, never a literal secret. Prefer a least-privilege service-account token. Verify with `datus grafana status health` and `datus grafana status whoami`.
