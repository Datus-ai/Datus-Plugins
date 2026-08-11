---
name: eks-setup
description: Configure an Amazon EKS environment profile for the datus eks plugin
requires_mutable_config: true
---

# Amazon EKS Setup

Use this skill when `datus eks` has no configured environment or the user wants
to add another EKS cluster.

Profiles live under `agent.plugins.eks.<profile>` in the agent config:

```yaml
agent:
  plugins:
    eks:
      datus-dev:
        default: true                 # mark the first profile as default
        cluster: datus-dev-eks-cluster # required
        region: us-east-1              # recommended
        profile: engineering           # optional ~/.aws named profile
        role_arn: arn:aws:iam::123456789012:role/datus-dev-eks-operator # optional
        role_session_name: datus-eks   # optional
        external_id: ${AWS_EXTERNAL_ID} # optional secret
        access_key_id: ${AWS_ACCESS_KEY_ID}         # optional secret
        secret_access_key: ${AWS_SECRET_ACCESS_KEY} # optional secret
        session_token: ${AWS_SESSION_TOKEN}         # optional secret
        timeout: "60"
        max_attempts: "3"
```

1. Ask for the profile name, EKS cluster name, region, and authentication mode:
   standard AWS credential chain, named AWS profile, static temporary keys, or
   AssumeRole.
2. For every credential or external ID, have the user export an environment
   variable and write only `${VAR}` into YAML. Never write literal secrets.
3. Add the profile to the config named by the Plugins prompt preamble and mark
   the first profile `default: true`.
4. Verify without exposing credentials:

```bash
datus eks --profile datus-dev auth whoami -o json
datus eks --profile datus-dev clusters describe -o json
```

To pair it with Kubernetes, create a same-named k8s profile containing
`provider: eks`; `provider_profile` can then be omitted.

If this environment cannot edit the agent config, tell the user to ask the
deployment administrator to make the change.
