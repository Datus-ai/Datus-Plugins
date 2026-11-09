---
name: tableau-setup
description: Configure a Tableau Server profile with PAT or password authentication and verify read access
requires_mutable_config: true
---

# Tableau Setup

Use this skill to configure an installed Tableau plugin or add an environment.
If the current deployment cannot edit its configuration, refer the user to the
administrator; do not suggest editing a server file from a read-only deployment.

Collect the server URL and the authentication choice. `auth_type` is one of
`pat` or `password`; configure exactly one credential pair. Prefer PAT for
automation; Tableau Cloud and MFA-enabled servers accept only PAT. Ask which
environment variables hold secrets; the user supplies them outside chat. Never
ask the user to paste secret values.

The site is optional at setup time. A site content URL is the segment shown in
Tableau URLs (`/#/site/<content_url>/`), not a display name or LUID; an empty
string is the default site. If the user does not know it, leave
`site_content_url` unset, save the profile, then run
`datus tableau --profile prod sites list` and present the returned sites
(`name`, `content_url`, `default`) so the user can choose. Save the chosen
value into the profile, or let the user pass `--site CONTENT_URL` per command
to switch between sites without editing the profile. Listing sites requires a
Server Administrator account; if the command reports that limit, ask the user
or their Tableau admin for the content URL instead of guessing.

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
        site_content_url: ""   # omit the key to choose later with `sites list`
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

Verify with `datus tableau --profile prod status` (its `site_configured` field
shows whether a site is set), followed by
`datus tableau --profile prod dashboards list`. Metadata API must already be
enabled and permissions granted. Report a closed Metadata endpoint or denied
download explicitly; do not automatically change Server administration settings.

PAT/password fields are secrets and must be environment-variable references.
Only the server URL, site, authentication type and API version belong in prompt
awareness. Installation alone does not establish live Server compatibility.
