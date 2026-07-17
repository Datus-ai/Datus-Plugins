---
name: statsig-setup
description: Configure an environment profile for the `datus statsig` plugin (Console API base URL, API key, version)
requires_mutable_config: true
---

# Statsig Setup

Use this skill when `datus statsig` is installed but has no configured
environment, or when the user wants to add another environment.

## Config structure

Profiles live under `agent.plugins.statsig.<profile>` in the config file named
by the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    statsig:
      prod:
        default: true                       # mark exactly one profile as default
        api_key: ${STATSIG_CONSOLE_API_KEY} # required, SECRET — env var reference, never a literal
        # optional (defaults shown):
        api_base_url: https://statsigapi.net # Console API root (plugin appends /console/v1)
        api_version: "20240601"              # STATSIG-API-VERSION header
        timeout: 30                          # request timeout in seconds
```

## Steps

1. Ask the user for:
   - The **Console API key** — created at `console.statsig.com/api_keys`
     (Project Settings → API Keys → *Console* API Key, not a Server/Client SDK
     key). Have the user export it as an environment variable, e.g.
     `export STATSIG_CONSOLE_API_KEY=...`, and write `${STATSIG_CONSOLE_API_KEY}`
     into the YAML — **never a literal secret**.
   - `api_base_url` only if they are not on the default `https://statsigapi.net`
     (e.g. a dedicated/enterprise host). Otherwise omit it.
2. Write the profile into the config file named in the `## Plugins` preamble;
   mark the first profile `default: true`.
3. Verify with a cheap read-only call: `datus statsig metrics list --limit 5`
   (exercises connectivity + auth). A `401` means the key is wrong or is not a
   Console API key; exit `3` means `api_key` is missing from the profile.

If this environment cannot edit the config file (API / web deployment), tell
the user to edit `agent.yml` on the server instead.

## Notes

- Only `api_key` is a secret — it is the one field that must be a `${VAR}`
  reference. `api_base_url` / `api_version` are non-secret and are the only
  fields surfaced in the agent's system prompt.
- Mutating commands are rate-limited (~100/10s, ~900/15min per project).
