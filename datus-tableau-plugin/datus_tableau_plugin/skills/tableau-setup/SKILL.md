---
name: tableau-setup
description: Configure a Tableau Server profile with PAT or password authentication and verify read access
requires_mutable_config: true
---

# Tableau Setup

Use this skill to configure an installed Tableau plugin or add an environment.
If the current deployment cannot edit its configuration, refer the user to the
administrator; do not suggest editing a server file from a read-only deployment.

Collect the server URL, site content URL (empty for the default site), and
authentication choice. A site content URL is not a site display name or LUID.
Prefer PAT for automation. Ask which environment variables hold secrets; the
user supplies them outside chat. Never ask the user to paste secret values.

Write to the configuration file named by the Plugins section of the system
prompt, preserving unrelated profiles and marking only the intended profile as
default. Datus expands environment variables and supplies the resolved dict.

### agent.yml

```yaml
agent:
  plugins:
    tableau:
      prod:
        default: true
        server_url: https://tableau.example.test
        site_content_url: ""
        auth_type: pat
        token_name: datus-automation
        token_secret: ${TABLEAU_TOKEN_SECRET}
        api_version: auto
        verify_ssl: true
        timeout: 60
```

For password authentication replace `auth_type`, `token_name` and `token_secret`
with the following fields; never leave a mixed pair of credential modes:

```yaml
auth_type: password
username: ${TABLEAU_USERNAME}
password: ${TABLEAU_PASSWORD}
```

An optional `ca_bundle` points at a local CA bundle for private deployments. Keep
TLS verification enabled; configure the actual CA rather than disabling checks.
`api_version: auto` negotiates the supported REST version. A single command
reuses one authenticated session. Concurrent processes must not share one PAT:
another sign-in with that PAT can terminate the previous session.

Verify with `datus tableau --profile prod status`, followed by
`datus tableau --profile prod dashboards list`. Metadata API must already be
enabled and permissions granted. Report a closed Metadata endpoint or denied
download explicitly; do not automatically change Server administration settings.

PAT/password fields are secrets and must be environment-variable references.
Only the server URL, site, authentication type and API version belong in prompt
awareness. Installation alone does not establish live Server compatibility.
